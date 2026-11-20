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
import uuid
from datetime import datetime

from sqlalchemy import select

from billing_qa.core.config import settings
from billing_qa.core.control_store import control_store
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
# 同一计划同一触发时刻的派发互斥窗口：覆盖「读计划 → 克隆入库 → 推进 next_run_at」
# 的整个事务过程即可，无需覆盖任务执行期（执行期由任务运行锁保护）。
PLAN_TRIGGER_LOCK_SECONDS = 60.0


def generate_task_code() -> str:
    return f"BATCH{datetime.now():%Y%m%d%H%M%S}{random.randint(100, 999)}"


_ROUND_SUFFIX_RE = re.compile(r" · 第 \d+/\d+ 轮$")


def round_task_name(name: str, round_index: int, total: int) -> str:
    """给轮次任务名追加统一的「第 k/N 轮」标识。

    - 总轮数为 1 时保持原名；
    - 已带轮次后缀的名称先剥离再补充，避免「第 2/3 轮 · 第 3/3 轮」逐轮叠加。
    """
    base = _ROUND_SUFFIX_RE.sub("", (name or "").strip()) or (name or "").strip()
    if total > 1:
        return f"{base} · 第 {round_index}/{total} 轮"
    return base


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
    name = round_task_name(f"{plan.name} · {moment:%m-%d %H:%M}", 1, total)
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
        retry_on_error=plan.retry_on_error,
        max_error_retries=plan.max_error_retries,
        retry_backoff_seconds=plan.retry_backoff_seconds,
        enable_self_check=plan.enable_self_check,
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
    return BatchTask(
        code=generate_task_code(),
        name=round_task_name(previous.name, round_index, total),
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
        retry_on_error=previous.retry_on_error,
        max_error_retries=previous.max_error_retries,
        retry_backoff_seconds=previous.retry_backoff_seconds,
        enable_self_check=previous.enable_self_check,
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


def _plan_trigger_lock_name(plan: BatchTask) -> str:
    """同一计划、同一触发时刻共用一把锁：多实例同时扫到到期计划时只有一个派发。"""
    moment = (
        plan.next_run_at.strftime("%Y%m%d%H%M%S") if plan.next_run_at else "immediate"
    )
    return f"{settings.redis_key_prefix}plan:{plan.id}:trigger:{moment}"


async def run_due_tasks(reference: datetime | None = None) -> list[int]:
    """派发所有到期的定时计划，返回本次启动的任务 id。

    多实例部署时，各实例的后台轮询会同时扫到同一条到期计划；派发前先按
    「计划 + 触发时刻」抢锁，抢不到的实例跳过本次派发，避免同一触发被克隆
    出多个重复批次（Redis 不可用时退化为进程内锁，仅保护单实例）。
    """
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
            lock_name = _plan_trigger_lock_name(plan)
            lock_token = uuid.uuid4().hex
            if not await control_store.acquire_lock(
                lock_name, lock_token, PLAN_TRIGGER_LOCK_SECONDS
            ):
                logger.info(
                    "计划触发锁被其他实例持有，跳过本次派发 plan=%s moment=%s",
                    plan.id,
                    plan.next_run_at,
                )
                continue
            try:
                await _dispatch_plan(db, plan, current, started)
            finally:
                await control_store.release_lock(lock_name, lock_token)
    return started


async def _dispatch_plan(
    db, plan: BatchTask, current: datetime, started: list[int]
) -> None:
    """派发单个到期计划（调用方已持有该触发的派发锁）。"""
    if plan.schedule_type == TaskScheduleType.SCHEDULED.value:
        total = max(1, int(plan.repeat_rounds or 1))
        plan.next_run_at = None
        plan.last_run_at = current
        # 连续多轮计划保留 schedule_active 表示轮次链继续；
        # 单轮计划触发后即收口，不再参与任何触发。
        plan.schedule_active = total > 1
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
            return
        # 一次性计划：到点直接用当前任务执行，之后不再触发
        plan.status = TaskStatus.PENDING
        await db.commit()
        if await scheduler.start(plan.id):
            started.append(plan.id)
            logger.info("定时任务已启动 task=%s(%s)", plan.id, plan.name)
        return

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
        return

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
                # 自愈：恢复派发后从未启动的计划批次（克隆入库与启动之间进程退出）
                await scheduler.resume_orphaned_plan_runs()
                # 自愈：接管重启后无人推进的任务（锁过期后由本实例续做）
                await scheduler.adopt_interrupted_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("定时派发失败")
            await asyncio.sleep(self.interval)


schedule_dispatcher = ScheduleDispatcher()
