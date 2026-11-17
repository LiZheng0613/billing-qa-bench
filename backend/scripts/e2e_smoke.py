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
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.services.adapter.session import (
    CLIENT_GENERATED,
    init_session_id,
    resolve_session_mode,
    session_updates_from_response,
)
from billing_qa.services.adapter.target_api import EnvContext, TargetApiAdapter
from billing_qa.services.engine.metrics import compute_task_metrics
from billing_qa.services.engine.scheduler import scheduler

EXPECTED = {
    "DEMO-SINGLE-001": "PASS",
    "DEMO-SINGLE-FAIL": "FAIL",
    "DEMO-STATIC-001": "PASS",
    "DEMO-DYNAMIC-001": "PASS",
    "DEMO-DYNAMIC-MAXTURNS": "MAX_TURNS_EXCEEDED",
}


async def _check_session_binding() -> list[str]:
    """独立校验：用例变量注入 headers/body，客户端生成会话贯穿多轮。"""
    async with SessionLocal() as db:
        env = (
            await db.execute(select(Environment).where(Environment.code == "QA-MOCK"))
        ).scalars().first()
        base_url = (env.base_url if env else "http://127.0.0.1:8002").rstrip("/")
        api_path = (env.api_path if env else "/api/v1/chat") or "/api/v1/chat"

    ctx = EnvContext(
        id=None,
        base_url=base_url,
        health_path="/health",
        api_method="POST",
        api_path=api_path,
        headers_template={"X-User-Id": "{{user_id}}"},
        body_template={
            "query": "{{query}}",
            "conversationId": "{{session_id}}",
            "user_id": "{{user_id}}",
            "channel": "{{channel}}",
            "history": "{{history}}",
        },
        is_stream=False,
        extractors=None,
        pre_hook=None,
        timeout_seconds=15,
        verify_ssl=True,
        variables={"channel": "env-pool"},
        session_mode=CLIENT_GENERATED,
    )
    # 用例变量应覆盖同名的环境变量池取值
    case_vars: dict[str, object] = {"user_id": "13800138000", "channel": "smoke"}
    mode = resolve_session_mode("INHERIT", ctx.session_mode, case_vars)
    session_id = init_session_id(mode, case_vars, 9001)
    if mode != CLIENT_GENERATED or not session_id:
        return [f"会话模式解析异常: mode={mode} session={session_id}"]

    adapter = TargetApiAdapter(ctx)
    history: list[dict[str, str]] = []
    query1 = "帮我查一下 13800138000 本月账单总额"
    query2 = "其中超套流量费是多少？"
    try:
        first = await adapter.chat(
            query1, session_id=session_id, history=history, extra_variables=case_vars
        )
        session_id = session_updates_from_response(mode, session_id, first.session_id) or session_id
        history = [
            {"role": "user", "content": query1},
            {"role": "assistant", "content": first.final_answer},
        ]
        second = await adapter.chat(
            query2, session_id=session_id, history=history, extra_variables=case_vars
        )
    except Exception as exc:  # 冒烟脚本需要把任何失败转成断言
        return [f"请求变量冒烟异常（Mock 被测系统需在 8002 运行）: {exc}"]

    problems: list[str] = []
    request_body = (second.request_payload or {}).get("body") or {}
    request_headers = (second.request_payload or {}).get("headers") or {}
    if request_body.get("user_id") != "13800138000":
        problems.append(f"请求体 user_id 未按用例变量注入: {request_body.get('user_id')!r}")
    if request_body.get("channel") != "smoke":
        problems.append(f"用例变量未覆盖环境变量池: {request_body.get('channel')!r}")
    if request_body.get("conversationId") != session_id:
        problems.append(
            f"会话值未贯穿第二轮: 期望 {session_id!r} 实际 {request_body.get('conversationId')!r}"
        )
    if request_headers.get("X-User-Id") != "13800138000":
        problems.append(f"请求头未注入用例变量: {request_headers.get('X-User-Id')!r}")

    payload = (second.response_payload or {}).get("body") or {}
    data = payload.get("data") if isinstance(payload, dict) else None
    echo = (data or {}).get("received_params") or {} if isinstance(data, dict) else {}
    if echo.get("user_id") != "13800138000" or echo.get("channel") != "smoke":
        problems.append(f"目标接口未收到用例变量回显: {echo!r}")

    if not problems:
        print(f"[OK] 请求变量与会话绑定冒烟通过 session_id={session_id}")
    return problems


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

    failures.extend(await _check_session_binding())

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
