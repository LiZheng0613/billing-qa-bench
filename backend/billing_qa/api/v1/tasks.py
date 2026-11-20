"""批量任务接口：创建、启停控制、指标、断点重跑、结果导出。"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.serializers import task_to_out
from billing_qa.core.authz import require_module_access, require_permission
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.schedule_time import normalize_local, normalize_weekdays, parse_time_text
from billing_qa.core.search import keyword_condition
from billing_qa.core.security import get_current_operator
from billing_qa.db.base import now
from billing_qa.db.session import get_db
from billing_qa.models.enums import TaskScheduleType, TaskStatus
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.prompt_template import PromptTemplate
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case import CaseExecutionItem
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.task import (
    TaskCreate,
    TaskMetrics,
    TaskOut,
    TaskRunsPage,
    TaskRunSummary,
    TaskScheduleUpdate,
)
from billing_qa.services.engine.metrics import (
    apply_metrics_to_task,
    compute_task_metrics,
    latest_executions,
)
from billing_qa.services.engine.schedule import (
    clone_for_run,
    generate_task_code,
    initial_next_run,
    round_task_name,
)
from billing_qa.services.engine.scheduler import (
    RETRYABLE_STATUSES,
    UNFINISHED_STATUSES,
    scheduler,
)
from billing_qa.services.io.execution_excel import (
    bool_text,
    build_task_results_xlsx,
    case_status_label,
    case_type_label,
    error_kind_label,
    format_datetime,
    grading_mode_label,
    input_type_label,
    review_status_label,
    risk_level_label,
    root_cause_label,
    self_check_status_label,
    source_label,
    status_label,
)
from billing_qa.services.io.execution_jsonl import build_task_results_jsonl

router = APIRouter(
    prefix="/tasks",
    tags=["批量任务"],
    dependencies=[
        Depends(
            require_module_access(
                "task:read",
                (
                    "task:create",
                    "task:stop",
                    "task:delete",
                    "execution:retry",
                ),
            )
        )
    ],
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


async def _latest_by_case(db: AsyncSession, task_id: int) -> dict[int, ExecutionRecord]:
    """按用例取最新一次执行记录。"""
    return {record.case_id: record for record in await latest_executions(db, task_id)}


async def _unfinished_case_ids(db: AsyncSession, task: BatchTask) -> list[int]:
    """未完成用例：从未执行、仍在执行，或执行被终止。

    已有最终判定（通过 / 失败 / 异常 / 熔断）的用例不算未完成，
    续做时保持原结果，不重复执行。
    """
    latest = await _latest_by_case(db, task.id)
    unfinished: list[int] = []
    for case_id in task.case_ids or []:
        record = latest.get(int(case_id))
        if record is None or record.status in UNFINISHED_STATUSES:
            unfinished.append(int(case_id))
    return unfinished


async def _unfinished_counts(
    db: AsyncSession, tasks: list[BatchTask]
) -> dict[int, int]:
    """批量计算任务列表页的未完成用例数，避免逐条查询。"""
    if not tasks:
        return {}
    task_by_id = {task.id: task for task in tasks}
    task_ids = list(task_by_id)
    latest_ids = (
        select(func.max(ExecutionRecord.id).label("max_id"))
        .where(ExecutionRecord.task_id.in_(task_ids))
        .group_by(ExecutionRecord.task_id, ExecutionRecord.case_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                ExecutionRecord.task_id,
                ExecutionRecord.case_id,
                ExecutionRecord.status,
            ).where(ExecutionRecord.id.in_(select(latest_ids.c.max_id)))
        )
    ).all()
    statuses: dict[int, dict[int, str]] = {task_id: {} for task_id in task_ids}
    for task_id, case_id, status in rows:
        statuses[int(task_id)][int(case_id)] = status
    counts: dict[int, int] = {}
    for task_id, task in task_by_id.items():
        latest = statuses.get(task_id, {})
        counts[task_id] = sum(
            1
            for case_id in (task.case_ids or [])
            if int(case_id) not in latest
            or latest[int(case_id)] in UNFINISHED_STATUSES
        )
    return counts


async def _task_out(db: AsyncSession, task: BatchTask, *, run_count: int = 0) -> TaskOut:
    unfinished = await _unfinished_counts(db, [task])
    return task_to_out(
        task,
        env_name=await _env_name(db, task.environment_id),
        run_count=run_count,
        unfinished_cases=unfinished.get(task.id, 0),
    )


@router.get("", response_model=Page[TaskOut], summary="任务列表")
async def list_tasks(
    status: TaskStatus | None = None,
    schedule_type: TaskScheduleType | None = None,
    keyword: str | None = None,
    include_runs: bool = Query(
        default=False, description="是否包含周期计划派生出的执行任务（默认只列顶层任务）"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[TaskOut]:
    conditions = []
    if status:
        conditions.append(BatchTask.status == status)
    if schedule_type:
        conditions.append(BatchTask.schedule_type == schedule_type.value)
    keyword_filter = keyword_condition((BatchTask.name, BatchTask.code), keyword)
    if keyword_filter is not None:
        conditions.append(keyword_filter)
    if not include_runs:
        # 周期计划每次触发派生的子任务不再平铺在列表里，统一到计划详情的“历次执行”。
        conditions.append(BatchTask.schedule_source_id.is_(None))
    total = (
        await db.execute(select(func.count()).select_from(BatchTask).where(*conditions))
    ).scalar_one()
    if include_runs:
        order_by = (BatchTask.id.desc(),)
    else:
        # 最近触发过的计划置顶；从未触发的按创建时间排。
        order_by = (
            func.coalesce(BatchTask.last_run_at, BatchTask.created_at).desc(),
            BatchTask.id.desc(),
        )
    rows = (
        await db.execute(
            select(BatchTask)
            .where(*conditions)
            .order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    env_names = {
        env_id: name
        for env_id, name in (await db.execute(select(Environment.id, Environment.name))).all()
    }
    run_counts: dict[int, int] = {}
    # 顶层任务可能派生出执行：周期计划每次触发一次，连续多轮任务每轮一次
    plan_ids = [item.id for item in rows]
    if plan_ids:
        count_rows = (
            await db.execute(
                select(BatchTask.schedule_source_id, func.count())
                .where(BatchTask.schedule_source_id.in_(plan_ids))
                .group_by(BatchTask.schedule_source_id)
            )
        ).all()
        run_counts = {
            int(source_id): int(count)
            for source_id, count in count_rows
            if source_id is not None
        }
    unfinished_counts = await _unfinished_counts(db, list(rows))
    return Page[TaskOut](
        items=[
            task_to_out(
                item,
                env_name=env_names.get(item.environment_id or 0),
                run_count=run_counts.get(item.id, 0),
                unfinished_cases=unfinished_counts.get(item.id, 0),
            )
            for item in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=TaskOut,
    summary="创建批量任务",
    dependencies=[Depends(require_permission("task:create"))],
)
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

    # 新字段未传时沿用旧的环境重试配置，保证旧客户端行为不变；
    # 旧字段同步写入，便于旧版本接口与历史导出继续读取。
    retry_on_error = (
        payload.retry_on_error
        if payload.retry_on_error is not None
        else payload.retry_on_env_error
    )
    max_error_retries = (
        payload.max_error_retries
        if payload.max_error_retries is not None
        else payload.max_env_retries
    )

    # 立即执行的连续多轮：任务自身就是第一轮，名称与其他轮次保持一致带上轮次标识；
    # 定时/周期计划的计划行本身不是某一轮，仍保留原名称。
    task_name = payload.name
    if (
        payload.schedule_type == TaskScheduleType.IMMEDIATE
        and payload.repeat_rounds > 1
    ):
        task_name = round_task_name(payload.name, 1, payload.repeat_rounds)

    task = BatchTask(
        code=_generate_code(),
        name=task_name,
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
        retry_on_env_error=retry_on_error,
        max_env_retries=max_error_retries,
        retry_on_error=retry_on_error,
        max_error_retries=max_error_retries,
        retry_backoff_seconds=payload.retry_backoff_seconds,
        enable_self_check=payload.enable_self_check,
        schedule_type=payload.schedule_type.value,
        scheduled_at=payload.scheduled_at,
        schedule_time=payload.schedule_time,
        schedule_weekdays=payload.schedule_weekdays or None,
        schedule_active=True,
        repeat_rounds=payload.repeat_rounds,
        round_index=1,
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
    return await _task_out(db, task)


@router.get("/{task_id}", response_model=TaskOut, summary="任务详情")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await _get_task(db, task_id)
    return await _task_out(db, task)


PERIODIC_PLAN_TYPES = {TaskScheduleType.DAILY.value, TaskScheduleType.WEEKLY.value}


def _is_plan(task: BatchTask) -> bool:
    return task.schedule_type != TaskScheduleType.IMMEDIATE.value


def _is_plan_root(task: BatchTask) -> bool:
    """计划根任务：定时/周期计划，或立即执行的连续多轮任务。"""
    if task.schedule_source_id is not None:
        return False
    return _is_plan(task) or max(1, int(task.repeat_rounds or 1)) > 1


async def _plan_inflight_tasks(db: AsyncSession, plan_id: int) -> list[BatchTask]:
    """计划根任务自身或其派生批次中仍在等待/执行的记录。"""
    rows = await db.execute(
        select(BatchTask).where(
            or_(BatchTask.id == plan_id, BatchTask.schedule_source_id == plan_id),
            BatchTask.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
        )
    )
    return list(rows.scalars().all())


async def _trigger_plan(db: AsyncSession, task: BatchTask) -> BatchTask:
    """立即执行一次计划：一次性计划用自身，周期计划克隆新任务。"""
    # 计划被停止后必须先重新启用，任何类型都不能绕过。
    if not task.schedule_active or task.status == TaskStatus.ABORTED:
        raise ConflictError("计划已停止，请先启用计划再立即执行")

    if task.schedule_type == TaskScheduleType.SCHEDULED.value:
        # 已跑完的一次性计划不允许重复执行。
        if task.started_at is not None and task.status not in {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
        }:
            raise ConflictError("该一次性计划已执行过，请重新创建或调整计划时间")
        total = max(1, int(task.repeat_rounds or 1))
        # 连续多轮：计划行保留为「轮次链是否继续」的开关，链跑满或手动停止后关闭；
        # 一次性单轮计划触发后即不再需要计划标记。
        task.schedule_active = total > 1
        task.next_run_at = None
        task.last_run_at = now()
        if total > 1:
            # 连续多轮：计划行只保留配置，第一轮也克隆成独立执行
            run = clone_for_run(task)
            db.add(run)
            await db.flush()
            task.status = TaskStatus.COMPLETED
            task.control_state = "FINISHED"
            task.finished_at = now()
            await db.commit()
            await db.refresh(run)
            await scheduler.start(run.id)
            return run
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


@router.post(
    "/{task_id}/start",
    response_model=MessageResponse,
    summary="启动任务",
    dependencies=[Depends(require_permission("task:create"))],
)
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
    "/{task_id}/run-now",
    response_model=TaskOut,
    summary="立即执行一次定时计划",
    dependencies=[Depends(require_permission("task:create"))],
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
    return await _task_out(db, run)


@router.put(
    "/{task_id}/schedule",
    response_model=TaskOut,
    summary="调整定时计划",
    dependencies=[Depends(require_permission("task:create"))],
)
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
    # 未显式传 active 时保持现状：避免「只编辑时间的调用静默重启已停计划」。
    # 注意 activate 分支要求显式传 active=true（或原本就是激活态的编辑）。
    task.schedule_active = payload.active if payload.active is not None else task.schedule_active
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
    return await _task_out(db, task)


@router.post(
    "/{task_id}/abort",
    response_model=MessageResponse,
    summary="终止任务",
    dependencies=[Depends(require_permission("task:stop"))],
)
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
    "/{task_id}/stop-plan",
    response_model=TaskOut,
    summary="停止计划",
    dependencies=[Depends(require_permission("task:stop"))],
)
async def stop_plan(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskOut:
    """停止计划的后续触发；连续多轮计划同时终止当前/待执行的轮次。

    与「终止」的区别：终止针对一次正在跑的批次；停止计划针对计划本身，
    周期计划停止后续每天的触发，指定时间/连续多轮计划则收口整条轮次链。
    """
    task = await _get_task(db, task_id)
    if not _is_plan_root(task):
        raise ConflictError("当前任务不是计划任务，请在任务运行中使用终止")

    if task.schedule_type in PERIODIC_PLAN_TYPES:
        if not task.schedule_active:
            raise ConflictError("计划已停止")
        task.schedule_active = False
        task.next_run_at = None
        if task.status == TaskStatus.SCHEDULED:
            task.status = TaskStatus.ABORTED
            task.control_state = "ABORTED"
            task.finished_at = now()
        await db.commit()
        await db.refresh(task)
        return await _task_out(db, task)

    if not task.schedule_active and task.status == TaskStatus.ABORTED:
        raise ConflictError("计划已停止")
    if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED} and not task.schedule_active:
        raise ConflictError("计划已结束，无需停止")

    inflight = await _plan_inflight_tasks(db, task.id)
    task.schedule_active = False
    task.next_run_at = None
    if task.status == TaskStatus.SCHEDULED:
        # 指定时间计划尚未触发：直接作废，不再进入调度器。
        task.status = TaskStatus.ABORTED
        task.control_state = "ABORTED"
        task.finished_at = now()
    elif task.status == TaskStatus.COMPLETED:
        # 计划行本身已完成、轮次链仍在执行：保留历史状态，只标记链已停止。
        task.control_state = "ABORTED"
    await db.commit()

    for run in inflight:
        await scheduler.abort(run.id)

    await db.refresh(task)
    return await _task_out(db, task)


@router.post(
    "/{task_id}/retry-failed",
    response_model=MessageResponse,
    summary="失败用例断点重跑",
    dependencies=[Depends(require_permission("execution:retry"))],
)
async def retry_failed(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    task = await _get_task(db, task_id)
    if scheduler.is_running(task_id):
        raise ConflictError("任务正在运行，无法发起重跑")
    latest = await _latest_by_case(db, task_id)
    targets: list[int] = []
    for case_id in task.case_ids or []:
        record = latest.get(int(case_id))
        # 没跑过、卡在执行中，以及失败/异常/熔断/已终止的用例都纳入重跑。
        if (
            record is None
            or record.status in {"RUNNING", "PENDING"}
            or record.status in RETRYABLE_STATUSES
        ):
            targets.append(int(case_id))
    if not targets:
        raise ConflictError("没有可重跑的失败/异常用例")
    # 不在此处预写 RUNNING：状态翻转统一由调度器 worker 执行。若先落库再
    # start，并发终止会落在两者之间（终止端点看到 RUNNING 会放行，而 worker
    # 只拦 ABORTING），导致终止意图丢失、任务照跑；交由 worker 翻转后，
    # 终止端点对「已结束任务」的守卫在所有时序下都成立。
    if not await scheduler.start(task_id, retry_failed=True):
        raise ConflictError("任务已在运行中")
    return MessageResponse(
        message=f"已提交 {len(targets)} 条失败/未完成用例的重跑（按用例取最新一次）"
    )


@router.post(
    "/{task_id}/resume",
    response_model=MessageResponse,
    summary="续做未完成用例",
    dependencies=[Depends(require_permission("execution:retry"))],
)
async def resume_task(task_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    """终止或异常结束后，从未完成处继续执行。

    与「失败重跑」的区别：续做只补跑未执行 / 执行中 / 被终止的用例，
    已有最终判定的用例（含失败）保持原结果，不会被重新执行。
    """
    task = await _get_task(db, task_id)
    if _is_plan(task):
        raise ConflictError("定时/周期计划不支持整体续做，请在其历次执行中选择批次续做")
    if scheduler.is_running(task_id):
        raise ConflictError("任务正在运行，无法发起续做")
    if task.status not in {TaskStatus.ABORTED, TaskStatus.FAILED}:
        raise ConflictError("仅已终止或异常结束的任务支持续做")
    unfinished = await _unfinished_case_ids(db, task)
    if not unfinished:
        raise ConflictError(
            "所有用例都已有最终结果，无需续做；如需重跑失败用例请使用「失败重跑」"
        )
    # 同「失败重跑」：不预写 RUNNING，由 worker 统一翻转，保证并发终止意图不丢。
    if not await scheduler.start(task_id, resume=True):
        raise ConflictError("任务已在运行中")
    return MessageResponse(
        message=(
            f"已续做 {len(unfinished)} 条未完成用例，"
            "已完成/已判定的用例结果保持不变"
        )
    )


@router.post(
    "/{task_id}/cases/{case_id}/retry",
    response_model=MessageResponse,
    summary="批次内单用例重试",
    dependencies=[Depends(require_permission("execution:retry"))],
)
async def retry_case(
    task_id: int, case_id: int, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """在当前批次内单独重跑一条用例，作为该用例的最新一次尝试。

    用于自动重试后仍异常、但仍希望每个用例都有最终状态的场景。
    仅当任务未运行、且该用例最近一次执行为失败/异常类状态时允许。
    """
    task = await _get_task(db, task_id)
    if scheduler.is_running(task_id):
        raise ConflictError("任务正在运行，无法发起单用例重试")
    if int(case_id) not in {int(item) for item in (task.case_ids or [])}:
        raise NotFoundError("该用例不在当前批次中")
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
    if latest is None:
        raise NotFoundError("该用例在当前批次尚无执行记录")
    if latest.status in {"RUNNING", "PENDING"}:
        raise ConflictError("该用例正在执行中，请稍后再试")
    if latest.status not in RETRYABLE_STATUSES:
        raise ConflictError("该用例最近一次执行不处于可重试状态")
    if not await scheduler.retry_case(task_id, case_id):
        raise ConflictError("任务正在运行，无法发起单用例重试")
    return MessageResponse(message="已提交单用例重试，结果将实时更新")


@router.get(
    "/{task_id}/runs",
    response_model=TaskRunsPage,
    summary="周期计划历次执行",
)
async def list_task_runs(
    task_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> TaskRunsPage:
    """返回计划每次触发/每一轮的批量任务，并附带全量聚合统计。"""
    task = await _get_task(db, task_id)
    conditions = [BatchTask.schedule_source_id == task_id]
    # 立即执行的多轮任务：第 1 轮就是任务自身，需要一并纳入历次执行
    if (
        max(1, int(task.repeat_rounds or 1)) > 1
        and task.schedule_type == TaskScheduleType.IMMEDIATE.value
        and task.started_at is not None
    ):
        conditions = [
            or_(BatchTask.schedule_source_id == task_id, BatchTask.id == task_id)
        ]

    total = (
        await db.execute(select(func.count()).select_from(BatchTask).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(BatchTask)
                .where(*conditions)
                .order_by(BatchTask.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    env_names = {
        env_id: name
        for env_id, name in (await db.execute(select(Environment.id, Environment.name))).all()
    }

    status_rows = (
        await db.execute(
            select(
                BatchTask.status,
                func.count(),
                func.coalesce(func.sum(BatchTask.pass_count), 0),
                func.coalesce(func.sum(BatchTask.finished_cases), 0),
                func.coalesce(func.sum(BatchTask.total_cases), 0),
            )
            .where(*conditions)
            .group_by(BatchTask.status)
        )
    ).all()
    summary = TaskRunSummary(total_runs=int(total))
    for status, count, pass_count, finished_cases, total_cases in status_rows:
        key = str(status).upper()
        if key == "COMPLETED":
            summary.completed += int(count)
        elif key == "FAILED":
            summary.failed += int(count)
        elif key == "ABORTED":
            summary.aborted += int(count)
        elif key == "RUNNING":
            summary.running += int(count)
        elif key == "PENDING":
            summary.pending += int(count)
        summary.pass_count += int(pass_count)
        summary.finished_cases += int(finished_cases)
        summary.total_cases += int(total_cases)
    if summary.finished_cases:
        summary.pass_rate = round(
            summary.pass_count / summary.finished_cases * 100, 2
        )

    unfinished_counts = await _unfinished_counts(db, list(rows))
    return TaskRunsPage(
        items=[
            task_to_out(
                item,
                env_name=env_names.get(item.environment_id or 0),
                unfinished_cases=unfinished_counts.get(item.id, 0),
            )
            for item in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
        summary=summary,
    )


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
    keyword: str | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100, description="最低得分"),
    max_score: float | None = Query(default=None, ge=0, le=100, description="最高得分"),
    sort: str | None = Query(
        default=None,
        pattern="^(score_desc|score_asc)$",
        description="排序：score_desc 得分从高到低 / score_asc 得分从低到高",
    ),
    latest_per_case: bool = Query(
        default=False, description="是否按用例只返回最新一次尝试（批量任务详情用）"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CaseExecutionItem]:
    await _get_task(db, task_id)
    if min_score is not None and max_score is not None and min_score > max_score:
        raise ValidationError("最低得分不能大于最高得分")
    attempt_counts: dict[int, int] = {}

    if latest_per_case:
        latest_ids = (
            select(func.max(ExecutionRecord.id).label("max_id"))
            .where(ExecutionRecord.task_id == task_id)
            .group_by(ExecutionRecord.case_id)
            .subquery()
        )
        conditions = [ExecutionRecord.id.in_(select(latest_ids.c.max_id))]
    else:
        conditions = [ExecutionRecord.task_id == task_id]
    if status:
        conditions.append(ExecutionRecord.status == status)
    keyword_filter = keyword_condition((TestCase.code, TestCase.name), keyword)
    if keyword_filter is not None:
        conditions.append(
            ExecutionRecord.case_id.in_(select(TestCase.id).where(keyword_filter))
        )
    if min_score is not None:
        conditions.append(ExecutionRecord.total_score >= min_score)
    if max_score is not None:
        conditions.append(ExecutionRecord.total_score <= max_score)

    if sort == "score_desc":
        order_by = [ExecutionRecord.total_score.desc().nulls_last(), ExecutionRecord.id.desc()]
    elif sort == "score_asc":
        order_by = [ExecutionRecord.total_score.asc().nulls_last(), ExecutionRecord.id.desc()]
    else:
        order_by = [ExecutionRecord.id.desc()]

    total = (
        await db.execute(select(func.count()).select_from(ExecutionRecord).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ExecutionRecord)
            .where(*conditions)
            .order_by(*order_by)
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


# ---------------------------------------------------------------- 结果导出
def _snapshot_value(snapshot: dict[str, Any], key: str, fallback: Any = None) -> Any:
    value = snapshot.get(key) if isinstance(snapshot, dict) else None
    return fallback if value is None else value


def _join_notes(value: Any) -> str | None:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if item not in (None, "")]
        return "；".join(items) or None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _judge_notes(record: ExecutionRecord) -> str | None:
    """优先取聚合裁决里的备注，缺失时回退到各裁判归因说明。"""
    result = record.judge_result
    if isinstance(result, dict):
        notes: list[str] = []
        for key in ("notes", "policy_notes"):
            text = _join_notes(result.get(key))
            if text:
                notes.extend(item for item in text.split("；") if item)
        if notes:
            # 去重并保持原有顺序，避免 notes 与 policy_notes 重复。
            return "；".join(dict.fromkeys(notes))
    reasons = [
        judge.reason.strip()
        for judge in record.judges
        if judge.reason and judge.reason.strip()
    ]
    return "；".join(reasons) or None


def _turn_payload(turn: Any) -> dict[str, Any]:
    return {
        "turn": turn.turn_index,
        "input_source": turn.input_source,
        "query": turn.query,
        "simulator_thought": turn.simulator_thought,
        "simulator_status": turn.simulator_status,
        "hidden_context_released": turn.hidden_context_released,
        "rendered_prompt": turn.rendered_prompt,
        "request_payload": turn.request_payload,
        "response_raw": turn.response_raw,
        "final_answer": turn.final_answer,
        "session_id": turn.session_id,
        "intent": turn.intent,
        "generated_sql": turn.generated_sql,
        "api_params": turn.api_params,
        "raw_db_data": turn.raw_db_data,
        "tool_calls": turn.tool_calls,
        "evidence": turn.evidence,
        "ttft_ms": turn.ttft_ms,
        "latency_ms": turn.latency_ms,
        "rule_check": turn.rule_check,
        "simulator_prompt_tokens": turn.simulator_prompt_tokens,
        "simulator_completion_tokens": turn.simulator_completion_tokens,
        "created_at": format_datetime(turn.created_at),
    }


def _judge_payload(judge: Any) -> dict[str, Any]:
    return {
        "model_config_id": judge.model_config_id,
        "model_name": judge.model_name,
        "pass_flag": judge.pass_flag,
        "total_score": judge.total_score,
        "dimension_scores": judge.dimension_scores,
        "error_tags": judge.error_tags,
        "reason": judge.reason,
        "judge_prompt": judge.judge_prompt,
        "raw_response": judge.raw_response,
        "prompt_tokens": judge.prompt_tokens,
        "completion_tokens": judge.completion_tokens,
        "created_at": format_datetime(judge.created_at),
    }


async def _collect_task_results(
    db: AsyncSession, task: BatchTask, *, latest_per_case: bool
) -> list[dict[str, Any]]:
    """收集批次执行结果，并整理成 Excel 行数据。"""
    attempt_counts: dict[int, int] = {}
    if latest_per_case:
        latest_ids = (
            select(func.max(ExecutionRecord.id).label("max_id"))
            .where(ExecutionRecord.task_id == task.id)
            .group_by(ExecutionRecord.case_id)
            .subquery()
        )
        condition = ExecutionRecord.id.in_(select(latest_ids.c.max_id))
    else:
        condition = ExecutionRecord.task_id == task.id

    records = list(
        (
            await db.execute(
                select(ExecutionRecord)
                .where(condition)
                .order_by(
                    ExecutionRecord.case_id.asc(),
                    ExecutionRecord.attempt_no.asc(),
                    ExecutionRecord.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not records:
        return []

    case_ids = {record.case_id for record in records}
    case_map = {
        case.id: case
        for case in (
            await db.execute(select(TestCase).where(TestCase.id.in_(case_ids)))
        )
        .scalars()
        .all()
    }
    count_rows = (
        await db.execute(
            select(ExecutionRecord.case_id, func.count())
            .where(
                ExecutionRecord.task_id == task.id,
                ExecutionRecord.case_id.in_(case_ids),
            )
            .group_by(ExecutionRecord.case_id)
        )
    ).all()
    attempt_counts = {int(case_id): int(count) for case_id, count in count_rows}

    env_ids = {record.environment_id for record in records if record.environment_id}
    if task.environment_id:
        env_ids.add(task.environment_id)
    env_names: dict[int, str] = {}
    if env_ids:
        env_names = {
            env_id: name
            for env_id, name in (
                await db.execute(
                    select(Environment.id, Environment.name).where(
                        Environment.id.in_(env_ids)
                    )
                )
            ).all()
        }

    template_codes = {
        str(record.self_check["template_code"])
        for record in records
        if isinstance(record.self_check, dict)
        and record.self_check.get("template_code")
    }
    template_names: dict[str, str] = {}
    if template_codes:
        template_names = {
            str(code): str(name)
            for code, name in (
                await db.execute(
                    select(PromptTemplate.code, PromptTemplate.name).where(
                        PromptTemplate.code.in_(template_codes)
                    )
                )
            ).all()
        }

    rows: list[dict[str, Any]] = []
    for record in records:
        snapshot = record.case_snapshot or {}
        case = case_map.get(record.case_id)
        tags = _snapshot_value(snapshot, "tags", case.tags if case else None)
        final_answer = next(
            (
                turn.final_answer
                for turn in reversed(record.turns)
                if turn.final_answer not in (None, "")
            ),
            None,
        )
        env_id = record.environment_id or task.environment_id
        self_check = record.self_check if isinstance(record.self_check, dict) else None
        sc = self_check or {}
        self_check_conclusion = (
            sc.get("conclusion") or sc.get("skip_reason") or sc.get("error")
        )
        template_code = sc.get("template_code")
        template_version = sc.get("template_version")
        template_name = (
            sc.get("template_name")
            or template_names.get(str(template_code))
            or ("用例自定义覆写" if template_code == "PROMPT_OVERRIDE" else template_code)
        )
        self_check_template = None
        if template_name:
            self_check_template = (
                f"{template_name}@v{template_version}"
                if template_version
                else str(template_name)
            )
        self_check_notes = (
            "；".join(str(note) for note in (sc.get("notes") or []) if str(note).strip())
            or None
        )
        rows.append(
            {
                "execution_id": record.id,
                "trace_uid": record.trace_uid,
                "case_id": record.case_id,
                "case_code": _snapshot_value(snapshot, "code", case.code if case else None),
                "case_name": _snapshot_value(snapshot, "name", case.name if case else None),
                "case_type": case_type_label(
                    _snapshot_value(snapshot, "case_type", case.case_type if case else None)
                ),
                "priority": _snapshot_value(
                    snapshot, "priority", case.priority if case else None
                ),
                "case_status": case_status_label(
                    _snapshot_value(snapshot, "status", case.status if case else None)
                ),
                "tags": ",".join(tags) if isinstance(tags, list) else tags,
                "owner": _snapshot_value(snapshot, "owner", case.owner if case else None),
                "dataset_version": _snapshot_value(
                    snapshot, "dataset_version", case.dataset_version if case else None
                ),
                "snapshot_id": _snapshot_value(
                    snapshot, "snapshot_id", case.snapshot_id if case else None
                ),
                "as_of": _snapshot_value(snapshot, "as_of", record.as_of),
                "grading_mode": grading_mode_label(
                    record.grading_mode or _snapshot_value(snapshot, "grading_mode")
                ),
                "input_type": input_type_label(_snapshot_value(snapshot, "input_type")),
                "risk_level": risk_level_label(_snapshot_value(snapshot, "risk_level")),
                "review_status": review_status_label(
                    _snapshot_value(snapshot, "review_status")
                ),
                "expected_answer": _snapshot_value(
                    snapshot, "expected_answer", case.expected_answer if case else None
                ),
                "description": _snapshot_value(
                    snapshot, "description", case.description if case else None
                ),
                "query": _snapshot_value(snapshot, "query", case.query if case else None),
                "user_goal": _snapshot_value(
                    snapshot, "user_goal", case.user_goal if case else None
                ),
                "source": source_label(record.source),
                "status": status_label(record.status),
                "status_code": record.status,
                "pass_flag": bool_text(record.pass_flag),
                "total_score": record.total_score,
                "termination_status": status_label(record.termination_status),
                "attempt_no": record.attempt_no,
                "attempt_count": attempt_counts.get(record.case_id, 1),
                "error_kind": error_kind_label(record.error_kind),
                "root_cause": root_cause_label(record.root_cause),
                "lucky_pass": bool_text(record.lucky_pass),
                "turns_used": record.turns_used,
                "max_turns": record.max_turns,
                "latency_ms": record.latency_ms,
                "ttft_ms": record.ttft_ms,
                "simulator_tokens": record.simulator_tokens,
                "judge_tokens": record.judge_tokens,
                "total_tokens": int(record.simulator_tokens or 0)
                + int(record.judge_tokens or 0),
                "simulator_template_version": record.simulator_template_version,
                "judge_template_version": record.judge_template_version,
                "grader_version": record.grader_version,
                "environment_id": env_id,
                "environment_name": env_names.get(env_id or 0),
                "started_at": format_datetime(record.started_at),
                "finished_at": format_datetime(record.finished_at),
                "error_message": record.error_message,
                "judge_notes": _judge_notes(record),
                "final_answer": final_answer,
                "self_check_status": self_check_status_label(
                    str(sc.get("status")) if sc.get("status") else None
                ),
                # 内部统计用原始状态码（不单独导出为列）
                "self_check_status_code": (
                    str(sc.get("status")) if sc.get("status") else None
                ),
                "self_check_trigger": sc.get("trigger"),
                "self_check_skip_reason": sc.get("skip_reason"),
                "self_check_conclusion": self_check_conclusion,
                "self_check_session_id": sc.get("session_id"),
                "self_check_template": self_check_template,
                "self_check_latency_ms": sc.get("latency_ms"),
                "self_check_performed_at": sc.get("performed_at"),
                "self_check_error": sc.get("error"),
                "self_check_notes": self_check_notes,
                "self_check_prompt": sc.get("prompt"),
                "self_check_request": sc.get("request_payload"),
                "self_check_response": sc.get("response_raw"),
                "rule_result": record.rule_result,
                "judge_result": record.judge_result,
                "answer_grading": record.answer_grading,
                "fact_results": record.fact_results,
                "diagnostics": record.diagnostics,
                "judges": [_judge_payload(judge) for judge in record.judges],
                "turns": [_turn_payload(turn) for turn in record.turns],
                "case_snapshot": snapshot,
            }
        )
    return rows


def _safe_filename_part(value: str | None) -> str:
    text = (value or "task").strip()
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", text).strip("._")
    return safe or "task"


@router.get(
    "/{task_id}/export",
    summary="导出本次批量执行结果（Excel / JSONL）",
    dependencies=[Depends(require_permission("task:export"))],
)
async def export_task_results(
    task_id: int,
    latest_per_case: bool = Query(
        default=True, description="是否按用例只导出最新一次尝试（false 导出全部尝试）"
    ),
    format: Literal["xlsx", "jsonl"] = Query(
        default="xlsx",
        description=(
            "导出格式：xlsx 受 Excel 单元格 32767 字符上限约束会截断超长字段；"
            "jsonl 不截断，完整保留轮次轨迹等大字段"
        ),
    ),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> Response:
    task = await _get_task(db, task_id)
    metrics = await compute_task_metrics(db, task)
    rows = await _collect_task_results(db, task, latest_per_case=latest_per_case)
    exported_at = now()
    scope_label = (
        "用例最新（每用例只保留最新一次尝试）"
        if latest_per_case
        else "全部尝试（含失败重跑）"
    )
    if format == "jsonl":
        content = build_task_results_jsonl(
            task,
            metrics,
            rows,
            environment_name=await _env_name(db, task.environment_id),
            scope_label=scope_label,
            exported_by=operator,
            exported_at=exported_at,
        )
        filename = (
            f"task_{_safe_filename_part(task.code)}_results_"
            f"{exported_at:%Y%m%d_%H%M%S}.jsonl"
        )
        media_type = "application/x-ndjson; charset=utf-8"
    else:
        content = build_task_results_xlsx(
            task,
            metrics,
            rows,
            environment_name=await _env_name(db, task.environment_id),
            scope_label=scope_label,
            exported_by=operator,
            exported_at=exported_at,
        )
        filename = (
            f"task_{_safe_filename_part(task.code)}_results_"
            f"{exported_at:%Y%m%d_%H%M%S}.xlsx"
        )
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
