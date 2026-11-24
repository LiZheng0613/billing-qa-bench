"""执行记录与全链路 Trace 查询接口。"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_qa.api.v1.serializers import (
    EXECUTION_ITEM_COLUMNS,
    build_execution_detail,
    execution_to_item,
)
from billing_qa.core.authz import require_module_access
from billing_qa.core.exceptions import NotFoundError
from billing_qa.db.session import get_db
from billing_qa.models.enums import ExecutionSource
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import Page
from billing_qa.schemas.dashboard import (
    ExecutionDailyPoint,
    ExecutionEnvironmentMetric,
    ExecutionEnvironmentSummary,
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
    # 列表序列化只用标量列（见 EXECUTION_ITEM_COLUMNS），按列裁剪避免整行
    # 加载 case_snapshot / judge_result 等大 JSON 列。
    rows = (
        await db.execute(
            select(
                *EXECUTION_ITEM_COLUMNS,
                BatchTask.name.label("task_name"),
                TestCase.code.label("case_code"),
                TestCase.name.label("case_name"),
            )
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
            row,
            task_name=row.task_name,
            case_code=row.case_code,
            case_name=row.case_name,
        )
        for row in rows
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

    # 最近秩法 P95 与 _percentile 的 ceil(p/100*n)-1 取位一致，AVG 忽略 NULL
    # 的口径也与原 Python 相同——聚合下推避免把整个窗口的行拉进内存。
    latency_row = (
        await db.execute(
            select(
                func.percentile_disc(0.95)
                .within_group(ExecutionRecord.latency_ms)
                .label("p95_ms"),
                func.avg(ExecutionRecord.latency_ms).label("avg_ms"),
                func.avg(ExecutionRecord.ttft_ms).label("avg_ttft_ms"),
            ).where(*window_conditions)
        )
    ).one()
    p95_raw = latency_row.p95_ms
    p95_ms = round(float(p95_raw), 1) if p95_raw is not None else None
    avg_ms = round(float(latency_row.avg_ms), 1) if latency_row.avg_ms is not None else None
    avg_ttft_ms = (
        round(float(latency_row.avg_ttft_ms), 1)
        if latency_row.avg_ttft_ms is not None
        else None
    )

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
            p95_ms=p95_ms,
            avg_ms=avg_ms,
            avg_ttft_ms=avg_ttft_ms,
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


@router.get(
    "/environment-summary",
    response_model=ExecutionEnvironmentSummary,
    summary="按执行环境横向对比",
)
async def execution_environment_summary(
    days: int = Query(default=7, ge=1, le=90, description="对比窗口天数"),
    db: AsyncSession = Depends(get_db),
) -> ExecutionEnvironmentSummary:
    """同一口径下按执行环境汇总正确率、稳定性与效率，用于比较调优思路。

    正确率：``pass_flag`` 口径的尝试级通过率，并给出按用例去重的覆盖率。
    稳定性：每日通过率波动（标准差）、抖动用例占比、执行异常率。
    效率：P95 / 平均时延、平均轮次与 Token。
    """

    now = datetime.now()
    first_day = now.date() - timedelta(days=days - 1)
    since = datetime.combine(first_day, time.min)
    previous_since = since - timedelta(days=days)
    window_conditions = (
        ExecutionRecord.started_at >= since,
        ExecutionRecord.source != ExecutionSource.SANDBOX,
    )

    env_rows = (
        await db.execute(
            select(
                Environment.id,
                Environment.code,
                Environment.name,
                Environment.is_active,
                Environment.last_health_status,
                Environment.last_health_latency_ms,
                Environment.last_health_at,
            ).order_by(Environment.id)
        )
    ).all()

    current_rows = (
        await db.execute(
            select(
                ExecutionRecord.environment_id.label("environment_id"),
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
                func.sum(case((ExecutionRecord.pass_flag.is_(False), 1), else_=0)).label(
                    "failed"
                ),
                func.sum(
                    case((ExecutionRecord.status == "ERROR", 1), else_=0)
                ).label("error_total"),
                func.avg(ExecutionRecord.total_score).label("avg_score"),
                func.avg(ExecutionRecord.turns_used).label("avg_turns"),
                func.sum(
                    func.coalesce(ExecutionRecord.simulator_tokens, 0)
                    + func.coalesce(ExecutionRecord.judge_tokens, 0)
                ).label("tokens"),
                func.max(ExecutionRecord.started_at).label("last_executed_at"),
            )
            .where(*window_conditions)
            .group_by(ExecutionRecord.environment_id)
        )
    ).all()
    current_map = {row.environment_id: row for row in current_rows}

    previous_rows = (
        await db.execute(
            select(
                ExecutionRecord.environment_id.label("environment_id"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            )
            .where(
                ExecutionRecord.started_at >= previous_since,
                ExecutionRecord.started_at < since,
                ExecutionRecord.source != ExecutionSource.SANDBOX,
            )
            .group_by(ExecutionRecord.environment_id)
        )
    ).all()
    previous_map = {row.environment_id: row for row in previous_rows}

    day_col = func.date(ExecutionRecord.started_at)
    daily_rows = (
        await db.execute(
            select(
                ExecutionRecord.environment_id.label("environment_id"),
                day_col.label("day"),
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            )
            .where(*window_conditions)
            .group_by(ExecutionRecord.environment_id, day_col)
        )
    ).all()
    daily_map: dict[tuple[int | None, str], Any] = {
        (row.environment_id, str(row.day)): row for row in daily_rows
    }

    # 最近秩法 P95 与 _percentile 取位一致、AVG 忽略 NULL——按环境 GROUP BY
    # 下推聚合，避免把窗口内全部执行的时延行拉进内存。
    latency_rows = (
        await db.execute(
            select(
                ExecutionRecord.environment_id.label("environment_id"),
                func.percentile_disc(0.95)
                .within_group(ExecutionRecord.latency_ms)
                .label("p95_ms"),
                func.avg(ExecutionRecord.latency_ms).label("avg_ms"),
                func.avg(ExecutionRecord.ttft_ms).label("avg_ttft_ms"),
            )
            .where(*window_conditions)
            .group_by(ExecutionRecord.environment_id)
        )
    ).all()
    latency_map: dict[int | None, Any] = {row.environment_id: row for row in latency_rows}

    case_rows = (
        await db.execute(
            select(
                ExecutionRecord.environment_id.label("environment_id"),
                ExecutionRecord.case_id,
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
                func.sum(case((ExecutionRecord.pass_flag.is_(False), 1), else_=0)).label(
                    "failed"
                ),
            )
            .where(*window_conditions, ExecutionRecord.pass_flag.is_not(None))
            .group_by(ExecutionRecord.environment_id, ExecutionRecord.case_id)
        )
    ).all()
    case_stats: dict[int | None, dict[str, int]] = {}
    for row in case_rows:
        stats = case_stats.setdefault(
            row.environment_id,
            {"total": 0, "passed": 0, "flaky": 0},
        )
        stats["total"] += 1
        if int(row.passed or 0) > 0:
            stats["passed"] += 1
        if int(row.passed or 0) > 0 and int(row.failed or 0) > 0:
            stats["flaky"] += 1

    reason_rows = (
        await db.execute(
            select(
                ExecutionRecord.environment_id.label("environment_id"),
                ExecutionRecord.status.label("status"),
                func.count(ExecutionRecord.id).label("count"),
            )
            .where(*window_conditions, ExecutionRecord.pass_flag.is_(False))
            .group_by(ExecutionRecord.environment_id, ExecutionRecord.status)
            .order_by(func.count(ExecutionRecord.id).desc())
        )
    ).all()
    reason_map: dict[int | None, list[ExecutionFailureReason]] = {}
    for row in reason_rows:
        reason_map.setdefault(row.environment_id, []).append(
            ExecutionFailureReason(status=str(row.status), count=int(row.count or 0))
        )

    metrics: list[ExecutionEnvironmentMetric] = []

    def build_metric(
        environment_id: int | None,
        code: str,
        name: str,
        is_active: bool,
        last_health_status: str | None = None,
        last_health_latency_ms: float | None = None,
        last_health_at: datetime | None = None,
    ) -> ExecutionEnvironmentMetric:
        current = current_map.get(environment_id)
        measured = int(current.measured or 0) if current else 0
        passed = int(current.passed or 0) if current else 0
        total = int(current.total or 0) if current else 0
        previous = previous_map.get(environment_id)
        previous_measured = int(previous.measured or 0) if previous else 0
        previous_passed = int(previous.passed or 0) if previous else 0

        daily: list[ExecutionDailyPoint] = []
        rates: list[float] = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            key = day.isoformat()
            item = daily_map.get((environment_id, key))
            day_measured = int(item.measured or 0) if item else 0
            day_passed = int(item.passed or 0) if item else 0
            rate = round(day_passed / day_measured * 100, 2) if day_measured else None
            if rate is not None:
                rates.append(rate)
            daily.append(
                ExecutionDailyPoint(
                    date=key,
                    total=int(item.total or 0) if item else 0,
                    measured=day_measured,
                    passed=day_passed,
                    pass_rate=rate,
                )
            )

        volatility: float | None = None
        if len(rates) >= 2:
            mean = sum(rates) / len(rates)
            variance = sum((rate - mean) ** 2 for rate in rates) / len(rates)
            volatility = round(math.sqrt(variance), 1)

        stats = case_stats.get(environment_id)
        case_total = int(stats["total"]) if stats else 0
        case_passed = int(stats["passed"]) if stats else 0
        flaky = int(stats["flaky"]) if stats else 0
        latency_stats = latency_map.get(environment_id)
        error_total = int(current.error_total or 0) if current else 0
        turns = float(current.avg_turns) if current and current.avg_turns is not None else None
        score = float(current.avg_score) if current and current.avg_score is not None else None

        return ExecutionEnvironmentMetric(
            environment_id=environment_id,
            code=code,
            name=name,
            is_active=is_active,
            last_health_status=last_health_status,
            last_health_latency_ms=last_health_latency_ms,
            last_health_at=last_health_at,
            last_executed_at=current.last_executed_at if current else None,
            total=total,
            measured=measured,
            passed=passed,
            failed=int(current.failed or 0) if current else 0,
            pass_rate=round(passed / measured * 100, 2) if measured else None,
            previous_pass_rate=(
                round(previous_passed / previous_measured * 100, 2)
                if previous_measured
                else None
            ),
            case_total=case_total,
            case_passed=case_passed,
            case_pass_rate=(
                round(case_passed / case_total * 100, 2) if case_total else None
            ),
            flaky_cases=flaky,
            flaky_rate=round(flaky / case_total * 100, 2) if case_total else None,
            error_total=error_total,
            error_rate=round(error_total / total * 100, 2) if total else None,
            avg_score=round(score, 1) if score is not None else None,
            avg_turns=round(turns, 1) if turns is not None else None,
            p95_ms=(
                round(float(latency_stats.p95_ms), 1)
                if latency_stats and latency_stats.p95_ms is not None
                else None
            ),
            avg_ms=(
                round(float(latency_stats.avg_ms), 1)
                if latency_stats and latency_stats.avg_ms is not None
                else None
            ),
            avg_ttft_ms=(
                round(float(latency_stats.avg_ttft_ms), 1)
                if latency_stats and latency_stats.avg_ttft_ms is not None
                else None
            ),
            tokens=int(current.tokens or 0) if current else 0,
            volatility=volatility,
            daily=daily,
            failure_reasons=reason_map.get(environment_id, []),
        )

    for env in env_rows:
        current = current_map.get(env.id)
        # 停用且窗口内没有执行的环境不占对比位；启用环境即使无数据也保留，便于发现「还没跑」。
        if not env.is_active and (current is None or int(current.total or 0) == 0):
            continue
        metrics.append(
            build_metric(
                environment_id=int(env.id),
                code=str(env.code),
                name=str(env.name),
                is_active=bool(env.is_active),
                last_health_status=env.last_health_status,
                last_health_latency_ms=(
                    float(env.last_health_latency_ms)
                    if env.last_health_latency_ms is not None
                    else None
                ),
                last_health_at=env.last_health_at,
            )
        )

    if None in current_map:
        metrics.append(
            build_metric(environment_id=None, code="UNBOUND", name="未绑定环境", is_active=True)
        )

    metrics.sort(
        key=lambda item: (
            -item.measured,
            item.pass_rate is None,
            -(item.pass_rate or 0),
            item.name,
        )
    )
    return ExecutionEnvironmentSummary(days=days, since=since, environments=metrics)


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
    # 只需要 code/name；避免整行加载 TestCase 的大列。
    case_code = case_name = None
    if record.case_id:
        case_row = (
            await db.execute(
                select(TestCase.code, TestCase.name).where(TestCase.id == record.case_id)
            )
        ).one_or_none()
        if case_row is not None:
            case_code, case_name = case_row
    return await build_execution_detail(
        db,
        record,
        task_name=task_name,
        case_code=case_code,
        case_name=case_name,
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
