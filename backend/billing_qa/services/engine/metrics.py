"""任务级指标聚合：进度、ETA、性能 P95、双轨 Token。"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import StatusDistribution, TaskMetrics

TERMINAL_STATUSES = {
    "PASS",
    "FAIL",
    "ERROR",
    "MAX_TURNS_EXCEEDED",
    "REPETITION_ABORT",
    "ABORTED",
}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[int(rank)], 2)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


async def latest_executions(db: AsyncSession, task_id: int) -> list[ExecutionRecord]:
    """每个用例取最新一次执行（用于统计，避免重跑重复计数）。"""
    subquery = (
        select(
            ExecutionRecord.case_id.label("case_id"),
            func.max(ExecutionRecord.id).label("max_id"),
        )
        .where(ExecutionRecord.task_id == task_id)
        .group_by(ExecutionRecord.case_id)
        .subquery()
    )
    rows = await db.execute(
        select(ExecutionRecord).join(subquery, ExecutionRecord.id == subquery.c.max_id)
    )
    return list(rows.scalars().all())


def build_distribution(records: list[ExecutionRecord], total_cases: int) -> StatusDistribution:
    distribution = StatusDistribution()
    for record in records:
        if hasattr(distribution, record.status):
            setattr(distribution, record.status, getattr(distribution, record.status) + 1)
    executed = len(records)
    distribution.PENDING = max(total_cases - executed, 0)
    return distribution


async def compute_task_metrics(db: AsyncSession, task: BatchTask) -> TaskMetrics:
    records = await latest_executions(db, task.id)
    total = task.total_cases or len(task.case_ids or [])
    finished = len([r for r in records if r.status in TERMINAL_STATUSES])
    distribution = build_distribution(records, total)

    latencies = [float(r.latency_ms) for r in records if r.latency_ms is not None]
    ttfts = [float(r.ttft_ms) for r in records if r.ttft_ms is not None]
    turns = [int(r.turns_used) for r in records]
    simulator_tokens = sum(int(r.simulator_tokens or 0) for r in records)
    judge_tokens = sum(int(r.judge_tokens or 0) for r in records)
    env_retry_count = len([r for r in records if int(r.attempt_no or 1) > 1])

    now = datetime.now()
    started = task.started_at or task.created_at or now
    ended = task.finished_at or now
    elapsed = max((ended - started).total_seconds(), 0.0)
    eta: float | None = None
    if task.status == "RUNNING" and finished > 0 and total > finished:
        eta = round(elapsed / finished * (total - finished), 1)
    throughput = round(finished / elapsed * 60, 2) if elapsed > 0 and finished else 0.0

    passed = distribution.PASS
    return TaskMetrics(
        task_id=task.id,
        status=task.status,
        control_state=task.control_state,
        total_cases=total,
        finished_cases=finished,
        progress=round(finished / total * 100, 2) if total else 0.0,
        status_distribution=distribution,
        pass_rate=round(passed / finished * 100, 2) if finished else 0.0,
        avg_ttft_ms=round(sum(ttfts) / len(ttfts), 2) if ttfts else 0.0,
        avg_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        p95_latency_ms=_percentile(latencies, 0.95),
        avg_turns=round(sum(turns) / len(turns), 2) if turns else 0.0,
        simulator_tokens=simulator_tokens,
        judge_tokens=judge_tokens,
        total_tokens=simulator_tokens + judge_tokens,
        env_retry_count=env_retry_count,
        eta_seconds=eta,
        elapsed_seconds=round(elapsed, 2),
        throughput_per_minute=throughput,
    )


def apply_metrics_to_task(task: BatchTask, metrics: TaskMetrics) -> None:
    task.finished_cases = metrics.finished_cases
    task.pass_count = metrics.status_distribution.PASS
    task.fail_count = metrics.status_distribution.FAIL + metrics.status_distribution.REPETITION_ABORT
    task.error_count = metrics.status_distribution.ERROR
    task.max_turns_count = metrics.status_distribution.MAX_TURNS_EXCEEDED
    task.summary = metrics.model_dump(mode="json")
