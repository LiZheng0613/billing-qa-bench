"""端到端冒烟脚本：跑通全部演示用例并校验状态分布。

用法（backend 目录）：
    python scripts/e2e_smoke.py
前置：Mock LLM(8001) / Mock 被测系统(8002) 已启动，且已执行 init_db --demo。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# 允许直接 `python scripts/e2e_smoke.py` 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from billing_qa.core.control_store import control_store
from billing_qa.db.session import SessionLocal, engine
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.services.engine.metrics import compute_task_metrics
from billing_qa.services.engine.scheduler import scheduler

EXPECTED = {
    "DEMO-SINGLE-001": "PASS",
    "DEMO-SINGLE-FAIL": "FAIL",
    "DEMO-STATIC-001": "PASS",
    "DEMO-DYNAMIC-001": "PASS",
    "DEMO-DYNAMIC-MAXTURNS": "MAX_TURNS_EXCEEDED",
}


async def main() -> int:
    await control_store.connect()
    async with SessionLocal() as db:
        cases = (
            await db.execute(
                select(TestCase)
                .where(TestCase.deleted_at.is_(None), TestCase.code.in_(list(EXPECTED)))
                .order_by(TestCase.id)
            )
        ).scalars().all()
        if len(cases) < len(EXPECTED):
            print("缺少演示用例，请先执行: python -m app.db.init_db --demo")
            return 2
        task = BatchTask(
            code=f"SMOKE-{int(time.time())}",
            name="端到端冒烟任务",
            description="脚本自动创建，覆盖单轮/静态多轮/动态多轮/熔断/失败",
            status="PENDING",
            control_state="RUNNING",
            case_ids=[case.id for case in cases],
            total_cases=len(cases),
            concurrency=3,
            qps_limit=10,
            created_by="smoke",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    print(f"任务已创建 id={task_id}，开始执行 {len(cases)} 条用例 ...")
    started = time.perf_counter()
    await scheduler.start(task_id)

    while True:
        await asyncio.sleep(1)
        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            if task is None:
                print("任务丢失")
                return 2
            if task.status in {"COMPLETED", "ABORTED", "FAILED"}:
                break
            print(
                f"  进度 {task.finished_cases}/{task.total_cases} "
                f"pass={task.pass_count} fail={task.fail_count} err={task.error_count}"
            )

    elapsed = time.perf_counter() - started
    async with SessionLocal() as db:
        cases = {case.id: case for case in (await db.execute(select(TestCase))).scalars().all()}
        records = (
            await db.execute(
                select(ExecutionRecord)
                .where(ExecutionRecord.task_id == task_id)
                .order_by(ExecutionRecord.id)
            )
        ).scalars().all()
        metrics = await compute_task_metrics(db, task)

    failures: list[str] = []
    print("\n=== 执行结果 ===")
    for record in records:
        case = cases.get(record.case_id)
        code = case.code if case else str(record.case_id)
        expected = EXPECTED.get(code)
        mark = "OK" if expected == record.status else "MISMATCH"
        if expected and expected != record.status:
            failures.append(f"{code}: 期望 {expected} 实际 {record.status} ({record.error_message})")
        print(
            f"[{mark}] {code:<24} status={record.status:<20} score={record.total_score} "
            f"turns={record.turns_used} latency={record.latency_ms}ms ttft={record.ttft_ms}ms"
        )

    print(
        f"\n汇总: status={task.status} progress={metrics.progress}% pass_rate={metrics.pass_rate}% "
        f"avg_ttft={metrics.avg_ttft_ms}ms p95={metrics.p95_latency_ms}ms "
        f"tokens(sim/judge)={metrics.simulator_tokens}/{metrics.judge_tokens} "
        f"耗时={elapsed:.1f}s"
    )
    print("状态分布:", metrics.status_distribution.model_dump())

    await scheduler.shutdown()
    await control_store.close()
    await engine.dispose()

    if failures:
        print("\n=== 失败断言 ===")
        for item in failures:
            print(" -", item)
        return 1
    print("\n端到端冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
