"""批量任务接口：创建、启停控制、指标、断点重跑。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.serializers import task_to_out
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.schedule_time import normalize_local, normalize_weekdays, parse_time_text
from billing_qa.core.security import get_current_operator
from billing_qa.db.base import now
from billing_qa.db.session import get_db
from billing_qa.models.enums import TaskScheduleType, TaskStatus
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case import CaseExecutionItem
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.task import TaskCreate, TaskMetrics, TaskOut, TaskScheduleUpdate
from billing_qa.services.engine.metrics import apply_metrics_to_task, compute_task_metrics
from billing_qa.services.engine.schedule import (
    clone_for_run,
    generate_task_code,
    initial_next_run,
)
from billing_qa.services.engine.scheduler import scheduler

router = APIRouter(
    prefix="/tasks", tags=["批量任务"], dependencies=[Depends(get_current_operator)]
)


def _generate_code() -> str:
    return generate_task_code()


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
    schedule_type: TaskScheduleType | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[TaskOut]:
    conditions = []
    if status:
        conditions.append(BatchTask.status == status)
    if schedule_type:
        conditions.append(BatchTask.schedule_type == schedule_type.value)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(BatchTask.name.ilike(like) | BatchTask.code.ilike(like))
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
        status=(
            TaskStatus.PENDING
            if payload.schedule_type == TaskScheduleType.IMMEDIATE
            else TaskStatus.SCHEDULED
        ),
        control_state="RUNNING",
        environment_id=environment_id,
        case_ids=case_ids,
        total_cases=len(case_ids),
        concurrency=payload.concurrency,
        qps_limit=payload.qps_limit,
        retry_on_env_error=payload.retry_on_env_error,
        max_env_retries=payload.max_env_retries,
        retry_backoff_seconds=payload.retry_backoff_seconds,
        schedule_type=payload.schedule_type.value,
        scheduled_at=payload.scheduled_at,
        schedule_time=payload.schedule_time,
        schedule_weekdays=payload.schedule_weekdays or None,
        schedule_active=True,
        next_run_at=initial_next_run(
            schedule_type=payload.schedule_type.value,
            scheduled_at=payload.scheduled_at,
            schedule_time=payload.schedule_time,
            schedule_weekdays=payload.schedule_weekdays,
        ),
        created_by=operator,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    # 定时/周期计划注册后由后台派发；只有立即执行才在创建时启动
    if payload.auto_start and payload.schedule_type == TaskScheduleType.IMMEDIATE:
        await scheduler.start(task.id)
        await db.refresh(task)
    return task_to_out(task, env_name=await _env_name(db, task.environment_id))


@router.get("/{task_id}", response_model=TaskOut, summary="任务详情")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await _get_task(db, task_id)
    return task_to_out(task, env_name=await _env_name(db, task.environment_id))


def _is_plan(task: BatchTask) -> bool:
    return task.schedule_type != TaskScheduleType.IMMEDIATE.value


async def _trigger_plan(db: AsyncSession, task: BatchTask) -> BatchTask:
    """立即执行一次计划：一次性计划用自身，周期计划克隆新任务。"""
    if task.schedule_type == TaskScheduleType.SCHEDULED.value:
        if not task.schedule_active and task.status not in {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
        }:
            raise ConflictError("该一次性计划已执行过，请重新创建或调整计划时间")
        task.schedule_active = False
        task.next_run_at = None
        task.last_run_at = now()
        task.status = TaskStatus.PENDING
        task.control_state = "RUNNING"
        await db.commit()
        await scheduler.start(task.id)
        return task

    run = clone_for_run(task)
    db.add(run)
    await db.flush()
    task.last_run_at = now()
    await db.commit()
    await db.refresh(run)
    await scheduler.start(run.id)
    return run


@router.post("/{task_id}/start", response_model=MessageResponse, summary="启动任务")
async def start_task(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    task = await _get_task(db, task_id)
    if _is_plan(task):
        run = await _trigger_plan(db, task)
        return MessageResponse(message=f"已按计划立即执行一次：{run.code}")
    started = await scheduler.start(task_id)
    if not started:
        raise ConflictError("任务已在运行中")
    return MessageResponse(message="任务已启动")


@router.post(
    "/{task_id}/run-now", response_model=TaskOut, summary="立即执行一次定时计划"
)
async def run_task_now(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await _get_task(db, task_id)
    if _is_plan(task):
        run = await _trigger_plan(db, task)
    else:
        if not await scheduler.start(task.id):
            raise ConflictError("任务已在运行中")
        run = task
    await db.refresh(run)
    return task_to_out(run, env_name=await _env_name(db, run.environment_id))


@router.put("/{task_id}/schedule", response_model=TaskOut, summary="调整定时计划")
async def update_schedule(
    task_id: int,
    payload: TaskScheduleUpdate,
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await _get_task(db, task_id)
    if not _is_plan(task):
        raise ConflictError("当前任务为立即执行，请新建任务时选择定时执行方式")
    if scheduler.is_running(task.id):
        raise ConflictError("任务正在运行，无法调整计划")

    schedule_type = (
        payload.schedule_type.value if payload.schedule_type else task.schedule_type
    )
    scheduled_at = task.scheduled_at
    schedule_time = task.schedule_time
    weekdays = list(task.schedule_weekdays or [])

    if schedule_type == TaskScheduleType.SCHEDULED.value:
        moment = normalize_local(payload.scheduled_at) or (
            task.scheduled_at if payload.scheduled_at is None else None
        )
        if moment is None:
            raise ValidationError("指定时间执行必须填写执行时间")
        if moment <= now():
            raise ValidationError("指定执行时间必须晚于当前时间")
        scheduled_at, schedule_time, weekdays = moment, None, []
    else:
        if payload.schedule_time is not None:
            schedule_time = payload.schedule_time
        if not schedule_time:
            raise ValidationError("周期执行必须填写执行时刻")
        try:
            parse_time_text(schedule_time)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        scheduled_at = None
        if schedule_type == TaskScheduleType.WEEKLY:
            if payload.schedule_weekdays is not None:
                weekdays = payload.schedule_weekdays
            try:
                weekdays = normalize_weekdays(weekdays)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        else:
            weekdays = []

    task.schedule_type = schedule_type
    task.scheduled_at = scheduled_at
    task.schedule_time = schedule_time
    task.schedule_weekdays = weekdays or None
    task.schedule_active = payload.active if payload.active is not None else True
    if task.schedule_active:
        task.next_run_at = initial_next_run(
            schedule_type=schedule_type,
            scheduled_at=scheduled_at,
            schedule_time=schedule_time,
            schedule_weekdays=weekdays,
        )
        task.status = TaskStatus.SCHEDULED
        task.control_state = "RUNNING"
        task.finished_at = None
        task.error_message = None
    else:
        task.next_run_at = None
        if task.status == TaskStatus.SCHEDULED:
            task.status = TaskStatus.ABORTED
            task.control_state = "ABORTED"
    await db.commit()
    await db.refresh(task)
    return task_to_out(task, env_name=await _env_name(db, task.environment_id))


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
    # 定时计划：还没开跑时直接停掉后续触发，不进入调度器
    if _is_plan(task) and task.status == TaskStatus.SCHEDULED:
        task.schedule_active = False
        task.next_run_at = None
        task.status = TaskStatus.ABORTED
        task.control_state = "ABORTED"
        task.finished_at = now()
        await db.commit()
        return MessageResponse(message="定时计划已停止，不再触发后续执行")
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
