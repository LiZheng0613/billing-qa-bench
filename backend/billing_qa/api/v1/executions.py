"""执行记录与全链路 Trace 查询接口。"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_qa.api.v1.serializers import build_execution_detail, execution_to_item
from billing_qa.core.authz import require_module_access
from billing_qa.core.exceptions import NotFoundError
from billing_qa.db.session import get_db
from billing_qa.models.enums import ExecutionSource
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import Page
from billing_qa.schemas.dashboard import (
    ExecutionDailyPoint,
    ExecutionFailedCase,
    ExecutionFailureReason,
    ExecutionLatencySummary,
    ExecutionSourceSummary,
    ExecutionSummary,
    ExecutionTokenSummary,
    ExecutionWindowSummary,
)
from billing_qa.schemas.task import ExecutionDetail, ExecutionListItem, ExecutionPassRate

router = APIRouter(
    prefix="/executions",
    tags=["执行追踪"],
    dependencies=[
        Depends(
            require_module_access(
                "execution:read",
                ("execution:retry",),
            )
        )
    ],
)


@router.get("", response_model=Page[ExecutionListItem], summary="执行记录列表")
async def list_executions(
    case_id: int | None = None,
    task_id: int | None = None,
    status: str | None = None,
    source: str | None = None,
    pass_flag: bool | None = None,
    hours: int | None = Query(default=None, ge=1, le=24 * 30, description="限定最近 N 小时"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[ExecutionListItem]:
    conditions = []
    if case_id:
        conditions.append(ExecutionRecord.case_id == case_id)
    if task_id:
        conditions.append(ExecutionRecord.task_id == task_id)
    if status:
        conditions.append(ExecutionRecord.status == status)
    if source:
        conditions.append(ExecutionRecord.source == source)
    if pass_flag is not None:
        conditions.append(ExecutionRecord.pass_flag.is_(pass_flag))
    if hours:
        conditions.append(ExecutionRecord.started_at >= datetime.now() - timedelta(hours=hours))
    total = (
        await db.execute(select(func.count()).select_from(ExecutionRecord).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ExecutionRecord, BatchTask.name, TestCase.code, TestCase.name)
            .outerjoin(BatchTask, BatchTask.id == ExecutionRecord.task_id)
            .outerjoin(TestCase, TestCase.id == ExecutionRecord.case_id)
            .where(*conditions)
            .order_by(ExecutionRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        execution_to_item(
            record, task_name=task_name, case_code=case_code, case_name=case_name
        )
        for record, task_name, case_code, case_name in rows
    ]
    return Page[ExecutionListItem](
        items=items, total=int(total), page=page, page_size=page_size
    )


@router.get(
    "/pass-rate",
    response_model=ExecutionPassRate,
    summary="时间窗内的执行通过率",
)
async def execution_pass_rate(
    hours: int = Query(default=24, ge=1, le=24 * 30, description="统计窗口（小时）"),
    db: AsyncSession = Depends(get_db),
) -> ExecutionPassRate:
    """按 started_at 统计最近 N 小时内的执行通过率，仅统计已判定（pass_flag 非空）的执行。

    沙盒试跑（SANDBOX）是作者侧草稿验证，不代表真实测试结果，不纳入通过率口径；
    单条调试（DEBUG）仍是正式的测试执行，保留统计。
    """
    since = datetime.now() - timedelta(hours=hours)
    row = (
        await db.execute(
            select(
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            ).where(
                ExecutionRecord.started_at >= since,
                ExecutionRecord.source != ExecutionSource.SANDBOX,
            )
        )
    ).one()
    measured = int(row.measured or 0)
    passed = int(row.passed or 0)
    return ExecutionPassRate(
        window_hours=hours,
        total=int(row.total or 0),
        measured=measured,
        passed=passed,
        pass_rate=round(passed / measured * 100, 2) if measured else None,
    )


@router.get(
    "/summary",
    response_model=ExecutionSummary,
    summary="大盘执行聚合",
)
async def execution_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30, description="主窗口（小时）"),
    days: int = Query(default=7, ge=1, le=30, description="趋势天数"),
    db: AsyncSession = Depends(get_db),
) -> ExecutionSummary:
    """大盘首屏所需的执行聚合：窗口通过率、环比、时延、Token、每日趋势、来源与失败聚焦。

    口径与 ``/executions/pass-rate`` 一致：按 ``started_at`` 归窗，排除沙盒试跑
    （SANDBOX），通过率只统计已判定（``pass_flag`` 非空）的执行。
    """
    now = datetime.now()
    since = now - timedelta(hours=hours)
    previous_since = since - timedelta(hours=hours)
    window_conditions = (
        ExecutionRecord.started_at >= since,
        ExecutionRecord.source != ExecutionSource.SANDBOX,
    )

    row = (
        await db.execute(
            select(
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
                func.sum(case((ExecutionRecord.pass_flag.is_(False), 1), else_=0)).label(
                    "failed"
                ),
                func.sum(ExecutionRecord.simulator_tokens).label("simulator_tokens"),
                func.sum(ExecutionRecord.judge_tokens).label("judge_tokens"),
            ).where(*window_conditions)
        )
    ).one()
    measured = int(row.measured or 0)
    passed = int(row.passed or 0)

    previous = (
        await db.execute(
            select(
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            ).where(
                ExecutionRecord.started_at >= previous_since,
                ExecutionRecord.started_at < since,
                ExecutionRecord.source != ExecutionSource.SANDBOX,
            )
        )
    ).one()
    previous_measured = int(previous.measured or 0)
    previous_passed = int(previous.passed or 0)

    latency_rows = (
        await db.execute(
            select(ExecutionRecord.latency_ms, ExecutionRecord.ttft_ms).where(
                *window_conditions
            )
        )
    ).all()
    latencies = [
        float(item.latency_ms) for item in latency_rows if item.latency_ms is not None
    ]
    ttfts = [float(item.ttft_ms) for item in latency_rows if item.ttft_ms is not None]

    today = now.date()
    first_day = today - timedelta(days=days - 1)
    day_col = func.date(ExecutionRecord.started_at)
    daily_rows = (
        await db.execute(
            select(
                day_col.label("day"),
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            )
            .where(
                ExecutionRecord.started_at >= datetime.combine(first_day, time.min),
                ExecutionRecord.source != ExecutionSource.SANDBOX,
            )
            .group_by(day_col)
        )
    ).all()
    daily_map = {str(item.day): item for item in daily_rows}
    daily: list[ExecutionDailyPoint] = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        key = day.isoformat()
        item = daily_map.get(key)
        day_measured = int(item.measured or 0) if item else 0
        day_passed = int(item.passed or 0) if item else 0
        daily.append(
            ExecutionDailyPoint(
                date=key,
                total=int(item.total or 0) if item else 0,
                measured=day_measured,
                passed=day_passed,
                pass_rate=(
                    round(day_passed / day_measured * 100, 2) if day_measured else None
                ),
            )
        )

    source_rows = (
        await db.execute(
            select(
                ExecutionRecord.source.label("source"),
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            )
            .where(*window_conditions)
            .group_by(ExecutionRecord.source)
            .order_by(func.count(ExecutionRecord.id).desc())
        )
    ).all()
    by_source = [
        ExecutionSourceSummary(
            source=str(item.source),
            total=int(item.total or 0),
            measured=int(item.measured or 0),
            passed=int(item.passed or 0),
            pass_rate=(
                round(int(item.passed or 0) / int(item.measured or 1) * 100, 2)
                if int(item.measured or 0)
                else None
            ),
        )
        for item in source_rows
    ]

    reason_rows = (
        await db.execute(
            select(
                ExecutionRecord.status.label("status"),
                func.count(ExecutionRecord.id).label("count"),
            )
            .where(*window_conditions, ExecutionRecord.pass_flag.is_(False))
            .group_by(ExecutionRecord.status)
            .order_by(func.count(ExecutionRecord.id).desc())
        )
    ).all()
    failure_reasons = [
        ExecutionFailureReason(status=str(item.status), count=int(item.count or 0))
        for item in reason_rows
    ]

    failed_case_rows = (
        await db.execute(
            select(
                ExecutionRecord.case_id.label("case_id"),
                TestCase.code.label("case_code"),
                TestCase.name.label("case_name"),
                func.count(ExecutionRecord.id).label("failures"),
                func.max(ExecutionRecord.started_at).label("last_failed_at"),
            )
            .join(TestCase, TestCase.id == ExecutionRecord.case_id)
            .where(*window_conditions, ExecutionRecord.pass_flag.is_(False))
            .group_by(ExecutionRecord.case_id, TestCase.code, TestCase.name)
            .order_by(
                func.count(ExecutionRecord.id).desc(),
                func.max(ExecutionRecord.started_at).desc(),
            )
            .limit(5)
        )
    ).all()
    case_ids = [int(item.case_id) for item in failed_case_rows]
    last_status: dict[int, str] = {}
    if case_ids:
        status_rows = (
            await db.execute(
                select(ExecutionRecord.case_id, ExecutionRecord.status)
                .where(
                    ExecutionRecord.case_id.in_(case_ids),
                    ExecutionRecord.pass_flag.is_(False),
                    ExecutionRecord.source != ExecutionSource.SANDBOX,
                    ExecutionRecord.started_at >= since,
                )
                .order_by(ExecutionRecord.started_at.desc())
            )
        ).all()
        for item in status_rows:
            last_status.setdefault(int(item.case_id), str(item.status))
    top_failed_cases = [
        ExecutionFailedCase(
            case_id=int(item.case_id),
            case_code=item.case_code,
            case_name=item.case_name,
            failures=int(item.failures or 0),
            last_failed_at=item.last_failed_at,
            last_status=last_status.get(int(item.case_id)),
        )
        for item in failed_case_rows
    ]

    return ExecutionSummary(
        window=ExecutionWindowSummary(
            hours=hours,
            total=int(row.total or 0),
            measured=measured,
            passed=passed,
            failed=int(row.failed or 0),
            pass_rate=round(passed / measured * 100, 2) if measured else None,
            previous_pass_rate=(
                round(previous_passed / previous_measured * 100, 2)
                if previous_measured
                else None
            ),
            previous_measured=previous_measured,
        ),
        latency=ExecutionLatencySummary(
            p95_ms=_percentile(latencies, 95),
            avg_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
            avg_ttft_ms=round(sum(ttfts) / len(ttfts), 1) if ttfts else None,
        ),
        tokens=ExecutionTokenSummary(
            simulator=int(row.simulator_tokens or 0),
            judge=int(row.judge_tokens or 0),
        ),
        daily=daily,
        by_source=by_source,
        failure_reasons=failure_reasons,
        top_failed_cases=top_failed_cases,
    )


def _percentile(values: list[float], percent: float) -> float | None:
    """最近秩法百分位，避免依赖数据库方言的分位数函数。"""

    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percent / 100 * len(ordered)) - 1))
    return round(ordered[index], 1)


@router.get("/{execution_id}", response_model=ExecutionDetail, summary="执行全链路详情")
async def get_execution(
    execution_id: int, db: AsyncSession = Depends(get_db)
) -> ExecutionDetail:
    record = (
        await db.execute(
            select(ExecutionRecord)
            .options(
                selectinload(ExecutionRecord.turns), selectinload(ExecutionRecord.judges)
            )
            .where(ExecutionRecord.id == execution_id)
        )
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError("执行记录不存在")
    task_name = await db.scalar(
        select(BatchTask.name).where(BatchTask.id == record.task_id)
    ) if record.task_id else None
    case = await db.get(TestCase, record.case_id)
    return await build_execution_detail(
        db,
        record,
        task_name=task_name,
        case_code=case.code if case else None,
        case_name=case.name if case else None,
    )


@router.get("/{execution_id}/raw", summary="原始请求/响应报文")
async def get_execution_raw(
    execution_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    record = (
        await db.execute(
            select(ExecutionRecord)
            .options(selectinload(ExecutionRecord.turns))
            .where(ExecutionRecord.id == execution_id)
        )
    ).scalar_one_or_none()
    if record is None:
        raise NotFoundError("执行记录不存在")
    return {
        "execution_id": record.id,
        "trace_uid": record.trace_uid,
        "case_snapshot": record.case_snapshot,
        "turns": [
            {
                "turn": turn.turn_index,
                "request": turn.request_payload,
                "response": turn.response_raw,
                "judge_prompt": None,
            }
            for turn in record.turns
        ],
        "judges": [
            {"model": judge.model_name, "prompt": judge.judge_prompt}
            for judge in record.judges
        ],
    }
