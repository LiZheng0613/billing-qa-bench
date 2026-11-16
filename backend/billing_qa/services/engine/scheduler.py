"""批量任务调度器：并发控制、QPS 限流、终止、断点重跑。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.control_store import control_store
from billing_qa.core.event_bus import event_bus
from billing_qa.core.logging import get_logger
from billing_qa.core.rate_limiter import QpsLimiter
from billing_qa.db.base import now
from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import ExecutionSource, ExecutionStatus, TaskStatus
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.schemas.case import CaseExecutionItem
from billing_qa.schemas.task import TaskProgressEvent
from billing_qa.services.engine.error_policy import ERROR_KIND_ENV, retry_delay
from billing_qa.services.engine.executor import CaseExecutor
from billing_qa.services.engine.metrics import (
    apply_metrics_to_task,
    compute_task_metrics,
    latest_executions,
)
from billing_qa.services.revision.snapshot import build_snapshot

logger = get_logger(__name__)

RETRYABLE_STATUSES = {
    ExecutionStatus.FAIL,
    ExecutionStatus.ERROR,
    ExecutionStatus.MAX_TURNS_EXCEEDED,
    ExecutionStatus.REPETITION_ABORT,
    ExecutionStatus.ABORTED,
}


class BatchScheduler:
    def __init__(self) -> None:
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, task_id: int) -> asyncio.Lock:
        lock = self._locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[task_id] = lock
        return lock

    def is_running(self, task_id: int) -> bool:
        worker = self._workers.get(task_id)
        return worker is not None and not worker.done()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def start(self, task_id: int, *, retry_failed: bool = False) -> bool:
        async with self._lock(task_id):
            if self.is_running(task_id):
                return False
            worker = asyncio.create_task(self._run(task_id, retry_failed))
            self._workers[task_id] = worker
        return True

    async def abort(self, task_id: int) -> None:
        await control_store.set_state(task_id, "ABORTING")
        if not self.is_running(task_id):
            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is None:
                    return
                task.status = TaskStatus.ABORTED
                task.control_state = "ABORTED"
                task.finished_at = now()
                await db.commit()
                await self._close_running_executions(db, task_id)
                await db.commit()
                await self._publish_status(task)
        logger.info("任务终止指令已下发 task=%s", task_id)

    async def shutdown(self) -> None:
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    async def recover_interrupted_tasks(self) -> int:
        """服务重启后把中断的任务收口为已终止。

        进程退出后 worker 已不存在，任务不可能自行继续；这里直接标记为
        ABORTED，不留下无人推进的中间态。已完成的用例结果保留，可点击
        「失败重跑」从断点继续。
        """
        async with SessionLocal() as db:
            rows = await db.execute(
                select(BatchTask).where(
                    # "PAUSED" 仅用于清理历史数据：该状态已从产品中移除
                    BatchTask.status.in_([TaskStatus.RUNNING.value, "PAUSED"])
                )
            )
            tasks = list(rows.scalars().all())
            for task in tasks:
                task.status = TaskStatus.ABORTED
                task.control_state = "ABORTED"
                task.finished_at = task.finished_at or now()
                task.error_message = "服务重启导致任务中断，已自动终止，可点击失败重跑继续"
                await control_store.set_state(task.id, "ABORTED")
                await self._close_running_executions(
                    db, task.id, "服务重启导致任务中断"
                )
                # 收口后重算进度/通过率，避免任务显示「7/55 已完成」但执行记录已全部终止
                metrics = await compute_task_metrics(db, task)
                apply_metrics_to_task(task, metrics)
            await db.commit()
        if tasks:
            logger.warning("已把 %d 个中断任务标记为已终止", len(tasks))
        return len(tasks)

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    async def _run(self, task_id: int, retry_failed: bool) -> None:
        try:
            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is None:
                    logger.warning("任务不存在 task=%s", task_id)
                    return
                case_ids = await self._select_case_ids(db, task, retry_failed)
                task.total_cases = len(task.case_ids or [])
                if not case_ids:
                    task.status = TaskStatus.COMPLETED
                    task.control_state = "FINISHED"
                    task.finished_at = now()
                    metrics = await compute_task_metrics(db, task)
                    apply_metrics_to_task(task, metrics)
                    await db.commit()
                    await control_store.set_state(task_id, "FINISHED")
                    await self._publish_status(task)
                    return
                task.status = TaskStatus.RUNNING
                task.control_state = "RUNNING"
                task.started_at = task.started_at or now()
                task.finished_at = None
                await db.commit()
                environment_id = task.environment_id
                concurrency = max(1, min(task.concurrency or 3, 20))
                qps_limit = max(0.1, float(task.qps_limit or 5))
                retry_enabled = bool(task.retry_on_env_error)
                max_env_retries = max(0, min(int(task.max_env_retries or 0), 10))
                backoff_base = max(0.0, min(float(task.retry_backoff_seconds or 0), 60.0))
                task_name = task.name

            await control_store.set_state(task_id, "RUNNING")
            await self._publish_status_by_id(task_id)
            logger.info(
                "任务开始 task=%s(%s) cases=%d concurrency=%d qps=%.1f "
                "retry_on_env=%s max_retries=%d",
                task_id,
                task_name,
                len(case_ids),
                concurrency,
                qps_limit,
                retry_enabled,
                max_env_retries,
            )

            semaphore = asyncio.Semaphore(concurrency)
            limiter = QpsLimiter(qps_limit)
            retry_enabled = retry_enabled and max_env_retries > 0

            async def worker(case_id: int) -> None:
                async with semaphore:
                    attempt = 0
                    while True:
                        # 每一轮尝试都重新检查终止指令，避免重试绕过控制
                        if not await self._not_aborted(task_id):
                            return
                        await limiter.acquire()
                        snapshot, version = await self._load_case_snapshot(case_id)
                        if snapshot is None:
                            logger.warning("用例不存在或已删除 case=%s", case_id)
                            return
                        source = (
                            ExecutionSource.RETRY
                            if attempt > 0 or retry_failed
                            else ExecutionSource.BATCH
                        )
                        executor = CaseExecutor()
                        execution_id = await executor.execute(
                            case_id=case_id,
                            case_version=version,
                            snapshot=snapshot,
                            task_id=task_id,
                            source=source,
                            environment_id=environment_id,
                            attempt_no=attempt + 1,
                        )
                        await self._publish_progress(
                            task_id, execution_id, case_id, version
                        )
                        if not retry_enabled or attempt >= max_env_retries:
                            return
                        if await self._load_error_kind(execution_id) != ERROR_KIND_ENV:
                            return
                        attempt += 1
                        delay = retry_delay(attempt, backoff_base)
                        logger.info(
                            "环境类失败，准备第 %d/%d 次自动重试 case=%s delay=%.1fs",
                            attempt,
                            max_env_retries,
                            case_id,
                            delay,
                        )
                        if not await self._interruptible_sleep(task_id, delay):
                            return

            worker_tasks = [asyncio.create_task(worker(cid)) for cid in case_ids]
            pending: set[asyncio.Task[None]] = set(worker_tasks)
            aborted = False
            while pending:
                _, pending = await asyncio.wait(
                    pending, timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
                state = await control_store.get_state(task_id)
                if state in {"ABORTING", "ABORTED"}:
                    aborted = True
                    for worker_task in pending:
                        worker_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    pending = set()
                    break

            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is None:
                    return
                if aborted:
                    await self._close_running_executions(db, task_id)
                    task.status = TaskStatus.ABORTED
                    task.control_state = "ABORTED"
                else:
                    task.status = TaskStatus.COMPLETED
                    task.control_state = "FINISHED"
                task.finished_at = now()
                metrics = await compute_task_metrics(db, task)
                apply_metrics_to_task(task, metrics)
                await db.commit()
                await control_store.set_state(
                    task_id, "ABORTED" if aborted else "FINISHED"
                )
                await self._publish_status(task)
            logger.info("任务结束 task=%s aborted=%s", task_id, aborted)
        except asyncio.CancelledError:
            logger.warning("任务调度被取消 task=%s", task_id)
            raise
        except Exception as exc:
            logger.exception("任务执行异常 task=%s", task_id)
            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is not None:
                    task.status = TaskStatus.FAILED
                    task.control_state = "FAILED"
                    task.error_message = f"{type(exc).__name__}: {exc}"[:2000]
                    task.finished_at = now()
                    await db.commit()
                    await self._publish_status(task)
            await control_store.set_state(task_id, "FAILED")
        finally:
            self._workers.pop(task_id, None)

    # ------------------------------------------------------------------ #
    # 数据加载与事件
    # ------------------------------------------------------------------ #
    async def _select_case_ids(
        self, db: AsyncSession, task: BatchTask, retry_failed: bool
    ) -> list[int]:
        case_ids = [int(item) for item in (task.case_ids or [])]
        latest = {record.case_id: record for record in await latest_executions(db, task.id)}
        selected: list[int] = []
        for case_id in case_ids:
            record = latest.get(case_id)
            if record is None or record.status in {"RUNNING", "PENDING"} or (retry_failed and record.status in RETRYABLE_STATUSES):
                selected.append(case_id)
        return selected

    @staticmethod
    async def _load_error_kind(execution_id: int) -> str | None:
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            return record.error_kind if record is not None else None

    @staticmethod
    async def _not_aborted(task_id: int) -> bool:
        """返回 False 表示任务已进入终止流程。"""
        state = await control_store.get_state(task_id)
        return state not in {"ABORTING", "ABORTED"}

    @staticmethod
    async def _interruptible_sleep(task_id: int, seconds: float) -> bool:
        """可被终止打断的退避等待。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, seconds)
        while True:
            state = await control_store.get_state(task_id)
            if state in {"ABORTING", "ABORTED"}:
                return False
            remaining = deadline - loop.time()
            if remaining <= 0:
                return True
            await asyncio.sleep(min(remaining, 0.4))

    @staticmethod
    async def _load_case_snapshot(case_id: int) -> tuple[dict[str, Any] | None, int]:
        async with SessionLocal() as db:
            case = await db.get(TestCase, case_id)
            if case is None or case.deleted_at is not None:
                return None, 0
            revision = (
                await db.execute(
                    select(TestCaseRevision).where(
                        TestCaseRevision.case_id == case.id,
                        TestCaseRevision.version == case.current_version,
                    )
                )
            ).scalar_one_or_none()
            if revision is not None:
                return revision.snapshot, revision.version
            return build_snapshot(case), case.current_version

    async def _publish_progress(
        self, task_id: int, execution_id: int, case_id: int, case_version: int
    ) -> None:
        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            if task is None:
                return
            metrics = await compute_task_metrics(db, task)
            apply_metrics_to_task(task, metrics)
            await db.commit()
            record = await db.get(ExecutionRecord, execution_id)
            item: CaseExecutionItem | None = None
            if record is not None:
                case = await db.get(TestCase, record.case_id)
                item = CaseExecutionItem(
                    id=record.id,
                    task_id=record.task_id,
                    case_id=record.case_id,
                    case_code=case.code if case else None,
                    case_name=case.name if case else None,
                    source=record.source,
                    status=record.status,
                    attempt_no=record.attempt_no,
                    error_kind=record.error_kind,
                    pass_flag=record.pass_flag,
                    total_score=record.total_score,
                    case_version=record.case_version,
                    turns_used=record.turns_used,
                    latency_ms=record.latency_ms,
                    ttft_ms=record.ttft_ms,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                )
            event = TaskProgressEvent(
                type="progress",
                task_id=task.id,
                status=task.status,
                control_state=task.control_state,
                progress=metrics.progress,
                finished_cases=metrics.finished_cases,
                total_cases=metrics.total_cases,
                pass_count=metrics.status_distribution.PASS,
                fail_count=metrics.status_distribution.FAIL
                + metrics.status_distribution.REPETITION_ABORT,
                error_count=metrics.status_distribution.ERROR,
                max_turns_count=metrics.status_distribution.MAX_TURNS_EXCEEDED,
                metrics=metrics,
                execution=item,
                timestamp=datetime.now(),
            )
            await control_store.set_progress(
                task_id,
                {
                    "progress": metrics.progress,
                    "finished": metrics.finished_cases,
                    "total": metrics.total_cases,
                    "status": task.status,
                },
            )
            await event_bus.publish(task_id, event.model_dump(mode="json"))

    async def _publish_status(self, task: BatchTask) -> None:
        async with SessionLocal() as db:
            fresh = await db.get(BatchTask, task.id)
            if fresh is None:
                return
            metrics = await compute_task_metrics(db, fresh)
            event = TaskProgressEvent(
                type="status",
                task_id=fresh.id,
                status=fresh.status,
                control_state=fresh.control_state,
                progress=metrics.progress,
                finished_cases=metrics.finished_cases,
                total_cases=metrics.total_cases,
                pass_count=metrics.status_distribution.PASS,
                fail_count=metrics.status_distribution.FAIL
                + metrics.status_distribution.REPETITION_ABORT,
                error_count=metrics.status_distribution.ERROR,
                max_turns_count=metrics.status_distribution.MAX_TURNS_EXCEEDED,
                metrics=metrics,
                message=fresh.error_message,
                timestamp=datetime.now(),
            )
            await event_bus.publish(fresh.id, event.model_dump(mode="json"))

    async def _publish_status_by_id(self, task_id: int) -> None:
        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            if task is not None:
                await self._publish_status(task)

    @staticmethod
    async def _close_running_executions(
        db: AsyncSession, task_id: int, message: str = "任务被终止"
    ) -> None:
        """把未完成的执行记录（执行中/排队中）一并收口为已终止。

        否则终止或重启后这些记录会永远停在 RUNNING/PENDING，
        且「失败重跑」不会选中它们。
        """
        await db.execute(
            update(ExecutionRecord)
            .where(
                ExecutionRecord.task_id == task_id,
                ExecutionRecord.status.in_(["RUNNING", "PENDING"]),
            )
            .values(
                status=ExecutionStatus.ABORTED,
                pass_flag=False,
                error_message=message,
                finished_at=now(),
            )
        )


scheduler = BatchScheduler()
