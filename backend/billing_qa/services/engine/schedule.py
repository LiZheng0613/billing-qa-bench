"""定时计划：下次触发时间、派发到期计划、后台轮询。

约定（与产品口径一致）：

- ``SCHEDULED``：一次性计划，到点后**当前任务**直接开跑，跑完即为普通历史任务；
- ``DAILY`` / ``WEEKLY``：周期计划，每次触发**克隆出一个新的批量任务**再启动，
  计划行本身只保留配置与下次触发时间，历史任务/指标/失败重跑互不干扰；
- 所有时间都是服务器本地时间；
- 服务停机错过的触发只补跑一次，不会回放每一次历史时刻（下次时间严格大于当前时间）。
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime

from sqlalchemy import select

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger
from billing_qa.core.schedule_time import compute_next_run
from billing_qa.db.base import now
from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import TaskScheduleType, TaskStatus
from billing_qa.models.task import BatchTask
from billing_qa.services.engine.scheduler import scheduler

logger = get_logger(__name__)

PERIODIC_TYPES = {TaskScheduleType.DAILY.value, TaskScheduleType.WEEKLY.value}
PLAN_TYPES = {TaskScheduleType.SCHEDULED.value, *PERIODIC_TYPES}
# 计划行本身的状态：等待下一次触发
IN_FLIGHT_STATUSES = {
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
    TaskStatus.SCHEDULED.value,
}


def generate_task_code() -> str:
    return f"BATCH{datetime.now():%Y%m%d%H%M%S}{random.randint(100, 999)}"


def initial_next_run(
    *,
    schedule_type: str,
    scheduled_at: datetime | None = None,
    schedule_time: str | None = None,
    schedule_weekdays: list[int] | None = None,
    base: datetime | None = None,
) -> datetime | None:
    """创建计划时的首次触发时间；立即执行返回 ``None``。"""
    if schedule_type == TaskScheduleType.SCHEDULED.value:
        return scheduled_at
    if schedule_type in PERIODIC_TYPES:
        return compute_next_run(
            schedule_type=schedule_type,
            schedule_time=schedule_time or "00:00",
            weekdays=schedule_weekdays,
            base=base,
        )
    return None


def clone_for_run(plan: BatchTask, *, current: datetime | None = None) -> BatchTask:
    """把周期计划克隆成一次独立执行的任务。"""
    moment = current or now()
    total = max(1, int(plan.repeat_rounds or 1))
    name = f"{plan.name} · {moment:%m-%d %H:%M}"
    if total > 1:
        name = f"{name} · 第 1/{total} 轮"
    return BatchTask(
        code=generate_task_code(),
        name=name,
        description=plan.description,
        status=TaskStatus.PENDING,
        control_state="RUNNING",
        environment_id=plan.environment_id,
        case_ids=list(plan.case_ids or []),
        total_cases=plan.total_cases,
        concurrency=plan.concurrency,
        qps_limit=plan.qps_limit,
        retry_on_env_error=plan.retry_on_env_error,
        max_env_retries=plan.max_env_retries,
        retry_backoff_seconds=plan.retry_backoff_seconds,
        schedule_type=TaskScheduleType.IMMEDIATE.value,
        schedule_active=False,
        schedule_source_id=plan.id,
        repeat_rounds=max(1, int(plan.repeat_rounds or 1)),
        round_index=1,
        created_by=plan.created_by,
    )


def clone_next_round(previous: BatchTask, *, plan_id: int, round_index: int) -> BatchTask:
    """把已完成的轮次克隆为下一轮，紧随其后串行执行。"""
    total = max(1, int(previous.repeat_rounds or 1))
    # 轮次名称始终基于原始任务名，避免「第 2/3 轮 · 第 3/3 轮」逐轮叠加
    base_name = re.sub(r" · 第 \d+/\d+ 轮$", "", previous.name)
    return BatchTask(
        code=generate_task_code(),
        name=f"{base_name} · 第 {round_index}/{total} 轮",
        description=previous.description,
        status=TaskStatus.PENDING,
        control_state="RUNNING",
        environment_id=previous.environment_id,
        case_ids=list(previous.case_ids or []),
        total_cases=previous.total_cases,
        concurrency=previous.concurrency,
        qps_limit=previous.qps_limit,
        retry_on_env_error=previous.retry_on_env_error,
        max_env_retries=previous.max_env_retries,
        retry_backoff_seconds=previous.retry_backoff_seconds,
        schedule_type=TaskScheduleType.IMMEDIATE.value,
        schedule_active=False,
        schedule_source_id=plan_id,
        repeat_rounds=total,
        round_index=round_index,
        round_prev_id=previous.id,
        created_by=previous.created_by,
    )


async def _latest_run_status(db, plan_id: int) -> str | None:
    row = (
        await db.execute(
            select(BatchTask.status)
            .where(BatchTask.schedule_source_id == plan_id)
            .order_by(BatchTask.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return str(row) if row else None


async def run_due_tasks(reference: datetime | None = None) -> list[int]:
    """派发所有到期的定时计划，返回本次启动的任务 id。"""
    current = reference or now()
    started: list[int] = []
    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(BatchTask)
                    .where(
                        BatchTask.schedule_type.in_(PLAN_TYPES),
                        BatchTask.schedule_active.is_(True),
                        BatchTask.next_run_at.is_not(None),
                        BatchTask.next_run_at <= current,
                    )
                    .order_by(BatchTask.next_run_at)
                )
            )
            .scalars()
            .all()
        )

        for plan in rows:
            if plan.schedule_type == TaskScheduleType.SCHEDULED.value:
                total = max(1, int(plan.repeat_rounds or 1))
                plan.next_run_at = None
                plan.last_run_at = current
                plan.schedule_active = False
                if total > 1:
                    # 连续多轮：计划行本身只保留配置，第一轮也克隆成独立执行
                    run = clone_for_run(plan, current=current)
                    db.add(run)
                    await db.flush()
                    plan.status = TaskStatus.COMPLETED
                    plan.control_state = "FINISHED"
                    await db.commit()
                    if await scheduler.start(run.id):
                        started.append(run.id)
                        logger.info(
                            "连续多轮计划已启动第 1/%d 轮 plan=%s run=%s",
                            total,
                            plan.id,
                            run.id,
                        )
                    continue
                # 一次性计划：到点直接用当前任务执行，之后不再触发
                plan.status = TaskStatus.PENDING
                await db.commit()
                if await scheduler.start(plan.id):
                    started.append(plan.id)
                    logger.info("定时任务已启动 task=%s(%s)", plan.id, plan.name)
                continue

            latest = await _latest_run_status(db, plan.id)
            if latest in IN_FLIGHT_STATUSES:
                # 上一轮还没跑完，跳过本次触发，避免周期任务堆叠
                plan.next_run_at = compute_next_run(
                    schedule_type=plan.schedule_type,
                    schedule_time=plan.schedule_time or "00:00",
                    weekdays=plan.schedule_weekdays,
                    base=current,
                )
                await db.commit()
                logger.warning(
                    "周期任务上一轮仍未结束，跳过本次触发 plan=%s status=%s next=%s",
                    plan.id,
                    latest,
                    plan.next_run_at,
                )
                continue

            run = clone_for_run(plan, current=current)
            db.add(run)
            await db.flush()
            plan.last_run_at = current
            plan.next_run_at = compute_next_run(
                schedule_type=plan.schedule_type,
                schedule_time=plan.schedule_time or "00:00",
                weekdays=plan.schedule_weekdays,
                base=current,
            )
            await db.commit()
            if await scheduler.start(run.id):
                started.append(run.id)
                logger.info(
                    "周期任务已生成并启动 plan=%s run=%s next=%s",
                    plan.id,
                    run.id,
                    plan.next_run_at,
                )
    return started


class ScheduleDispatcher:
    """后台轮询：到点即派发定时计划。"""

    def __init__(self, interval_seconds: float | None = None) -> None:
        self._interval = interval_seconds
        self._worker: asyncio.Task[None] | None = None

    @property
    def interval(self) -> float:
        return max(1.0, float(self._interval or settings.schedule_poll_interval_seconds))

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start(self) -> None:
        if self.running:
            return
        self._worker = asyncio.create_task(self._loop())
        logger.info("定时派发已启动，轮询间隔 %.1fs", self.interval)

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def tick(self, reference: datetime | None = None) -> list[int]:
        return await run_due_tasks(reference)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
                # 自愈：恢复中断/未启动的连续轮次链
                await scheduler.resume_round_chains()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("定时派发失败")
            await asyncio.sleep(self.interval)


schedule_dispatcher = ScheduleDispatcher()
