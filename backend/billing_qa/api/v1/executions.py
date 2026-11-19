"""执行记录与全链路 Trace 查询接口。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_qa.api.v1.serializers import build_execution_detail, execution_to_item
from billing_qa.core.authz import require_module_access
from billing_qa.core.exceptions import NotFoundError
from billing_qa.db.session import get_db
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import Page
from billing_qa.schemas.task import ExecutionDetail, ExecutionListItem, ExecutionPassRate

router = APIRouter(
    prefix="/executions",
    tags=["执行追踪"],
    dependencies=[
        Depends(
            require_module_access(
                "execution:read",
                ("execution:retry", "execution:export"),
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
    """按 started_at 统计最近 N 小时内的执行通过率，仅统计已判定（pass_flag 非空）的执行。"""
    since = datetime.now() - timedelta(hours=hours)
    row = (
        await db.execute(
            select(
                func.count(ExecutionRecord.id).label("total"),
                func.count(ExecutionRecord.pass_flag).label("measured"),
                func.sum(case((ExecutionRecord.pass_flag.is_(True), 1), else_=0)).label(
                    "passed"
                ),
            ).where(ExecutionRecord.started_at >= since)
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
