"""批量任务调度器：并发控制、QPS 限流、终止、断点重跑。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from billing_qa.core.config import settings
from billing_qa.core.control_store import control_store
from billing_qa.core.event_bus import event_bus
from billing_qa.core.logging import get_logger
from billing_qa.core.rate_limiter import QpsLimiter
from billing_qa.db.base import now
from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import ExecutionSource, ExecutionStatus, TaskScheduleType, TaskStatus
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.schemas.case import CaseExecutionItem
from billing_qa.schemas.task import TaskProgressEvent
from billing_qa.services.engine.error_policy import (
    ERROR_KIND_ENV,
    RETRYABLE_ERROR_KINDS,
    retry_delay,
)
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

# 未完成状态：续做只补跑这些用例，已有最终判定的用例保持原结果。
UNFINISHED_STATUSES = {"RUNNING", "PENDING", "ABORTED"}


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

    def _task_lock_name(self, task_id: int) -> str:
        return control_store.lock_key(task_id)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    async def start(
        self, task_id: int, *, retry_failed: bool = False, resume: bool = False
    ) -> bool:
        async with self._lock(task_id):
            if self.is_running(task_id):
                return False
            token = uuid.uuid4().hex
            if not await control_store.acquire_lock(
                self._task_lock_name(task_id), token, settings.task_run_lock_ttl_seconds
            ):
                logger.warning("任务已由其他实例执行，跳过启动 task=%s", task_id)
                return False
            worker = asyncio.create_task(
                self._run(task_id, retry_failed, resume, run_token=token)
            )
            self._workers[task_id] = worker
        return True

    async def retry_case(self, task_id: int, case_id: int) -> bool:
        """在当前批次内单独重跑一条用例。

        与 :meth:`start` 互斥（同一任务同时只允许一个 worker），任务立即
        切回 RUNNING 以便前端连接实时进度；执行完成后按用例最新一次结果
        重算指标并收口为 COMPLETED。返回 False 表示任务正在运行。
        """
        async with self._lock(task_id):
            if self.is_running(task_id):
                return False
            token = uuid.uuid4().hex
            if not await control_store.acquire_lock(
                self._task_lock_name(task_id), token, settings.task_run_lock_ttl_seconds
            ):
                logger.warning("任务已由其他实例执行，跳过单用例重试 task=%s", task_id)
                return False
            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is None:
                    await control_store.release_lock(self._task_lock_name(task_id), token)
                    return False
                task.status = TaskStatus.RUNNING
                task.control_state = "RUNNING"
                task.started_at = task.started_at or now()
                task.finished_at = None
                if await control_store.get_state(task_id) == "ABORTING":
                    # 与终止指令竞态：保持终止语义，回滚刚写入的 RUNNING
                    # 并收口，不把控制面翻转回去。
                    task.status = TaskStatus.ABORTED
                    task.control_state = "ABORTED"
                    task.finished_at = now()
                    await db.commit()
                    await control_store.set_state(task_id, "ABORTED")
                    await control_store.release_lock(self._task_lock_name(task_id), token)
                    logger.info("单用例重试与终止指令竞态，保持终止 task=%s", task_id)
                    return False
                await db.commit()
            await control_store.set_state(task_id, "RUNNING")
            worker = asyncio.create_task(
                self._run_case_retry(task_id, case_id, run_token=token)
            )
            self._workers[task_id] = worker
        logger.info("单用例重试已启动 task=%s case=%s", task_id, case_id)
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
            # 落库完成后把控制面定稿为 ABORTED，避免 ABORTING 永久残留——
            # ABORTING 只应表示「仍在下发的终止意图」。
            await control_store.set_state(task_id, "ABORTED")
        logger.info("任务终止指令已下发 task=%s", task_id)

    async def shutdown(self) -> None:
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    async def recover_interrupted_tasks(self) -> int:
        """服务重启后恢复中断的任务。

        - 用户已明确终止（控制面 ABORTING/ABORTED）的任务：保持终止语义，
          连同历史 ``PAUSED`` 数据一并收口为已终止；
        - 其余 RUNNING 任务：先收口在途执行记录（保留审计痕迹），再自动
          续做；拿不到运行锁（其他实例持有 / 上个进程的锁尚未过期）时先
          跳过，由后台轮询（:meth:`adopt_interrupted_tasks`）在锁过期后接管。

        返回本次处理（续做 + 收口）的任务数。
        """
        async with SessionLocal() as db:
            rows = await db.execute(
                select(BatchTask).where(
                    # "PAUSED" 仅用于清理历史数据：该状态已从产品中移除
                    BatchTask.status.in_([TaskStatus.RUNNING.value, "PAUSED"])
                )
            )
            tasks = list(rows.scalars().all())
        resumed = 0
        closed = 0
        for task in tasks:
            if str(task.status) == TaskStatus.RUNNING.value:
                if await self.adopt_interrupted_task(int(task.id)):
                    resumed += 1
                else:
                    # 未接管：区分「带终止意图被收口为 ABORTED」与「锁被其他实例持有跳过」
                    async with SessionLocal() as db:
                        fresh_status = await db.scalar(
                            select(BatchTask.status).where(BatchTask.id == task.id)
                        )
                    if fresh_status == TaskStatus.ABORTED.value:
                        closed += 1
                continue
            # 历史 PAUSED 数据：状态已从产品移除，直接收口
            async with SessionLocal() as db:
                fresh = await db.get(BatchTask, task.id)
                if fresh is None or str(fresh.status) == TaskStatus.RUNNING.value:
                    continue
                fresh.status = TaskStatus.ABORTED
                fresh.control_state = "ABORTED"
                fresh.finished_at = fresh.finished_at or now()
                fresh.error_message = "历史暂停任务已收口为终止"
                await self._close_running_executions(db, fresh.id, "服务重启导致执行中断")
                metrics = await compute_task_metrics(db, fresh)
                apply_metrics_to_task(fresh, metrics)
                await db.commit()
            await control_store.set_state(task.id, "ABORTED")
            closed += 1
        if resumed or closed:
            logger.info(
                "重启恢复完成：自动续做 %d 个，收口为已终止 %d 个", resumed, closed
            )
        return resumed + closed

    async def adopt_interrupted_task(self, task_id: int) -> bool:
        """接管一个被服务重启中断的 RUNNING 任务并自动续做。

        顺序很关键：**先拿分布式运行锁，再收口在途记录**，避免把另一个
        实例正在推进的执行误标为终止。返回 False 表示未接管（任务不存在 /
        状态已变化 / 已有终止意图 / 锁被其他实例持有）。
        """
        lock_name = self._task_lock_name(task_id)
        async with self._lock(task_id):
            if self.is_running(task_id):
                return False
            token = uuid.uuid4().hex
            if not await control_store.acquire_lock(
                lock_name, token, settings.task_run_lock_ttl_seconds
            ):
                logger.info("任务运行锁被其他实例持有，暂不接管 task=%s", task_id)
                return False
            adopted = False
            try:
                state = await control_store.get_state(task_id)
                async with SessionLocal() as db:
                    task = await db.get(BatchTask, task_id)
                    if task is None or str(task.status) != TaskStatus.RUNNING.value:
                        return False
                    if state in {"ABORTING", "ABORTED"}:
                        # 用户在重启前已明确终止：保持终止语义，不自动拉起
                        task.status = TaskStatus.ABORTED
                        task.control_state = "ABORTED"
                        task.finished_at = task.finished_at or now()
                        task.error_message = "服务重启时任务正在终止，已保持终止状态"
                        await self._close_running_executions(
                            db, task_id, "服务重启导致执行中断"
                        )
                        metrics = await compute_task_metrics(db, task)
                        apply_metrics_to_task(task, metrics)
                        await db.commit()
                        await control_store.set_state(task_id, "ABORTED")
                        logger.info(
                            "重启恢复：任务带终止意图，已收口为已终止 task=%s", task_id
                        )
                        return False
                    # 中断续做：把在途记录收口为 ABORTED（保留审计痕迹），
                    # 续做时这些用例会被重新执行，最终结果不残留终止态。
                    await self._close_running_executions(
                        db, task_id, "服务重启导致执行中断"
                    )
                    task.control_state = "RUNNING"
                    task.error_message = None
                    task.finished_at = None
                    metrics = await compute_task_metrics(db, task)
                    apply_metrics_to_task(task, metrics)
                    await db.commit()
                await control_store.set_state(task_id, "RUNNING")
                worker = asyncio.create_task(
                    self._run(task_id, False, True, run_token=token)
                )
                self._workers[task_id] = worker
                adopted = True
                logger.warning("重启恢复：已自动续做中断任务 task=%s", task_id)
                return True
            finally:
                if not adopted:
                    # 未成功接管时归还锁（成功时锁移交 worker 心跳续租）
                    await control_store.release_lock(lock_name, token)

    async def adopt_interrupted_tasks(self, *, limit: int = 20) -> list[int]:
        """周期自愈：接管无人推进的 RUNNING 任务。

        覆盖启动恢复时锁尚未过期而跳过的任务；多实例下靠分布式运行锁
        互斥，健康实例持有锁时自动跳过。
        """
        adopted: list[int] = []
        async with SessionLocal() as db:
            rows = (
                (
                    await db.execute(
                        select(BatchTask.id)
                        .where(BatchTask.status == TaskStatus.RUNNING.value)
                        .order_by(BatchTask.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        for task_id in rows:
            if self.is_running(int(task_id)):
                continue
            if await self.adopt_interrupted_task(int(task_id)):
                adopted.append(int(task_id))
        return adopted

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    async def _run(
        self,
        task_id: int,
        retry_failed: bool,
        resume: bool,
        *,
        run_token: str | None = None,
    ) -> None:
        heartbeat: asyncio.Task[None] | None = None
        if run_token is not None:
            heartbeat = asyncio.create_task(self._heartbeat(task_id, run_token))
        try:
            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is None:
                    logger.warning("任务不存在 task=%s", task_id)
                    return
                case_ids = await self._select_case_ids(
                    db, task, retry_failed=retry_failed, resume=resume
                )
                attempt_bases = await self._attempt_bases(db, task.id)
                task.total_cases = len(task.case_ids or [])
                if not case_ids:
                    task.status = TaskStatus.COMPLETED
                    task.control_state = "FINISHED"
                    task.finished_at = now()
                    task.error_message = None
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
                task.error_message = None
                await db.commit()
                environment_id = task.environment_id
                concurrency = max(1, min(task.concurrency or 3, 20))
                qps_limit = max(0.1, float(task.qps_limit or 5))
                retry_on_error = bool(task.retry_on_error)
                max_error_retries = max(0, min(int(task.max_error_retries or 0), 10))
                # 兼容旧任务：迁移前只配置了环境类重试
                retry_on_env_error = bool(task.retry_on_env_error)
                max_env_retries = max(0, min(int(task.max_env_retries or 0), 10))
                backoff_base = max(0.0, min(float(task.retry_backoff_seconds or 0), 60.0))
                enable_self_check = bool(task.enable_self_check)
                task_name = task.name

            retry_enabled = retry_on_error or retry_on_env_error
            # 续做 / 失败重跑写入不同来源，Trace 与导出可区分本次补跑的性质；
            # 自动重试仍统一记为 RETRY。
            base_source = (
                ExecutionSource.RESUME
                if resume
                else ExecutionSource.RETRY
                if retry_failed
                else ExecutionSource.BATCH
            )

            # 启动过程中可能到达终止指令（如恢复扫描与本轮启动竞态、或用户
            # 对刚入队的任务点终止）：控制面出现 ABORTING 即保持终止语义，
            # 收口退出，不把控制面翻转回 RUNNING 擦掉终止意图。
            if await control_store.get_state(task_id) == "ABORTING":
                async with SessionLocal() as db:
                    task = await db.get(BatchTask, task_id)
                    if task is not None:
                        task.status = TaskStatus.ABORTED
                        task.control_state = "ABORTED"
                        task.finished_at = now()
                        task.error_message = "启动过程中收到终止指令，保持终止状态"
                        metrics = await compute_task_metrics(db, task)
                        apply_metrics_to_task(task, metrics)
                        await db.commit()
                        await control_store.set_state(task_id, "ABORTED")
                        await self._publish_status(task)
                logger.info("任务启动过程中收到终止指令，保持终止 task=%s", task_id)
                return

            await control_store.set_state(task_id, "RUNNING")
            await self._publish_status_by_id(task_id)
            logger.info(
                "任务开始 task=%s(%s) cases=%d concurrency=%d qps=%.1f "
                "retry_on_error=%s max_error_retries=%d "
                "retry_on_env=%s max_env_retries=%d",
                task_id,
                task_name,
                len(case_ids),
                concurrency,
                qps_limit,
                retry_on_error,
                max_error_retries,
                retry_on_env_error,
                max_env_retries,
            )

            semaphore = asyncio.Semaphore(concurrency)
            limiter = QpsLimiter(qps_limit)

            async def worker(case_id: int) -> None:
                async with semaphore:
                    attempt = 0
                    attempt_base = attempt_bases.get(case_id, 0)
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
                            ExecutionSource.RETRY if attempt > 0 else base_source
                        )
                        executor = CaseExecutor()
                        execution_id = await executor.execute(
                            case_id=case_id,
                            case_version=version,
                            snapshot=snapshot,
                            task_id=task_id,
                            source=source,
                            environment_id=environment_id,
                            attempt_no=attempt_base + attempt + 1,
                            enable_self_check=enable_self_check,
                        )
                        await self._publish_progress(
                            task_id, execution_id, case_id, version
                        )
                        if not retry_enabled:
                            return
                        error_kind = await self._load_error_kind(execution_id)
                        if error_kind is None:
                            return
                        if retry_on_error:
                            if error_kind not in RETRYABLE_ERROR_KINDS:
                                return
                            retry_limit = max_error_retries
                        elif retry_on_env_error and error_kind == ERROR_KIND_ENV:
                            retry_limit = max_env_retries
                        else:
                            return
                        if retry_limit <= 0 or attempt >= retry_limit:
                            return
                        attempt += 1
                        delay = retry_delay(attempt, backoff_base)
                        logger.info(
                            "执行异常，准备第 %d/%d 次自动重试 case=%s kind=%s delay=%.1fs",
                            attempt,
                            retry_limit,
                            case_id,
                            error_kind,
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
            if not aborted:
                try:
                    await self._start_next_round(task_id)
                except Exception:
                    logger.exception("连续轮次启动失败 task=%s", task_id)
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
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if run_token is not None:
                await control_store.release_lock(
                    self._task_lock_name(task_id), run_token
                )
            self._workers.pop(task_id, None)

    async def _heartbeat(self, task_id: int, token: str) -> None:
        """持续续租任务运行锁；续租失败说明锁已易主，记录后停止续租。

        不因此中断本地执行：出现双主的概率极低（需要 Redis 抖动叠加另一
        实例恰好接管），任务最终由各实例自身的执行器收口。
        """
        ttl = settings.task_run_lock_ttl_seconds
        interval = max(5.0, ttl / 3)
        while True:
            await asyncio.sleep(interval)
            if not await control_store.renew_lock(
                self._task_lock_name(task_id), token, ttl
            ):
                logger.error(
                    "任务运行锁续租失败，可能已被其他实例接管 task=%s", task_id
                )
                return

    # ------------------------------------------------------------------ #
    # 单用例重试
    # ------------------------------------------------------------------ #
    async def _run_case_retry(
        self, task_id: int, case_id: int, *, run_token: str | None = None
    ) -> None:
        """在当前批次内重跑单条用例：复用执行器，结果作为该用例的最新一次尝试。"""
        cancelled = False
        try:
            async with SessionLocal() as db:
                task = await db.get(BatchTask, task_id)
                if task is None:
                    logger.warning("单用例重试任务不存在 task=%s", task_id)
                    return
                environment_id = task.environment_id
                enable_self_check = bool(task.enable_self_check)
                latest = (
                    await db.execute(
                        select(ExecutionRecord)
                        .where(
                            ExecutionRecord.task_id == task_id,
                            ExecutionRecord.case_id == case_id,
                        )
                        .order_by(ExecutionRecord.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                attempt_no = int(latest.attempt_no if latest is not None else 0) + 1

            snapshot, version = await self._load_case_snapshot(case_id)
            if snapshot is None:
                logger.warning("单用例重试跳过：用例不存在或已删除 case=%s", case_id)
                return
            await self._publish_status_by_id(task_id)
            execution_id = await CaseExecutor().execute(
                case_id=case_id,
                case_version=version,
                snapshot=snapshot,
                task_id=task_id,
                source=ExecutionSource.RETRY,
                environment_id=environment_id,
                attempt_no=attempt_no,
                enable_self_check=enable_self_check,
            )
            await self._publish_progress(task_id, execution_id, case_id, version)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:
            logger.exception("单用例重试执行失败 task=%s case=%s", task_id, case_id)
        finally:
            try:
                await self._finish_case_retry(task_id, cancelled=cancelled)
            finally:
                if run_token is not None:
                    await control_store.release_lock(
                        self._task_lock_name(task_id), run_token
                    )
                self._workers.pop(task_id, None)

    async def _finish_case_retry(self, task_id: int, *, cancelled: bool) -> None:
        """单用例重试结束后收口任务状态并重算指标。"""
        state = await control_store.get_state(task_id)
        aborted = cancelled or state in {"ABORTING", "ABORTED"}
        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            if task is None:
                return
            metrics = await compute_task_metrics(db, task)
            apply_metrics_to_task(task, metrics)
            if aborted:
                task.status = TaskStatus.ABORTED
                task.control_state = "ABORTED"
            else:
                task.status = TaskStatus.COMPLETED
                task.control_state = "FINISHED"
                task.error_message = None
            task.finished_at = now()
            await db.commit()
            await control_store.set_state(task_id, "ABORTED" if aborted else "FINISHED")
            await self._publish_status(task)
        logger.info(
            "单用例重试结束 task=%s aborted=%s", task_id, aborted
        )

    # ------------------------------------------------------------------ #
    # 数据加载与事件
    # ------------------------------------------------------------------ #
    ROUND_CHAIN_ATTEMPTS = 3
    # 恢复扫描只兜底「内联补链已失败且超过宽限期」的链，避免与刚完成的正常补链重复
    ROUND_CHAIN_GRACE_SECONDS = 60

    async def _start_next_round(self, task_id: int) -> None:
        """正常完成后发起下一轮；创建/启动失败会退避重试。

        重试仍失败时不丢链：下一轮任务已落库为 PENDING，由后台
        :meth:`resume_round_chains` 继续自愈。
        """
        for attempt in range(1, self.ROUND_CHAIN_ATTEMPTS + 1):
            try:
                run_id = await self._ensure_next_round(task_id)
            except Exception:
                logger.exception(
                    "连续轮次创建失败 task=%s（第 %d/%d 次）",
                    task_id,
                    attempt,
                    self.ROUND_CHAIN_ATTEMPTS,
                )
            else:
                if run_id is None:
                    return
                status = await self._round_status(run_id)
                if status != TaskStatus.PENDING.value:
                    # 下一轮已执行过（重跑/恢复扫描的重复触发），不重复拉起
                    return
                if self.is_running(run_id) or await self.start(run_id):
                    logger.info("连续轮次已启动 task=%s", run_id)
                    return
                logger.warning(
                    "连续轮次启动未成功，稍后重试 task=%s（第 %d/%d 次）",
                    run_id,
                    attempt,
                    self.ROUND_CHAIN_ATTEMPTS,
                )
            if attempt < self.ROUND_CHAIN_ATTEMPTS:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        logger.error("连续轮次自愈未成功，等待后台恢复扫描 task=%s", task_id)

    async def _ensure_next_round(self, task_id: int) -> int | None:
        """幂等派生下一轮：已存在则直接复用；无需派生时返回 None。"""
        # 延迟导入避免与 schedule 模块（其顶部导入 scheduler）形成循环依赖
        from billing_qa.services.engine.schedule import clone_next_round

        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            if task is None or task.status != TaskStatus.COMPLETED:
                return None
            total = max(1, int(task.repeat_rounds or 1))
            if total <= 1:
                return None
            current_index = max(1, int(task.round_index or 1))
            plan_id = task.schedule_source_id or task.id
            root = task if plan_id == task.id else await db.get(BatchTask, plan_id)
            # 周期计划停止只影响后续天的触发，当天已派生的轮次链应正常跑完；
            # 指定时间/立即执行的连续多轮则用 schedule_active 表示链是否继续。
            periodic = root is not None and root.schedule_type in {
                TaskScheduleType.DAILY.value,
                TaskScheduleType.WEEKLY.value,
            }
            if not periodic and root is not None and not root.schedule_active:
                logger.info("计划已停止，不再派生下一轮 task=%s plan=%s", task_id, plan_id)
                return None
            if current_index >= total:
                if not periodic and root is not None and root.schedule_active:
                    # 最后一轮正常跑完：轮次链收口，停止按钮随之消失。
                    root.schedule_active = False
                    await db.commit()
                return None
            existing = (
                await db.execute(
                    select(BatchTask.id).where(BatchTask.round_prev_id == task.id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return int(existing)
            plan_id = task.schedule_source_id or task.id
            clone = clone_next_round(task, plan_id=plan_id, round_index=current_index + 1)
            db.add(clone)
            try:
                await db.commit()
            except IntegrityError:
                # 并发补链（重试/恢复扫描）已创建，复用已有记录
                await db.rollback()
                existing = (
                    await db.execute(
                        select(BatchTask.id).where(BatchTask.round_prev_id == task.id)
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise
                return int(existing)
            logger.info(
                "连续轮次已派生：task=%s 第 %d/%d 轮 → %s",
                task.id,
                current_index + 1,
                total,
                clone.id,
            )
            return int(clone.id)

    @staticmethod
    async def _round_status(run_id: int) -> str | None:
        async with SessionLocal() as db:
            return await db.scalar(select(BatchTask.status).where(BatchTask.id == run_id))

    async def resume_round_chains(self, *, limit: int = 20) -> list[int]:
        """后台自愈：恢复中断的连续轮次链。

        覆盖两类故障：
        1. 下一轮已创建但启动失败 / 服务重启，任务停在 PENDING；
        2. 上一轮已完成，但创建下一轮时进程异常退出，链中断。
        """
        started: list[int] = []
        async with SessionLocal() as db:
            pending_runs = (
                (
                    await db.execute(
                        select(BatchTask)
                        .where(
                            BatchTask.round_prev_id.is_not(None),
                            BatchTask.status == TaskStatus.PENDING.value,
                        )
                        .order_by(BatchTask.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        for run in pending_runs:
            run_id = int(run.id)
            if self.is_running(run_id):
                continue
            state = await control_store.get_state(run_id)
            if state in {"ABORTING", "ABORTED"}:
                # 用户已对该轮次明确终止（终止指令先写控制面再落库的窗口期），
                # 保持终止语义，收口而不是拉起。
                await self._abort_pending_run(run_id)
                logger.info("恢复扫描：轮次带终止意图，收口待执行轮次 task=%s", run_id)
                continue
            if not await self._chain_active(run):
                # 计划已停止：作废待执行轮次，避免恢复扫描反复拉起。
                await self._abort_pending_run(run_id)
                logger.info("恢复扫描：计划已停止，跳过并作废待执行轮次 task=%s", run_id)
                continue
            if await self.start(run_id):
                started.append(run_id)
                logger.info("恢复扫描：已启动待执行的轮次 task=%s", run_id)

        child_table = aliased(BatchTask)
        child_exists = exists().where(child_table.round_prev_id == BatchTask.id)
        grace_deadline = datetime.now() - timedelta(seconds=self.ROUND_CHAIN_GRACE_SECONDS)
        async with SessionLocal() as db:
            stalled_ids = (
                await db.execute(
                    select(BatchTask.id)
                    .where(
                        BatchTask.status == TaskStatus.COMPLETED.value,
                        BatchTask.repeat_rounds > BatchTask.round_index,
                        BatchTask.finished_at.is_not(None),
                        BatchTask.finished_at < grace_deadline,
                        ~child_exists,
                    )
                    .order_by(BatchTask.id)
                    .limit(limit)
                )
            ).scalars().all()
        for task_id in stalled_ids:
            if not self.is_running(int(task_id)):
                async with SessionLocal() as db:
                    task = await db.get(BatchTask, task_id)
                if task is None or not await self._chain_active(task):
                    logger.info("恢复扫描：计划已停止，跳过补链 task=%s", task_id)
                    continue
                logger.warning("恢复扫描：轮次链中断，补派生下一轮 task=%s", task_id)
                await self._start_next_round(int(task_id))
        return started

    @staticmethod
    async def _chain_active(task: BatchTask) -> bool:
        """轮次链是否仍可继续：周期计划当天批次不受计划启停影响。"""
        total = max(1, int(task.repeat_rounds or 1))
        if total <= 1:
            return False
        plan_id = task.schedule_source_id or task.id
        async with SessionLocal() as db:
            root = task if plan_id == task.id else await db.get(BatchTask, plan_id)
        if root is None:
            return False
        if root.schedule_type in {
            TaskScheduleType.DAILY.value,
            TaskScheduleType.WEEKLY.value,
        }:
            return True
        return bool(root.schedule_active)

    async def _abort_pending_run(self, task_id: int) -> None:
        """把无法继续的 PENDING 计划批次收口为已终止，避免恢复扫描反复拉起。"""
        async with SessionLocal() as db:
            fresh = await db.get(BatchTask, task_id)
            if fresh is None or str(fresh.status) != TaskStatus.PENDING.value:
                return
            fresh.status = TaskStatus.ABORTED
            fresh.control_state = "ABORTED"
            fresh.finished_at = now()
            fresh.error_message = "计划已停止或带终止意图，待执行批次自动收口"
            await db.commit()
        await control_store.set_state(task_id, "ABORTED")

    @staticmethod
    async def _plan_batch_active(run: BatchTask) -> bool:
        """派生批次所属的计划是否仍允许该批次执行。

        与轮次链的 ``_chain_active`` 不同：周期计划每次触发的批次通常只有
        1 轮，只要计划是 DAILY/WEEKLY 就应跑完当天批次；指定时间 / 连续多轮
        计划则以 ``schedule_active`` 为准。
        """
        plan_id = run.schedule_source_id
        if plan_id is None:
            return False
        async with SessionLocal() as db:
            root = await db.get(BatchTask, plan_id)
        if root is None:
            return False
        if root.schedule_type in {
            TaskScheduleType.DAILY.value,
            TaskScheduleType.WEEKLY.value,
        }:
            return True
        return bool(root.schedule_active)

    async def resume_orphaned_plan_runs(self, *, limit: int = 20) -> list[int]:
        """后台自愈：恢复「已派发但从未启动」的计划批次。

        ``run_due_tasks`` 在「克隆入库 / 置 PENDING」与 ``scheduler.start``
        之间进程退出时，会留下永远无人拉起的孤儿任务；周期计划还会因为
        ``_latest_run_status`` 一直看到 PENDING 而跳过所有后续触发。这里分两类恢复：

        1. 周期 / 连续多轮计划派生的首轮批次：``round_prev_id`` 为空、
           ``schedule_source_id`` 非空且仍为 PENDING；
        2. 已到点的一次性定时计划本体：``last_run_at`` 已写（派发标记）但仍是 PENDING。

        计划已停止或控制面带终止意图的批次收口为 ABORTED，不自动拉起。
        """
        started: list[int] = []
        async with SessionLocal() as db:
            orphans = (
                (
                    await db.execute(
                        select(BatchTask)
                        .where(
                            BatchTask.status == TaskStatus.PENDING.value,
                            BatchTask.round_prev_id.is_(None),
                            BatchTask.schedule_source_id.is_not(None),
                        )
                        .order_by(BatchTask.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        for run in orphans:
            run_id = int(run.id)
            if self.is_running(run_id):
                continue
            state = await control_store.get_state(run_id)
            if state in {"ABORTING", "ABORTED"}:
                # 用户已对该批次明确终止（终止指令先写控制面再落库的窗口期），
                # 保持终止语义，收口而不是拉起。
                await self._abort_pending_run(run_id)
                logger.info("恢复扫描：批次带终止意图，收口派发未启动的批次 task=%s", run_id)
                continue
            if not await self._plan_batch_active(run):
                await self._abort_pending_run(run_id)
                logger.info("恢复扫描：计划已停止，收口派发未启动的批次 task=%s", run_id)
                continue
            if await self.start(run_id):
                started.append(run_id)
                logger.warning("恢复扫描：已启动派发后未启动的计划首轮 task=%s", run_id)

        async with SessionLocal() as db:
            one_shots = (
                (
                    await db.execute(
                        select(BatchTask.id)
                        .where(
                            BatchTask.status == TaskStatus.PENDING.value,
                            BatchTask.schedule_type == TaskScheduleType.SCHEDULED.value,
                            BatchTask.last_run_at.is_not(None),
                        )
                        .order_by(BatchTask.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        for task_id in one_shots:
            task_id = int(task_id)
            if self.is_running(task_id):
                continue
            state = await control_store.get_state(task_id)
            if state in {"ABORTING", "ABORTED"}:
                await self._abort_pending_run(task_id)
                logger.info(
                    "恢复扫描：一次性计划带终止意图，收口待执行计划 task=%s", task_id
                )
                continue
            if await self.start(task_id):
                started.append(task_id)
                logger.warning("恢复扫描：已启动派发后未启动的一次性定时计划 task=%s", task_id)
        return started

    async def _select_case_ids(
        self,
        db: AsyncSession,
        task: BatchTask,
        retry_failed: bool = False,
        resume: bool = False,
    ) -> list[int]:
        """按本次启动的语义挑选要执行的用例。

        - 首次/普通启动：只补没跑过的用例，避免重复覆盖已有结果；
        - 失败重跑（retry_failed）：失败/异常/已终止与未跑过的用例全部重跑；
        - 续做（resume）：只补未完成（未跑 / 执行中 / 已终止）的用例，
          已有最终判定的用例保持原结果，不因续做被重跑。
        """
        case_ids = [int(item) for item in (task.case_ids or [])]
        latest = {record.case_id: record for record in await latest_executions(db, task.id)}

        def needs_run(record: ExecutionRecord | None) -> bool:
            if record is None or record.status in {"RUNNING", "PENDING"}:
                return True
            if resume:
                return record.status in UNFINISHED_STATUSES
            if retry_failed:
                return record.status in RETRYABLE_STATUSES
            return False

        return [case_id for case_id in case_ids if needs_run(latest.get(case_id))]

    @staticmethod
    async def _attempt_bases(db: AsyncSession, task_id: int) -> dict[int, int]:
        """每个用例当前的最大尝试次数，续跑时从其后继续编号。"""
        rows = (
            await db.execute(
                select(
                    ExecutionRecord.case_id,
                    func.max(ExecutionRecord.attempt_no),
                )
                .where(ExecutionRecord.task_id == task_id)
                .group_by(ExecutionRecord.case_id)
            )
        ).all()
        return {int(case_id): int(attempt or 0) for case_id, attempt in rows}

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
