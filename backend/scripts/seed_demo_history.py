"""写入演示用批量任务与执行历史（真实调用 Mock 服务产生 Trace / 裁判数据）。

用法（在 backend 目录下，需先启动 Mock 8001/8002）：
    python scripts/seed_demo_history.py

脚本幂等：会先删除 `DEMO-TASK-*` 及其执行记录，再重新生成：
- 全量回归 / 单轮与契约 / 动态多轮 三条已完成任务；
- 一条执行到中途后终止的「续做」演示任务；
- 一条每日定时任务（未来触发，不消耗执行）。
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


async def _purge_demo_tasks(db: Any) -> None:
    tasks = (
        (await db.execute(select(BatchTask).where(BatchTask.code.like(f"{TASK_PREFIX}%"))))
        .scalars()
        .all()
    )
    task_ids = [int(task.id) for task in tasks]
    if not task_ids:
        return
    execution_ids = (
        (await db.execute(select(ExecutionRecord.id).where(ExecutionRecord.task_id.in_(task_ids))))
        .scalars()
        .all()
    )
    if execution_ids:
        await db.execute(delete(FactResult).where(FactResult.execution_id.in_(execution_ids)))
        await db.execute(delete(JudgeResult).where(JudgeResult.execution_id.in_(execution_ids)))
        await db.execute(delete(TurnTrace).where(TurnTrace.execution_id.in_(execution_ids)))
        await db.execute(delete(ExecutionRecord).where(ExecutionRecord.id.in_(execution_ids)))
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
) -> int:
    """登记一条未来触发的每日任务，展示定时计划与下次触发时间。"""
    next_run = (datetime.now() + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    async with SessionLocal() as db:
        task = BatchTask(
            code=code,
            name=name,
            description=description,
            status="SCHEDULED",
            control_state="FINISHED",
            environment_id=environment_id,
            case_ids=[item[0] for item in cases],
            total_cases=len(cases),
            concurrency=3,
            qps_limit=5,
            schedule_type="DAILY",
            schedule_time="09:00",
            schedule_active=True,
            next_run_at=next_run,
            created_by="system",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = int(task.id)
    print(f"[{code}] scheduled task_id={task_id} next_run_at={next_run}", flush=True)
    return task_id


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

        all_cases = await _case_rows(db)
        single_cases = await _case_rows(db, TestCase.case_type == "SINGLE")
        dynamic_cases = await _case_rows(db, TestCase.case_type == "DYNAMIC_MULTI")

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
        description="每天 09:00 自动执行核心账单用例，演示定时计划与下次触发时间。",
        environment_id=environment_id,
        cases=single_cases[:5],
    )
    print("演示历史数据写入完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
