"""定时执行：时间计算、参数校验、派发与任务克隆。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from billing_qa.core.schedule_time import (
    compute_next_run,
    normalize_local,
    normalize_weekdays,
    parse_time_text,
)
from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import TaskScheduleType, TaskStatus
from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskCreate
from billing_qa.services.engine.schedule import (
    clone_for_run,
    clone_next_round,
    run_due_tasks,
)
from billing_qa.services.engine.scheduler import scheduler

MONDAY = datetime(2026, 11, 16)  # 2026-11-16 是周一


# --------------------------------------------------------------------- 时间计算
def test_daily_next_run_same_day_and_rollover() -> None:
    assert compute_next_run(
        schedule_type="DAILY",
        schedule_time="09:30",
        base=MONDAY.replace(hour=8, minute=0),
    ) == MONDAY.replace(hour=9, minute=30)
    # 当天时刻已过 → 顺延到次日
    assert compute_next_run(
        schedule_type="DAILY",
        schedule_time="09:30",
        base=MONDAY.replace(hour=10, minute=0),
    ) == MONDAY.replace(hour=9, minute=30) + timedelta(days=1)


def test_weekly_next_run_picks_selected_weekday() -> None:
    # 周一 10:00，选周一/三/五 09:00 → 周三 09:00
    assert compute_next_run(
        schedule_type="WEEKLY",
        schedule_time="09:00",
        weekdays=[1, 3, 5],
        base=MONDAY.replace(hour=10, minute=0),
    ) == datetime(2026, 11, 18, 9, 0)
    # 周一 08:00 未到当天时刻 → 当天 09:00
    assert compute_next_run(
        schedule_type="WEEKLY",
        schedule_time="09:00",
        weekdays=[1, 3],
        base=MONDAY.replace(hour=8, minute=0),
    ) == MONDAY.replace(hour=9, minute=0)
    # 只有周一，且当天已过 → 下周一
    assert compute_next_run(
        schedule_type="WEEKLY",
        schedule_time="09:00",
        weekdays=[1],
        base=MONDAY.replace(hour=10, minute=0),
    ) == MONDAY.replace(hour=9, minute=0) + timedelta(days=7)


def test_weekday_and_time_normalization() -> None:
    assert normalize_weekdays([5, 1, 5, 3]) == [1, 3, 5]
    with pytest.raises(ValueError):
        normalize_weekdays([])
    with pytest.raises(ValueError):
        normalize_weekdays([0])
    with pytest.raises(ValueError):
        normalize_weekdays([8])
    assert parse_time_text("00:00").hour == 0
    assert parse_time_text("23:59").minute == 59
    for bad in ("24:00", "9:00", "09:60", "", "09-00"):
        with pytest.raises(ValueError):
            parse_time_text(bad)


def test_normalize_local_strips_timezone() -> None:
    aware = datetime(2026, 11, 16, 9, 0).astimezone()
    assert normalize_local(aware) == datetime(2026, 11, 16, 9, 0)
    assert normalize_local(None) is None


# --------------------------------------------------------------------- 参数校验
def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "定时回归",
        "case_scope": "ALL_ACTIVE",
        "schedule_type": "IMMEDIATE",
    }
    base.update(overrides)
    return base


def test_create_immediate_clears_schedule_fields() -> None:
    data = TaskCreate(
        **_payload(scheduled_at=datetime.now() + timedelta(hours=1), schedule_time="09:00")
    )
    assert data.scheduled_at is None
    assert data.schedule_time is None
    assert data.schedule_weekdays == []


def test_create_scheduled_requires_future_time() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(**_payload(schedule_type="SCHEDULED"))
    with pytest.raises(ValidationError):
        TaskCreate(
            **_payload(
                schedule_type="SCHEDULED", scheduled_at=datetime.now() - timedelta(minutes=1)
            )
        )
    moment = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    data = TaskCreate(**_payload(schedule_type="SCHEDULED", scheduled_at=moment))
    assert data.scheduled_at == moment
    assert data.schedule_time is None


def test_create_periodic_validates_time_and_weekdays() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(**_payload(schedule_type="DAILY"))
    with pytest.raises(ValidationError):
        TaskCreate(**_payload(schedule_type="DAILY", schedule_time="25:00"))

    daily = TaskCreate(**_payload(schedule_type="DAILY", schedule_time="09:05"))
    assert daily.schedule_time == "09:05"
    assert daily.schedule_weekdays == []

    with pytest.raises(ValidationError):
        TaskCreate(**_payload(schedule_type="WEEKLY", schedule_time="09:05"))
    weekly = TaskCreate(
        **_payload(schedule_type="WEEKLY", schedule_time="09:05", schedule_weekdays=[5, 1, 5])
    )
    assert weekly.schedule_weekdays == [1, 5]
    assert weekly.scheduled_at is None


# --------------------------------------------------------------------- 派发
def test_clone_for_run_isolates_history() -> None:
    plan = BatchTask(
        id=42,
        code="PLAN-1",
        name="每日回归",
        status=TaskStatus.SCHEDULED,
        control_state="RUNNING",
        case_ids=[1, 2],
        total_cases=2,
        concurrency=4,
        qps_limit=2.5,
        retry_on_env_error=False,
        max_env_retries=1,
        retry_backoff_seconds=9,
        schedule_type=TaskScheduleType.DAILY,
        schedule_time="09:00",
        schedule_active=True,
        created_by="admin",
    )
    run = clone_for_run(plan, current=datetime(2026, 11, 17, 9, 0))
    assert run.code != plan.code
    assert run.schedule_type == TaskScheduleType.IMMEDIATE
    assert run.schedule_source_id == 42
    assert run.schedule_active is False
    assert run.case_ids == [1, 2]
    assert run.total_cases == 2
    assert run.concurrency == 4
    assert run.qps_limit == 2.5
    assert run.retry_on_env_error is False
    assert run.status == TaskStatus.PENDING
    assert run.created_by == "admin"
    assert run.next_run_at is None
    assert "11-17 09:00" in run.name


def test_clone_for_run_marks_first_round_for_multi_round_plan() -> None:
    plan = BatchTask(
        id=43,
        code="PLAN-2",
        name="指定时间三轮",
        status=TaskStatus.SCHEDULED,
        control_state="RUNNING",
        case_ids=[1],
        total_cases=1,
        schedule_type=TaskScheduleType.SCHEDULED,
        scheduled_at=datetime(2026, 11, 17, 22, 0),
        schedule_active=True,
        repeat_rounds=3,
        created_by="admin",
    )
    run = clone_for_run(plan, current=datetime(2026, 11, 17, 22, 0))
    assert run.round_index == 1
    assert run.repeat_rounds == 3
    assert run.schedule_source_id == 43
    assert "第 1/3 轮" in run.name


def test_clone_next_round_increments_index() -> None:
    previous = BatchTask(
        id=7,
        code="RUN-1",
        name="回归任务",
        status=TaskStatus.COMPLETED,
        control_state="FINISHED",
        case_ids=[1, 2],
        total_cases=2,
        concurrency=2,
        qps_limit=4,
        retry_on_env_error=True,
        max_env_retries=2,
        retry_backoff_seconds=3,
        schedule_type=TaskScheduleType.IMMEDIATE,
        schedule_active=False,
        schedule_source_id=42,
        repeat_rounds=3,
        round_index=1,
    )
    clone = clone_next_round(previous, plan_id=42, round_index=2)
    assert clone.round_index == 2
    assert clone.repeat_rounds == 3
    assert clone.schedule_source_id == 42
    assert clone.round_prev_id == 7
    assert clone.schedule_type == TaskScheduleType.IMMEDIATE
    assert clone.schedule_active is False
    assert clone.status == TaskStatus.PENDING
    assert clone.case_ids == [1, 2]
    assert "第 2/3 轮" in clone.name

    # 名称始终基于原始任务名，不逐轮叠加
    chained = clone_next_round(
        BatchTask(
            code="RUN-2",
            name=f"{previous.name} · 第 2/3 轮",
            status=TaskStatus.COMPLETED,
            control_state="FINISHED",
            case_ids=[1],
            total_cases=1,
            schedule_type=TaskScheduleType.IMMEDIATE,
            schedule_active=False,
            schedule_source_id=42,
            repeat_rounds=3,
            round_index=2,
        ),
        plan_id=42,
        round_index=3,
    )
    assert chained.name == f"{previous.name} · 第 3/3 轮"


@pytest.mark.asyncio
async def test_scheduler_chains_next_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """上一轮正常完成后自动开始下一轮，轮次跑满后不再派生。"""
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr(scheduler, "start", fake_start)
    code = f"ROUNDS-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        root = BatchTask(
            code=code,
            name="连续三轮",
            status=TaskStatus.COMPLETED,
            control_state="FINISHED",
            case_ids=[1],
            total_cases=1,
            schedule_type=TaskScheduleType.IMMEDIATE,
            schedule_active=False,
            repeat_rounds=3,
            round_index=1,
        )
        db.add(root)
        await db.commit()
        root_id = root.id
    created: list[int] = []
    try:
        await scheduler._start_next_round(root_id)
        assert len(started) == 1
        round2_id = started[0]
        created.append(round2_id)
        async with SessionLocal() as db:
            round2 = await db.get(BatchTask, round2_id)
            assert round2 is not None
            assert round2.round_index == 2
            assert round2.repeat_rounds == 3
            assert round2.schedule_source_id == root_id
            # 模拟第二轮跑完
            round2.status = TaskStatus.COMPLETED
            await db.commit()

        await scheduler._start_next_round(round2_id)
        assert len(started) == 2
        round3_id = started[1]
        created.append(round3_id)
        async with SessionLocal() as db:
            round3 = await db.get(BatchTask, round3_id)
            assert round3 is not None
            assert round3.round_index == 3
            assert round3.repeat_rounds == 3
            round3.status = TaskStatus.COMPLETED
            await db.commit()

        # 最后一轮完成后不再派生新轮次
        await scheduler._start_next_round(round3_id)
        assert len(started) == 2
    finally:
        await _cleanup(root_id, *created)


async def _complete_root(rounds: int, name: str = "连续多轮") -> int:
    async with SessionLocal() as db:
        root = BatchTask(
            code=f"{name}-{uuid.uuid4().hex[:6]}",
            name=name,
            status=TaskStatus.COMPLETED,
            control_state="FINISHED",
            case_ids=[1],
            total_cases=1,
            schedule_type=TaskScheduleType.IMMEDIATE,
            schedule_active=False,
            repeat_rounds=rounds,
            round_index=1,
            finished_at=datetime.now(),
        )
        db.add(root)
        await db.commit()
        return int(root.id)


@pytest.mark.asyncio
async def test_start_next_round_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """重复触发（重跑 / 恢复扫描）不会重复派生下一轮。"""
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr(scheduler, "start", fake_start)
    root_id = await _complete_root(2)
    try:
        await scheduler._start_next_round(root_id)
        await scheduler._start_next_round(root_id)
        await scheduler._start_next_round(root_id)
        async with SessionLocal() as db:
            children = (
                (
                    await db.execute(
                        select(BatchTask).where(BatchTask.round_prev_id == root_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(children) == 1
        assert children[0].round_index == 2
        assert children[0].round_prev_id == root_id
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(BatchTask).where(
                    (BatchTask.round_prev_id == root_id) | (BatchTask.id == root_id)
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_start_next_round_retries_after_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建下一轮 transient 失败时会自动重试并最终成功（自愈）。"""
    from billing_qa.services.engine import schedule as schedule_module

    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr(scheduler, "start", fake_start)
    real_clone = schedule_module.clone_next_round
    calls = {"count": 0}

    def flaky_clone(*args: object, **kwargs: object) -> BatchTask:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("模拟创建下一轮失败")
        return real_clone(*args, **kwargs)

    monkeypatch.setattr(schedule_module, "clone_next_round", flaky_clone)
    root_id = await _complete_root(2, name="连续多轮重试")
    try:
        await scheduler._start_next_round(root_id)
        assert calls["count"] >= 2
        assert started, "重试成功后应启动下一轮"
        async with SessionLocal() as db:
            children = (
                (
                    await db.execute(
                        select(BatchTask).where(BatchTask.round_prev_id == root_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(children) == 1
        assert children[0].round_index == 2
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(BatchTask).where(
                    (BatchTask.round_prev_id == root_id) | (BatchTask.id == root_id)
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_start_next_round_does_not_rerun_executed_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对已完成轮次重跑时，不应把已执行过的下一轮再次拉起。"""
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr(scheduler, "start", fake_start)
    root_id = await _complete_root(2, name="连续多轮重跑")
    try:
        async with SessionLocal() as db:
            child = BatchTask(
                code=f"DONE-{uuid.uuid4().hex[:6]}",
                name="连续多轮重跑 · 第 2/2 轮",
                status=TaskStatus.COMPLETED,
                control_state="FINISHED",
                case_ids=[1],
                total_cases=1,
                schedule_type=TaskScheduleType.IMMEDIATE,
                schedule_active=False,
                schedule_source_id=root_id,
                repeat_rounds=2,
                round_index=2,
                round_prev_id=root_id,
            )
            db.add(child)
            await db.commit()

        await scheduler._start_next_round(root_id)
        assert started == []
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(BatchTask).where(
                    (BatchTask.round_prev_id == root_id) | (BatchTask.id == root_id)
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_resume_round_chains_starts_pending_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """下一轮已创建但启动失败/重启后停在 PENDING，恢复扫描会拉起。"""
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr(scheduler, "start", fake_start)
    root_id = await _complete_root(3, name="连续多轮待恢复")
    child_id: int | None = None
    try:
        async with SessionLocal() as db:
            child = BatchTask(
                code=f"PENDING-{uuid.uuid4().hex[:6]}",
                name="连续多轮待恢复 · 第 2/3 轮",
                status=TaskStatus.PENDING,
                control_state="RUNNING",
                case_ids=[1],
                total_cases=1,
                schedule_type=TaskScheduleType.IMMEDIATE,
                schedule_active=False,
                schedule_source_id=root_id,
                repeat_rounds=3,
                round_index=2,
                round_prev_id=root_id,
            )
            db.add(child)
            await db.commit()
            child_id = int(child.id)

        await scheduler.resume_round_chains()
        async with SessionLocal() as db:
            child_status = (
                await db.execute(
                    select(BatchTask.status).where(BatchTask.id == child_id)
                )
            ).scalar_one_or_none()
        # 由本进程恢复，或已被后台扫描启动
        assert child_id in started or child_status != TaskStatus.PENDING.value
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(BatchTask).where(
                    (BatchTask.round_prev_id == root_id) | (BatchTask.id == root_id)
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_resume_round_chains_creates_missing_next_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上一轮已完成但下一轮未创建（进程异常），恢复扫描会补齐并启动。"""
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr(scheduler, "start", fake_start)
    root_id = await _complete_root(2, name="连续多轮补链")
    try:
        # 宽限期外（模拟内联补链已失败一段时间）
        async with SessionLocal() as db:
            root = await db.get(BatchTask, root_id)
            root.finished_at = datetime.now() - timedelta(minutes=5)
            await db.commit()
        await scheduler.resume_round_chains()
        async with SessionLocal() as db:
            children = (
                (
                    await db.execute(
                        select(BatchTask).where(BatchTask.round_prev_id == root_id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(children) == 1
        assert children[0].round_index == 2
        assert (
            int(children[0].id) in started
            or children[0].status != TaskStatus.PENDING.value
        )
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(BatchTask).where(
                    (BatchTask.round_prev_id == root_id) | (BatchTask.id == root_id)
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_run_due_tasks_clones_first_round_for_multi_round_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续多轮的一次性计划：到点先克隆第 1 轮，计划行本身不执行。"""
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr("billing_qa.services.engine.schedule.scheduler.start", fake_start)
    reference = datetime.now().replace(microsecond=0)
    code = f"PLAN-ROUNDS-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        plan = BatchTask(
            code=code,
            name="指定时间三轮",
            status=TaskStatus.SCHEDULED,
            control_state="RUNNING",
            case_ids=[1],
            total_cases=1,
            schedule_type=TaskScheduleType.SCHEDULED,
            scheduled_at=reference - timedelta(seconds=5),
            next_run_at=reference - timedelta(seconds=5),
            schedule_active=True,
            repeat_rounds=3,
        )
        db.add(plan)
        await db.commit()
        plan_id = plan.id
    try:
        await run_due_tasks(reference)
        assert len(started) == 1
        run_id = started[0]
        assert run_id != plan_id
        async with SessionLocal() as db:
            plan = await db.get(BatchTask, plan_id)
            run = await db.get(BatchTask, run_id)
            assert plan is not None and run is not None
            assert plan.status == TaskStatus.COMPLETED
            assert plan.schedule_active is False
            assert plan.next_run_at is None
            assert plan.last_run_at is not None
            assert run.schedule_source_id == plan_id
            assert run.round_index == 1
            assert run.repeat_rounds == 3
    finally:
        await _cleanup(plan_id, *started)


async def _cleanup(*task_ids: int) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(BatchTask).where(BatchTask.id.in_(task_ids)))
        await db.commit()


@pytest.mark.asyncio
async def test_run_due_tasks_fires_one_off_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr("billing_qa.services.engine.schedule.scheduler.start", fake_start)
    reference = datetime.now().replace(microsecond=0)
    code = f"PLAN-ONCE-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        problem = BatchTask(
            code=code,
            name="一次性定时",
            status=TaskStatus.SCHEDULED,
            control_state="RUNNING",
            case_ids=[],
            total_cases=0,
            schedule_type=TaskScheduleType.SCHEDULED,
            scheduled_at=reference - timedelta(seconds=5),
            next_run_at=reference - timedelta(seconds=5),
            schedule_active=True,
        )
        db.add(problem)
        await db.commit()
        problem_id = problem.id
    try:
        await run_due_tasks(reference)
        assert started == [problem_id]
        async with SessionLocal() as db:
            plan = await db.get(BatchTask, problem_id)
            assert plan is not None
            assert plan.next_run_at is None
            assert plan.schedule_active is False
            assert plan.last_run_at is not None
    finally:
        await _cleanup(problem_id)


@pytest.mark.asyncio
async def test_run_due_tasks_clones_periodic_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr("billing_qa.services.engine.schedule.scheduler.start", fake_start)
    reference = datetime(2026, 11, 16, 10, 0)
    code = f"PLAN-DAILY-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        plan = BatchTask(
            code=code,
            name="每日回归",
            status=TaskStatus.SCHEDULED,
            control_state="RUNNING",
            case_ids=[1],
            total_cases=1,
            schedule_type=TaskScheduleType.DAILY,
            schedule_time="09:00",
            next_run_at=reference - timedelta(minutes=1),
            schedule_active=True,
        )
        db.add(plan)
        await db.commit()
        plan_id = plan.id
    run_ids: list[int] = []
    try:
        await run_due_tasks(reference)
        assert len(started) == 1
        run_id = started[0]
        assert run_id != plan_id
        run_ids.append(run_id)
        async with SessionLocal() as db:
            plan = await db.get(BatchTask, plan_id)
            run = await db.get(BatchTask, run_id)
            assert plan is not None and run is not None
            assert plan.schedule_type == TaskScheduleType.DAILY
            assert plan.status == TaskStatus.SCHEDULED
            assert plan.next_run_at == datetime(2026, 11, 17, 9, 0)
            assert plan.last_run_at == reference
            assert run.schedule_source_id == plan_id
            assert run.schedule_type == TaskScheduleType.IMMEDIATE
            assert run.case_ids == [1]
    finally:
        await _cleanup(plan_id, *run_ids)


@pytest.mark.asyncio
async def test_periodic_plan_skips_when_previous_run_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[int] = []

    async def fake_start(task_id: int, **_: object) -> bool:
        started.append(task_id)
        return True

    monkeypatch.setattr("billing_qa.services.engine.schedule.scheduler.start", fake_start)
    reference = datetime(2026, 11, 16, 10, 0)
    code = f"PLAN-BUSY-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as db:
        plan = BatchTask(
            code=code,
            name="每天回归（上一轮未结束）",
            status=TaskStatus.SCHEDULED,
            control_state="RUNNING",
            case_ids=[1],
            total_cases=1,
            schedule_type=TaskScheduleType.DAILY,
            schedule_time="09:00",
            next_run_at=reference - timedelta(minutes=1),
            schedule_active=True,
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        busy = clone_for_run(plan, current=reference - timedelta(hours=2))
        busy.status = TaskStatus.RUNNING
        db.add(busy)
        await db.commit()
        plan_id, busy_id = plan.id, busy.id
    try:
        await run_due_tasks(reference)
        assert started == []
        async with SessionLocal() as db:
            plan = await db.get(BatchTask, plan_id)
            assert plan is not None
            assert plan.next_run_at == datetime(2026, 11, 17, 9, 0)
            clones = (
                (
                    await db.execute(
                        select(BatchTask).where(BatchTask.schedule_source_id == plan_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(clones) == 1
    finally:
        await _cleanup(plan_id, busy_id)
