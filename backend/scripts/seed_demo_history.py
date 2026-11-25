"""写入演示用批量任务与执行历史（真实调用 Mock 服务产生 Trace / 裁判数据）。

用法（在 backend 目录下，需先启动 Mock 8001/8002）：
    python scripts/seed_demo_history.py

脚本幂等：会先删除 `DEMO-TASK-*` 及其执行记录，再重新生成：
- 全量回归 / 单轮与契约 / 动态多轮 三条已完成任务；
- 一条执行到中途后终止的「续做」演示任务；
- 一条错误策略任务（LLM 错误自动重试 + 已停用环境 BUSINESS 错误）；
- 一条在途终止任务（慢响应执行中被取消，落 ABORTED 执行）；
- 一条草稿试跑（SANDBOX，不挂任务，不计入趋势）；
- 一条定时计划组：每日 / 每周两条停在「已停止」（演示库不自动触发、不消耗执行），
  一条一次性计划设在远期等待触发；需要看真实触发时在页面点「启用计划」。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, select

from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import ExecutionSource
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.gold_query import FactResult
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.services.engine.executor import CaseExecutor
from billing_qa.services.engine.metrics import apply_metrics_to_task, compute_task_metrics

TASK_PREFIX = "DEMO-TASK-"
# 慢响应用例只用于「在途终止」演示：单跑约 30s，不放进批量任务。
SLOW_CASE_CODE = "DEMO-SINGLE-SLOW-ABORT"
RETRY_CASE_CODE = "DEMO-SINGLE-ERROR-LLM"
BUSINESS_CASE_CODE = "DEMO-SINGLE-ERROR-BUSINESS"
SANDBOX_CASE_CODE = "DEMO-DRAFT-001"


async def _case_rows(db: Any, *conditions: Any) -> list[tuple[int, int, dict[str, Any], str]]:
    cases = (
        (
            await db.execute(
                select(TestCase)
                .where(TestCase.status == "ACTIVE", *conditions)
                .order_by(TestCase.id)
            )
        )
        .scalars()
        .all()
    )
    rows: list[tuple[int, int, dict[str, Any], str]] = []
    for case in cases:
        revision = (
            await db.execute(
                select(TestCaseRevision).where(
                    TestCaseRevision.case_id == case.id,
                    TestCaseRevision.version == case.current_version,
                )
            )
        ).scalar_one_or_none()
        if revision is None:
            continue
        rows.append((int(case.id), int(revision.version), revision.snapshot, case.code))
    return rows


async def _case_row_by_code(
    db: Any, code: str, *, active_only: bool = True
) -> tuple[int, int, dict[str, Any], str] | None:
    """按编号取单条用例的当前版本快照（草稿试跑需要读非 ACTIVE 用例）。"""
    conditions: list[Any] = [TestCase.code == code]
    if active_only:
        conditions.append(TestCase.status == "ACTIVE")
    case = (await db.execute(select(TestCase).where(*conditions))).scalar_one_or_none()
    if case is None:
        return None
    revision = (
        await db.execute(
            select(TestCaseRevision).where(
                TestCaseRevision.case_id == case.id,
                TestCaseRevision.version == case.current_version,
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        return None
    return (int(case.id), int(revision.version), revision.snapshot, case.code)


async def _purge_demo_tasks(db: Any) -> None:
    tasks = (
        (await db.execute(select(BatchTask).where(BatchTask.code.like(f"{TASK_PREFIX}%"))))
        .scalars()
        .all()
    )
    plan_ids = [int(task.id) for task in tasks]
    # 计划到点触发时后端会克隆出自动编号的执行任务（code=BATCH*、schedule_source_id=计划 id）。
    # 重跑脚本时一并清掉，否则「幂等」只对计划行成立，克隆出来的任务会越积越多。
    cloned: list[BatchTask] = []
    if plan_ids:
        cloned = list(
            (
                await db.execute(
                    select(BatchTask).where(BatchTask.schedule_source_id.in_(plan_ids))
                )
            )
            .scalars()
            .all()
        )
    task_ids = sorted({int(task.id) for task in tasks} | {int(task.id) for task in cloned})
    execution_ids: list[int] = []
    if task_ids:
        execution_ids = list(
            (
                await db.execute(
                    select(ExecutionRecord.id).where(ExecutionRecord.task_id.in_(task_ids))
                )
            )
            .scalars()
            .all()
        )
    # 草稿试跑挂在任务之外（task_id 为空），重跑脚本时同样要清掉，
    # 否则每次重建都会多出一条 SANDBOX 执行。只清理演示用例的试跑记录。
    demo_case_ids = select(TestCase.id).where(TestCase.code.like("DEMO-%"))
    execution_ids.extend(
        (
            await db.execute(
                select(ExecutionRecord.id).where(
                    ExecutionRecord.source == ExecutionSource.SANDBOX,
                    ExecutionRecord.case_id.in_(demo_case_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    if execution_ids:
        await db.execute(delete(FactResult).where(FactResult.execution_id.in_(execution_ids)))
        await db.execute(delete(JudgeResult).where(JudgeResult.execution_id.in_(execution_ids)))
        await db.execute(delete(TurnTrace).where(TurnTrace.execution_id.in_(execution_ids)))
        await db.execute(delete(ExecutionRecord).where(ExecutionRecord.id.in_(execution_ids)))
    if task_ids:
        await db.execute(delete(BatchTask).where(BatchTask.id.in_(task_ids)))


async def _shift_history(task_id: int, shift: timedelta) -> None:
    """把执行记录与轨迹时间整体前移，制造历史趋势数据。"""
    if not shift:
        return
    async with SessionLocal() as db:
        records = (
            (await db.execute(select(ExecutionRecord).where(ExecutionRecord.task_id == task_id)))
            .scalars()
            .all()
        )
        for record in records:
            record.started_at = record.started_at - shift
            if record.finished_at is not None:
                record.finished_at = record.finished_at - shift
        execution_ids = [record.id for record in records]
        if execution_ids:
            turns = (
                (
                    await db.execute(
                        select(TurnTrace).where(TurnTrace.execution_id.in_(execution_ids))
                    )
                )
                .scalars()
                .all()
            )
            for turn in turns:
                turn.created_at = turn.created_at - shift
            judges = (
                (
                    await db.execute(
                        select(JudgeResult).where(JudgeResult.execution_id.in_(execution_ids))
                    )
                )
                .scalars()
                .all()
            )
            for judge in judges:
                judge.created_at = judge.created_at - shift
        await db.commit()


async def _run_task(
    *,
    code: str,
    name: str,
    description: str,
    environment_id: int,
    cases: list[tuple[int, int, dict[str, Any], str]],
    started_ago: timedelta,
    concurrency: int = 4,
    qps_limit: float = 20.0,
    enable_self_check: bool = False,
) -> int:
    async with SessionLocal() as db:
        task = BatchTask(
            code=code,
            name=name,
            description=description,
            status="PENDING",
            control_state="RUNNING",
            environment_id=environment_id,
            case_ids=[item[0] for item in cases],
            total_cases=len(cases),
            concurrency=concurrency,
            qps_limit=qps_limit,
            enable_self_check=enable_self_check,
            # 历史回归任务不开自动重试：重试策略由 DEMO-TASK-006 单独演示，
            # 避免任务配置写「重试 2 次」而执行记录只有 attempt 1。
            retry_on_error=False,
            max_error_retries=0,
            retry_on_env_error=False,
            max_env_retries=0,
            schedule_type="IMMEDIATE",
            created_by="system",
            started_at=datetime.now(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)
        actual_started = task.started_at

    for index, (case_id, version, snapshot, case_code) in enumerate(cases, start=1):
        await CaseExecutor().execute(
            case_id=case_id,
            case_version=version,
            snapshot=snapshot,
            task_id=task_id,
            source=ExecutionSource.BATCH,
            environment_id=environment_id,
            attempt_no=1,
            enable_self_check=enable_self_check,
        )
        print(f"[{code}] {index}/{len(cases)} {case_code}", flush=True)

    async with SessionLocal() as db:
        task = await db.get(BatchTask, task_id)
        assert task is not None
        actual_finished = datetime.now()
        task.status = "COMPLETED"
        task.control_state = "FINISHED"
        task.finished_at = actual_finished
        metrics = await compute_task_metrics(db, task)
        apply_metrics_to_task(task, metrics)
        duration = actual_finished - (actual_started or actual_finished)
        backdated_start = actual_finished - started_ago
        task.started_at = backdated_start
        task.finished_at = backdated_start + duration
        task.last_run_at = backdated_start
        task.created_at = backdated_start
        summary = metrics.model_dump(mode="json")
        summary["status"] = "COMPLETED"
        summary["control_state"] = "FINISHED"
        task.summary = summary
        await db.commit()

    shift = actual_started - backdated_start if actual_started is not None else timedelta(0)
    if shift:
        await _shift_history(task_id, shift)
    print(f"[{code}] completed task_id={task_id}", flush=True)
    return task_id


async def _run_aborted_task(
    *,
    code: str,
    name: str,
    description: str,
    environment_id: int,
    cases: list[tuple[int, int, dict[str, Any], str]],
    started_ago: timedelta,
    stop_after: int = 3,
) -> int:
    """执行前几条后模拟人工终止，保留未完成用例供「续做」演示。"""
    async with SessionLocal() as db:
        task = BatchTask(
            code=code,
            name=name,
            description=description,
            status="PENDING",
            control_state="RUNNING",
            environment_id=environment_id,
            case_ids=[item[0] for item in cases],
            total_cases=len(cases),
            concurrency=2,
            qps_limit=10,
            retry_on_error=False,
            max_error_retries=0,
            retry_on_env_error=False,
            max_env_retries=0,
            schedule_type="IMMEDIATE",
            created_by="system",
            started_at=datetime.now(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)
        actual_started = task.started_at

    executed = cases[: max(1, stop_after)]
    for index, (case_id, version, snapshot, case_code) in enumerate(executed, start=1):
        await CaseExecutor().execute(
            case_id=case_id,
            case_version=version,
            snapshot=snapshot,
            task_id=task_id,
            source=ExecutionSource.BATCH,
            environment_id=environment_id,
            attempt_no=1,
        )
        print(f"[{code}] {index}/{len(cases)} {case_code}", flush=True)

    async with SessionLocal() as db:
        task = await db.get(BatchTask, task_id)
        assert task is not None
        task.status = "ABORTED"
        task.control_state = "ABORTED"
        backdated_start = datetime.now() - started_ago
        metrics = await compute_task_metrics(db, task)
        apply_metrics_to_task(task, metrics)
        task.started_at = backdated_start
        task.finished_at = backdated_start + timedelta(minutes=2)
        task.error_message = "演示：执行到中途后人工终止，剩余用例可点击续做"
        summary = metrics.model_dump(mode="json")
        summary["status"] = "ABORTED"
        summary["control_state"] = "ABORTED"
        summary["error_message"] = task.error_message
        task.summary = summary
        await db.commit()

    if actual_started is not None:
        await _shift_history(task_id, actual_started - backdated_start)
    print(f"[{code}] aborted task_id={task_id} executed={len(executed)}/{len(cases)}", flush=True)
    return task_id


async def _create_scheduled_task(
    *,
    code: str,
    name: str,
    description: str,
    environment_id: int,
    cases: list[tuple[int, int, dict[str, Any], str]],
    schedule_type: str = "DAILY",
    schedule_time: str = "09:00",
    schedule_weekdays: list[int] | None = None,
    scheduled_at: datetime | None = None,
    stopped: bool = False,
) -> int:
    """登记一条未来触发的定时计划，展示定时配置与下次触发时间。

    ``stopped=True`` 用于演示库：计划配置（每日 / 每周时刻）完整保留，但停在
    「已停止」——``schedule_active=False`` + ``next_run_at=None`` + ``status=ABORTED``，
    与 ``PUT /tasks/{id}/schedule``（``active=false``）写出的状态一致。
    后端调度器只派发 ``schedule_active=True`` 且 ``next_run_at`` 到期的计划，
    因此演示库不会在后台每天自动追加执行、把文档里的基线数字刷走；
    想看真实触发，在详情页点「启用计划」即可恢复。
    """
    now_local = datetime.now()
    if schedule_type == "WEEKLY":
        days = sorted(set(schedule_weekdays or [1, 4]))
        dayset = set(days)
        for offset in range(1, 8):
            candidate = now_local + timedelta(days=offset)
            if candidate.isoweekday() in dayset:
                next_run = candidate.replace(hour=9, minute=0, second=0, microsecond=0)
                break
        else:
            next_run = (now_local + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
    elif schedule_type == "SCHEDULED":
        next_run = scheduled_at or (now_local + timedelta(days=2)).replace(
            hour=10, minute=30, second=0, microsecond=0
        )
    else:
        next_run = (now_local + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
    async with SessionLocal() as db:
        task = BatchTask(
            code=code,
            name=name,
            description=description,
            status="ABORTED" if stopped else "SCHEDULED",
            control_state="ABORTED" if stopped else "FINISHED",
            environment_id=environment_id,
            case_ids=[item[0] for item in cases],
            total_cases=len(cases),
            concurrency=3,
            qps_limit=5,
            schedule_type=schedule_type,
            schedule_time=schedule_time,
            schedule_weekdays=schedule_weekdays if schedule_type == "WEEKLY" else None,
            scheduled_at=next_run if schedule_type == "SCHEDULED" else None,
            schedule_active=not stopped,
            next_run_at=None if stopped else next_run,
            # 停止计划时后端（POST /tasks/{id}/stop-plan）会落 finished_at，
            # 计划从未开跑，started_at 保持为空。
            finished_at=now_local if stopped else None,
            created_by="system",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)
    state = "stopped" if stopped else f"next_run_at={next_run}"
    print(
        f"[{code}] scheduled({schedule_type}) task_id={task_id} {state}",
        flush=True,
    )
    return task_id


async def _run_error_policy_task(
    *,
    code: str,
    name: str,
    description: str,
    # 环境留空时按用例自身绑定执行；BUSINESS 演示正是借此让「已停用环境」的用例
    # 在批量任务里真实命中 context 校验，而不是绕过调度器手工构造错误。
    environment_id: int | None,
    retry_case: tuple[int, int, dict[str, Any], str],
    business_case: tuple[int, int, dict[str, Any], str],
    started_ago: timedelta,
    max_error_retries: int = 2,
) -> int:
    """错误策略演示：LLM 错误自动重试（attempt_no=2 + RETRY），BUSINESS 错误不重试。

    与调度器的真实重试路径一致：首次失败以 ``BATCH`` 落库，重试以 ``RETRY`` 落库并
    递增 ``attempt_no``；用例内部异常不会让任务失败，任务仍以 COMPLETED 收口。
    """
    async with SessionLocal() as db:
        task = BatchTask(
            code=code,
            name=name,
            description=description,
            status="PENDING",
            control_state="RUNNING",
            environment_id=environment_id,
            case_ids=[retry_case[0], business_case[0]],
            total_cases=2,
            concurrency=2,
            qps_limit=10,
            retry_on_error=True,
            max_error_retries=max_error_retries,
            retry_backoff_seconds=3.0,
            retry_on_env_error=True,
            max_env_retries=2,
            schedule_type="IMMEDIATE",
            created_by="system",
            started_at=datetime.now(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)
        actual_started = task.started_at

    case_id, version, snapshot, case_code = retry_case
    # 第 1 次尝试：源 BATCH，模型 5xx → LLM 错误。
    await CaseExecutor().execute(
        case_id=case_id,
        case_version=version,
        snapshot=snapshot,
        task_id=task_id,
        source=ExecutionSource.BATCH,
        environment_id=environment_id,
        attempt_no=1,
    )
    print(f"[{code}] 1/2 {case_code} attempt=1", flush=True)
    # 第 2 次尝试：源 RETRY，同一快照重跑。
    await CaseExecutor().execute(
        case_id=case_id,
        case_version=version,
        snapshot=snapshot,
        task_id=task_id,
        source=ExecutionSource.RETRY,
        environment_id=environment_id,
        attempt_no=2,
    )
    print(f"[{code}] 2/2 {case_code} attempt=2 (RETRY)", flush=True)

    # BUSINESS 错误：不传环境，走用例自身绑定的已停用环境。
    await CaseExecutor().execute(
        case_id=business_case[0],
        case_version=business_case[1],
        snapshot=business_case[2],
        task_id=task_id,
        source=ExecutionSource.BATCH,
        environment_id=None,
        attempt_no=1,
    )
    print(f"[{code}] {BUSINESS_CASE_CODE} attempt=1 (BUSINESS)", flush=True)

    async with SessionLocal() as db:
        task = await db.get(BatchTask, task_id)
        assert task is not None
        actual_finished = datetime.now()
        task.status = "COMPLETED"
        task.control_state = "FINISHED"
        task.finished_at = actual_finished
        metrics = await compute_task_metrics(db, task)
        apply_metrics_to_task(task, metrics)
        duration = actual_finished - (actual_started or actual_finished)
        backdated_start = actual_finished - started_ago
        task.started_at = backdated_start
        task.finished_at = backdated_start + duration
        task.last_run_at = backdated_start
        task.created_at = backdated_start
        summary = metrics.model_dump(mode="json")
        summary["status"] = "COMPLETED"
        summary["control_state"] = "FINISHED"
        task.summary = summary
        await db.commit()

    if actual_started is not None:
        await _shift_history(task_id, actual_started - backdated_start)
    print(f"[{code}] completed task_id={task_id}", flush=True)
    return task_id


async def _run_cancelled_task(
    *,
    code: str,
    name: str,
    description: str,
    environment_id: int,
    case: tuple[int, int, dict[str, Any], str],
    started_ago: timedelta,
    cancel_after: float = 2.0,
) -> int:
    """在途终止：慢响应执行中被取消，执行记录以 ABORTED 收口。"""
    async with SessionLocal() as db:
        task = BatchTask(
            code=code,
            name=name,
            description=description,
            status="PENDING",
            control_state="RUNNING",
            environment_id=environment_id,
            case_ids=[case[0]],
            total_cases=1,
            concurrency=1,
            qps_limit=5,
            schedule_type="IMMEDIATE",
            created_by="system",
            started_at=datetime.now(),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)
        actual_started = task.started_at

    case_id, version, snapshot, case_code = case
    runner = asyncio.create_task(
        CaseExecutor().execute(
            case_id=case_id,
            case_version=version,
            snapshot=snapshot,
            task_id=task_id,
            source=ExecutionSource.BATCH,
            environment_id=environment_id,
            attempt_no=1,
        )
    )
    await asyncio.sleep(cancel_after)
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    print(f"[{code}] {case_code} cancelled in flight", flush=True)

    async with SessionLocal() as db:
        task = await db.get(BatchTask, task_id)
        assert task is not None
        task.status = "ABORTED"
        task.control_state = "ABORTED"
        backdated_start = datetime.now() - started_ago
        metrics = await compute_task_metrics(db, task)
        apply_metrics_to_task(task, metrics)
        task.started_at = backdated_start
        task.finished_at = backdated_start + timedelta(seconds=cancel_after)
        task.error_message = "演示：慢响应在途执行被人工终止，保留 ABORTED 执行记录"
        summary = metrics.model_dump(mode="json")
        summary["status"] = "ABORTED"
        summary["control_state"] = "ABORTED"
        summary["error_message"] = task.error_message
        task.summary = summary
        await db.commit()

    if actual_started is not None:
        await _shift_history(task_id, actual_started - backdated_start)
    print(f"[{code}] aborted task_id={task_id}", flush=True)
    return task_id


async def _run_sandbox_case(
    *, case: tuple[int, int, dict[str, Any], str]
) -> int:
    """草稿试跑：source=SANDBOX，不挂任务，不计入趋势 / 通过率。"""
    case_id, version, snapshot, case_code = case
    environment_id = snapshot.get("environment_id")
    execution_id = await CaseExecutor().execute(
        case_id=case_id,
        case_version=version,
        snapshot=snapshot,
        task_id=None,
        source=ExecutionSource.SANDBOX,
        environment_id=environment_id,
        attempt_no=1,
    )
    print(
        f"[SANDBOX] {case_code} execution_id={execution_id}", flush=True
    )
    return execution_id


async def main() -> None:
    async with SessionLocal() as db:
        env = (
            await db.execute(select(Environment).where(Environment.code == "QA-MOCK-JSON"))
        ).scalar_one_or_none()
        if env is None:
            env = (
                await db.execute(select(Environment).where(Environment.code == "QA-MOCK"))
            ).scalar_one_or_none()
        if env is None:
            raise SystemExit("未找到 QA-MOCK-JSON / QA-MOCK 环境，请先执行 init_db --demo")
        environment_id = int(env.id)

        all_cases = await _case_rows(
            db,
            TestCase.environment_id == environment_id,
            TestCase.code != SLOW_CASE_CODE,
        )
        single_cases = await _case_rows(
            db,
            TestCase.case_type == "SINGLE",
            TestCase.environment_id == environment_id,
            TestCase.code != SLOW_CASE_CODE,
        )
        dynamic_cases = await _case_rows(
            db, TestCase.case_type == "DYNAMIC_MULTI", TestCase.environment_id == environment_id
        )
        retry_case = await _case_row_by_code(db, RETRY_CASE_CODE)
        business_case = await _case_row_by_code(db, BUSINESS_CASE_CODE)
        slow_case = await _case_row_by_code(db, SLOW_CASE_CODE)
        sandbox_case = await _case_row_by_code(db, SANDBOX_CASE_CODE, active_only=False)

        if not all_cases:
            raise SystemExit("未找到 ACTIVE 用例，请先执行 init_db --demo")

        await _purge_demo_tasks(db)
        await db.commit()

    await _run_task(
        code=f"{TASK_PREFIX}001",
        name="电信计费全量回归（演示）",
        description="覆盖账单、套餐余量、漫游、增值业务、欠费、发票、宽带、积分及契约/熔断场景。",
        environment_id=environment_id,
        cases=all_cases,
        started_ago=timedelta(days=3),
        concurrency=5,
        qps_limit=30,
    )
    await _run_task(
        code=f"{TASK_PREFIX}002",
        name="单轮与答案契约专项（演示）",
        description="聚焦单轮问答与 ANSWER_CONTRACT 判分，用于趋势与 Run 对比演示；失败项自动触发追问自检。",
        environment_id=environment_id,
        cases=single_cases,
        started_ago=timedelta(hours=30),
        concurrency=4,
        qps_limit=20,
        enable_self_check=True,
    )
    await _run_task(
        code=f"{TASK_PREFIX}003",
        name="动态多轮与熔断专项（演示）",
        description="覆盖动态多轮目标达成、最大轮次熔断与复读熔断。",
        environment_id=environment_id,
        cases=dynamic_cases,
        started_ago=timedelta(hours=2),
        concurrency=2,
        qps_limit=10,
    )
    await _run_aborted_task(
        code=f"{TASK_PREFIX}004",
        name="中断与续做演示（演示）",
        description="执行 3 条后模拟人工终止，剩余用例可在任务详情点击「续做」继续跑完。",
        environment_id=environment_id,
        cases=all_cases[:5],
        started_ago=timedelta(hours=6),
        stop_after=3,
    )
    await _create_scheduled_task(
        code=f"{TASK_PREFIX}005",
        name="每日账单回归（定时演示）",
        description=(
            "每天 09:00 执行核心账单用例，演示每日计划；演示库中停在「已停止」，"
            "避免调度器每天自动追加执行，点「启用计划」即可恢复真实触发。"
        ),
        environment_id=environment_id,
        cases=single_cases[:5],
        schedule_type="DAILY",
        schedule_time="09:00",
        stopped=True,
    )
    if retry_case is not None and business_case is not None:
        await _run_error_policy_task(
            code=f"{TASK_PREFIX}006",
            name="错误策略与自动重试（演示）",
            description=(
                "任务不指定环境、按用例绑定执行：LLM 5xx 触发自动重试（第 2 次以 RETRY 落库），"
                "绑定已停用环境的 BUSINESS 错误不进入自动重试。"
            ),
            environment_id=None,
            retry_case=retry_case,
            business_case=business_case,
            started_ago=timedelta(hours=9),
        )
    if slow_case is not None:
        await _run_cancelled_task(
            code=f"{TASK_PREFIX}007",
            name="在途终止与 ABORTED（演示）",
            description="慢响应执行期间人工终止任务，执行记录以 ABORTED 收口。",
            environment_id=environment_id,
            case=slow_case,
            started_ago=timedelta(hours=4),
        )
    await _create_scheduled_task(
        code=f"{TASK_PREFIX}008",
        name="每周计费回归（定时演示）",
        description=(
            "每周一、周四 09:00 执行核心账单用例，演示每周计划；演示库中停在「已停止」，"
            "避免调度器每周自动追加执行，点「启用计划」即可恢复真实触发。"
        ),
        environment_id=environment_id,
        cases=single_cases[:4],
        schedule_type="WEEKLY",
        schedule_time="09:00",
        schedule_weekdays=[1, 4],
        stopped=True,
    )
    await _create_scheduled_task(
        code=f"{TASK_PREFIX}009",
        name="版本发布前一次性回归（定时演示）",
        description="指定时间执行一次发布前回归，演示一次性定时计划；触发时间设在远期，演示期间不消耗执行。",
        environment_id=environment_id,
        cases=single_cases[:3],
        schedule_type="SCHEDULED",
        schedule_time="10:30",
        scheduled_at=datetime(2027, 6, 1, 10, 30),
    )
    if sandbox_case is not None:
        await _run_sandbox_case(case=sandbox_case)
    print("演示历史数据写入完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
