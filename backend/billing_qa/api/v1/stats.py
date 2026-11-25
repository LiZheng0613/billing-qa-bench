"""事实级统计与数据集概览（只读）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_module_access
from billing_qa.core.config import settings
from billing_qa.core.schedule_time import normalize_local
from billing_qa.db.session import get_db
from billing_qa.models.enums import ExecutionSource
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.gold_query import FactResult
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.ops import DatasetStatItem, FactStatItem, FactStatsOverview

router = APIRouter(
    prefix="/stats",
    tags=["统计"],
    dependencies=[Depends(require_module_access("stats:read", ("stats:read",)))],
)


def _window_start(since: datetime | None, all_time: bool) -> datetime | None:
    """默认时间窗：未显式指定起点且未要求全史时，统计近 N 天（可配置）。

    ``since`` 可能由前端 ``toISOString()`` 传入（带 UTC 时区），而库内业务时间列是
    ``TIMESTAMP WITHOUT TIME ZONE`` 的服务器本地时间，比较前需统一为 naive。
    """
    if since is not None or all_time:
        return normalize_local(since)
    return datetime.now() - timedelta(days=settings.stats_default_window_days)


def _rate(passed: int, total: int) -> float:
    return round(passed / total * 100, 2) if total else 0.0


def _fact_rows(stmt_rows: list[Any]) -> list[FactStatItem]:
    items = [
        FactStatItem(
            fact_id=str(row.fact_id),
            label=row.label,
            total=int(row.total or 0),
            passed=int(row.passed or 0),
            pass_rate=_rate(int(row.passed or 0), int(row.total or 0)),
            last_run_at=row.last_run_at,
        )
        for row in stmt_rows
    ]
    items.sort(key=lambda item: (item.pass_rate, -item.total))
    return items


@router.get("/facts", response_model=list[FactStatItem], summary="事实级通过率")
async def fact_stats(
    case_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    all_time: bool = Query(
        default=False, description="忽略默认时间窗，统计全部历史"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[FactStatItem]:
    conditions = [
        # 草稿试跑（SANDBOX）只是调试实验，不纳入事实通过率统计
        ExecutionRecord.source != ExecutionSource.SANDBOX
    ]
    if case_id:
        conditions.append(ExecutionRecord.case_id == case_id)
    since = _window_start(since, all_time)
    until = normalize_local(until)
    if since:
        conditions.append(FactResult.created_at >= since)
    if until:
        conditions.append(FactResult.created_at <= until)
    stmt = (
        select(
            FactResult.fact_id.label("fact_id"),
            FactResult.label.label("label"),
            func.count(FactResult.id).label("total"),
            func.sum(case((FactResult.passed.is_(True), 1), else_=0)).label("passed"),
            func.max(FactResult.created_at).label("last_run_at"),
        )
        .join(ExecutionRecord, ExecutionRecord.id == FactResult.execution_id)
        .where(*conditions)
        .group_by(FactResult.fact_id, FactResult.label)
    )
    rows = (await db.execute(stmt)).all()
    return _fact_rows(list(rows))


@router.get("/overview", response_model=FactStatsOverview, summary="契约执行与事实总览")
async def stats_overview(
    since: datetime | None = None,
    until: datetime | None = None,
    all_time: bool = Query(
        default=False, description="忽略默认时间窗，统计全部历史"
    ),
    db: AsyncSession = Depends(get_db),
) -> FactStatsOverview:
    window = _window_start(since, all_time)
    until = normalize_local(until)
    exec_conditions = [
        ExecutionRecord.grading_mode == "ANSWER_CONTRACT",
        ExecutionRecord.source != ExecutionSource.SANDBOX,
    ]
    if window:
        # ExecutionRecord 的业务时间列是 started_at（带索引 ix_execution_started）
        exec_conditions.append(ExecutionRecord.started_at >= window)
    if until:
        exec_conditions.append(ExecutionRecord.started_at <= until)
    exec_row = (
        await db.execute(
            select(
                func.count(ExecutionRecord.id).label("total"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
                func.sum(case((ExecutionRecord.lucky_pass.is_(True), 1), else_=0)).label(
                    "lucky"
                ),
                func.avg(ExecutionRecord.total_score).label("avg_score"),
            ).where(*exec_conditions)
        )
    ).one()
    fact_conditions = [
        # 沙盒试跑不进入事实统计口径（与 fact_stats / 执行总览保持一致）。
        ExecutionRecord.source != ExecutionSource.SANDBOX
    ]
    if window:
        fact_conditions.append(FactResult.created_at >= window)
    if until:
        fact_conditions.append(FactResult.created_at <= until)
    fact_row = (
        await db.execute(
            select(
                func.count(FactResult.id).label("total"),
                func.sum(case((FactResult.passed.is_(True), 1), else_=0)).label("passed"),
            )
            .join(ExecutionRecord, ExecutionRecord.id == FactResult.execution_id)
            .where(*fact_conditions)
        )
    ).one()

    all_facts = (
        await db.execute(
            select(
                FactResult.fact_id.label("fact_id"),
                FactResult.label.label("label"),
                func.count(FactResult.id).label("total"),
                func.sum(case((FactResult.passed.is_(True), 1), else_=0)).label("passed"),
                func.max(FactResult.created_at).label("last_run_at"),
            )
            .join(ExecutionRecord, ExecutionRecord.id == FactResult.execution_id)
            .where(*fact_conditions)
            .group_by(FactResult.fact_id, FactResult.label)
        )
    ).all()
    ranked = _fact_rows(list(all_facts))
    top_failed = [item for item in ranked if item.passed < item.total][:5]

    execution_total = int(exec_row.total or 0)
    passed = int(exec_row.passed or 0)
    fact_total = int(fact_row.total or 0)
    fact_passed = int(fact_row.passed or 0)
    return FactStatsOverview(
        execution_total=execution_total,
        passed=passed,
        pass_rate=_rate(passed, execution_total),
        lucky_pass=int(exec_row.lucky or 0),
        avg_score=round(float(exec_row.avg_score or 0.0), 2),
        fact_total=fact_total,
        fact_passed=fact_passed,
        fact_pass_rate=_rate(fact_passed, fact_total),
        top_failed=top_failed,
    )


@router.get("/datasets", response_model=list[DatasetStatItem], summary="数据集分布")
async def dataset_stats(db: AsyncSession = Depends(get_db)) -> list[DatasetStatItem]:
    rows = (
        await db.execute(
            select(
                TestCase.dataset_version,
                TestCase.grading_mode,
                TestCase.review_status,
                func.count(TestCase.id).label("total"),
            )
            .where(TestCase.deleted_at.is_(None))
            .group_by(
                TestCase.dataset_version,
                TestCase.grading_mode,
                TestCase.review_status,
            )
            .order_by(TestCase.dataset_version.desc().nullslast())
        )
    ).all()
    return [
        DatasetStatItem(
            dataset_version=row.dataset_version,
            grading_mode=row.grading_mode,
            review_status=row.review_status,
            total=int(row.total or 0),
        )
        for row in rows
    ]
