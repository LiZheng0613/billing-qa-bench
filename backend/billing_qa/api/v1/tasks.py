"""批量任务接口：创建、启停控制、指标、断点重跑。"""

from __future__ import annotations

import random
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.serializers import task_to_out
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.security import get_current_operator
from billing_qa.db.session import get_db
from billing_qa.models.enums import TaskStatus
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case import CaseExecutionItem
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.task import TaskCreate, TaskMetrics, TaskOut
from billing_qa.services.engine.metrics import apply_metrics_to_task, compute_task_metrics
from billing_qa.services.engine.scheduler import scheduler

router = APIRouter(
    prefix="/tasks", tags=["批量任务"], dependencies=[Depends(get_current_operator)]
)


def _generate_code() -> str:
    return f"BATCH{datetime.now():%Y%m%d%H%M%S}{random.randint(100, 999)}"


async def _get_task(db: AsyncSession, task_id: int) -> BatchTask:
    task = await db.get(BatchTask, task_id)
    if task is None:
        raise NotFoundError("批量任务不存在")
    return task


async def _env_name(db: AsyncSession, env_id: int | None) -> str | None:
    if not env_id:
        return None
    return (
        await db.execute(select(Environment.name).where(Environment.id == env_id))
    ).scalar_one_or_none()


@router.get("", response_model=Page[TaskOut], summary="任务列表")
async def list_tasks(
    status: TaskStatus | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[TaskOut]:
    conditions = []
    if status:
        conditions.append(BatchTask.status == status)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(BatchTask.name.like(like) | BatchTask.code.like(like))
    total = (
        await db.execute(select(func.count()).select_from(BatchTask).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(BatchTask)
            .where(*conditions)
            .order_by(BatchTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    env_names = {
        env_id: name
        for env_id, name in (await db.execute(select(Environment.id, Environment.name))).all()
    }
    return Page[TaskOut](
        items=[task_to_out(item, env_name=env_names.get(item.environment_id or 0)) for item in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=TaskOut, summary="创建批量任务")
async def create_task(
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> TaskOut:
    if payload.case_scope == "ALL_ACTIVE":
        case_ids = list(
            (
                await db.execute(
                    select(TestCase.id)
                    .where(TestCase.deleted_at.is_(None), TestCase.status == "ACTIVE")
                    .order_by(TestCase.id)
                )
            )
            .scalars()
            .all()
        )
        if not case_ids:
            raise ValidationError("用例库中没有启用用例，请先在用例资产中启用后再批量执行")
    else:
        case_ids = list(dict.fromkeys(payload.case_ids))
        if not case_ids:
            raise ValidationError("请至少选择一条用例，或选择“全部启用用例”执行范围")

    cases = (
        await db.execute(
            select(TestCase).where(
                TestCase.id.in_(case_ids), TestCase.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    found = {case.id for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in found]
    if missing:
        raise ValidationError(f"以下用例不存在或已删除: {missing}")
    disabled = [case.code for case in cases if case.status != "ACTIVE"]
    if disabled:
        raise ValidationError(f"以下用例未启用: {', '.join(disabled)}")

    environment_id = payload.environment_id
    if environment_id:
        env = await db.get(Environment, environment_id)
        if env is None or not env.is_active:
            raise ValidationError("指定环境不存在或已停用")

    task = BatchTask(
        code=_generate_code(),
        name=payload.name,
        description=payload.description,
        status=TaskStatus.PENDING,
        control_state="RUNNING",
        environment_id=environment_id,
        case_ids=case_ids,
        total_cases=len(case_ids),
        concurrency=payload.concurrency,
        qps_limit=payload.qps_limit,
        retry_on_env_error=payload.retry_on_env_error,
        max_env_retries=payload.max_env_retries,
        retry_backoff_seconds=payload.retry_backoff_seconds,
        created_by=operator,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    if payload.auto_start:
        await scheduler.start(task.id)
        await db.refresh(task)
    return task_to_out(task, env_name=await _env_name(db, task.environment_id))


@router.get("/{task_id}", response_model=TaskOut, summary="任务详情")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await _get_task(db, task_id)
    return task_to_out(task, env_name=await _env_name(db, task.environment_id))


@router.post("/{task_id}/start", response_model=MessageResponse, summary="启动任务")
async def start_task(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await _get_task(db, task_id)
    started = await scheduler.start(task_id)
    if not started:
        raise ConflictError("任务已在运行中")
    return MessageResponse(message="任务已启动")


@router.post("/{task_id}/pause", response_model=MessageResponse, summary="暂停任务")
async def pause_task(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    task = await _get_task(db, task_id)
    if task.status != TaskStatus.RUNNING:
        raise ConflictError("仅运行中的任务可以暂停")
    await scheduler.pause(task_id)
    return MessageResponse(message="任务已暂停")


@router.post("/{task_id}/resume", response_model=MessageResponse, summary="恢复任务")
async def resume_task(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    task = await _get_task(db, task_id)
    if task.status not in {TaskStatus.PAUSED, TaskStatus.RUNNING}:
        raise ConflictError("仅暂停中的任务可以恢复")
    await scheduler.resume(task_id)
    return MessageResponse(message="任务已恢复")


@router.post("/{task_id}/abort", response_model=MessageResponse, summary="终止任务")
async def abort_task(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    task = await _get_task(db, task_id)
    if task.status in {TaskStatus.COMPLETED, TaskStatus.ABORTED, TaskStatus.FAILED}:
        raise ConflictError("任务已结束，无需终止")
    await scheduler.abort(task_id)
    return MessageResponse(message="终止指令已下发")


@router.post(
    "/{task_id}/retry-failed", response_model=MessageResponse, summary="失败用例断点重跑"
)
async def retry_failed(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    task = await _get_task(db, task_id)
    if scheduler.is_running(task_id):
        raise ConflictError("任务正在运行，无法发起重跑")
    failed = (
        await db.execute(
            select(func.count())
            .select_from(ExecutionRecord)
            .where(
                ExecutionRecord.task_id == task_id,
                ExecutionRecord.status.in_(
                    ["FAIL", "ERROR", "MAX_TURNS_EXCEEDED", "REPETITION_ABORT", "ABORTED"]
                ),
            )
        )
    ).scalar_one()
    if not failed:
        raise ConflictError("没有可重跑的失败/异常用例")
    task.status = TaskStatus.RUNNING
    task.control_state = "RUNNING"
    task.error_message = None
    task.finished_at = None
    await db.commit()
    await scheduler.start(task_id, retry_failed=True)
    return MessageResponse(message=f"已提交 {failed} 条历史失败记录的重跑（按用例取最新一次）")


@router.get("/{task_id}/metrics", response_model=TaskMetrics, summary="任务实时指标")
async def task_metrics(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskMetrics:
    task = await _get_task(db, task_id)
    metrics = await compute_task_metrics(db, task)
    apply_metrics_to_task(task, metrics)
    await db.commit()
    return metrics


@router.get(
    "/{task_id}/executions",
    response_model=Page[CaseExecutionItem],
    summary="任务执行明细",
)
async def task_executions(
    task_id: int,
    status: str | None = None,
    latest_per_case: bool = Query(
        default=False, description="是否按用例只返回最新一次尝试（批量任务详情用）"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CaseExecutionItem]:
    await _get_task(db, task_id)
    attempt_counts: dict[int, int] = {}

    if latest_per_case:
        latest_ids = (
            select(func.max(ExecutionRecord.id).label("max_id"))
            .where(ExecutionRecord.task_id == task_id)
            .group_by(ExecutionRecord.case_id)
            .subquery()
        )
        conditions = [ExecutionRecord.id.in_(select(latest_ids.c.max_id))]
        if status:
            conditions.append(ExecutionRecord.status == status)
    else:
        conditions = [ExecutionRecord.task_id == task_id]
        if status:
            conditions.append(ExecutionRecord.status == status)

    total = (
        await db.execute(select(func.count()).select_from(ExecutionRecord).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ExecutionRecord)
            .where(*conditions)
            .order_by(ExecutionRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    if latest_per_case and rows:
        count_rows = (
            await db.execute(
                select(ExecutionRecord.case_id, func.count())
                .where(
                    ExecutionRecord.task_id == task_id,
                    ExecutionRecord.case_id.in_({record.case_id for record in rows}),
                )
                .group_by(ExecutionRecord.case_id)
            )
        ).all()
        attempt_counts = {int(case_id): int(count) for case_id, count in count_rows}

    case_map = {
        case.id: (case.code, case.name)
        for case in (
            await db.execute(
                select(TestCase).where(TestCase.id.in_({record.case_id for record in rows}))
            )
        ).scalars().all()
    }
    items = [
        CaseExecutionItem(
            id=record.id,
            task_id=record.task_id,
            case_id=record.case_id,
            case_code=case_map.get(record.case_id, (None, None))[0],
            case_name=case_map.get(record.case_id, (None, None))[1],
            source=record.source,
            status=record.status,
            attempt_no=record.attempt_no,
            attempt_count=attempt_counts.get(record.case_id, 1),
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
        for record in rows
    ]
    return Page[CaseExecutionItem](
        items=items, total=int(total), page=page, page_size=page_size
    )
