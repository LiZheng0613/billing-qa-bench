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
from billing_qa.services.engine.schedule import clone_for_run, run_due_tasks

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
