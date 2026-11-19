"""执行引擎端到端测试（依赖本机 PostgreSQL 与内置 Mock 服务）。"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest
from sqlalchemy import delete, select, text

from billing_qa.core.control_store import control_store
from billing_qa.db.session import SessionLocal
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase as CaseModel
from billing_qa.models.test_case import TestCaseRevision as CaseRevision
from billing_qa.services.engine.context import load_execution_context
from billing_qa.services.engine.executor import CaseExecutor
from billing_qa.services.engine.metrics import compute_task_metrics
from billing_qa.services.engine.scheduler import scheduler
from billing_qa.services.revision.snapshot import build_snapshot

pytestmark = pytest.mark.asyncio

MOCK_LLM = "http://127.0.0.1:8001/health"
MOCK_TARGET = "http://127.0.0.1:8002/health"


async def _dependencies_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        async with httpx.AsyncClient(timeout=2) as client:
            for url in (MOCK_LLM, MOCK_TARGET):
                if (await client.get(url)).status_code != 200:
                    return False
        return True
    except Exception:
        return False


async def _load_demo_case(code: str) -> tuple[int, int, dict] | None:
    async with SessionLocal() as db:
        case = (
            await db.execute(select(CaseModel).where(CaseModel.code == code))
        ).scalar_one_or_none()
        if case is None:
            return None
        revision = (
            await db.execute(
                select(CaseRevision).where(
                    CaseRevision.case_id == case.id,
                    CaseRevision.version == case.current_version,
                )
            )
        ).scalar_one_or_none()
        snapshot = revision.snapshot if revision is not None else None
        if snapshot is None:
            return None
        return case.id, case.current_version, snapshot


@pytest.mark.parametrize(
    ("case_code", "expected_status"),
    [
        ("DEMO-SINGLE-001", "PASS"),
        ("DEMO-SINGLE-FAIL", "FAIL"),
        ("DEMO-STATIC-001", "PASS"),
        ("DEMO-DYNAMIC-001", "PASS"),
        ("DEMO-DYNAMIC-MAXTURNS", "MAX_TURNS_EXCEEDED"),
    ],
)
async def test_demo_case_execution(case_code: str, expected_status: str) -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用，跳过引擎端到端测试")
    loaded = await _load_demo_case(case_code)
    if loaded is None:
        pytest.skip(f"演示用例 {case_code} 未初始化，请先执行 init_db --demo")
    case_id, version, snapshot = loaded

    executor = CaseExecutor()
    execution_id = await executor.execute(
        case_id=case_id,
        case_version=version,
        snapshot=snapshot,
        source="DEBUG",
    )

    async with SessionLocal() as db:
        record = await db.get(ExecutionRecord, execution_id)
        assert record is not None
        assert record.status == expected_status, record.error_message
        assert record.case_version == version
        assert record.case_snapshot["code"] == case_code
        assert record.finished_at is not None
        assert record.latency_ms is not None and record.latency_ms > 0

        turns = (
            await db.execute(
                select(TurnTrace)
                .where(TurnTrace.execution_id == execution_id)
                .order_by(TurnTrace.turn_index)
            )
        ).scalars().all()
        assert 1 <= len(turns) <= int(snapshot.get("max_turns") or 1) or len(turns) >= 1
        assert turns[0].query
        assert turns[0].request_payload is not None
        assert turns[0].response_raw is not None

        judges = (
            await db.execute(select(JudgeResult).where(JudgeResult.execution_id == execution_id))
        ).scalars().all()
        assert judges, "应生成至少一条裁判明细"
        assert judges[0].judge_prompt
        assert judges[0].total_score >= 0

        if expected_status == "PASS":
            assert record.pass_flag is True
            assert record.total_score and record.total_score >= 60
            assert record.judge_tokens > 0
        if expected_status == "MAX_TURNS_EXCEEDED":
            assert record.termination_status == "MAX_TURNS_EXCEEDED"
            assert record.pass_flag is False
            assert record.turns_used == int(snapshot.get("max_turns") or 0)


async def test_task_environment_overrides_case_snapshot() -> None:
    """任务显式指定的环境优先于用例快照中的环境。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例 DEMO-SINGLE-001 未初始化，请先执行 init_db --demo")
    _, _, snapshot = loaded
    case_env_id = snapshot.get("environment_id")
    if not case_env_id:
        pytest.skip("演示用例未绑定环境")

    override_id: int | None = None
    async with SessionLocal() as db:
        case_env = await db.get(Environment, case_env_id)
        assert case_env is not None
        override = Environment(
            code=f"PYTEST-ENV-OVERRIDE-{uuid.uuid4().hex[:8]}",
            name="pytest 任务指定环境",
            base_url=case_env.base_url,
            health_path=case_env.health_path,
            api_method=case_env.api_method,
            api_path=case_env.api_path,
            is_active=True,
        )
        db.add(override)
        await db.commit()
        await db.refresh(override)
        override_id = int(override.id)

    try:
        async with SessionLocal() as db:
            ctx = await load_execution_context(
                db, snapshot, default_environment_id=override_id
            )
        assert ctx.env.id == override_id
    finally:
        await _cleanup(env_id=override_id)


async def test_dynamic_hidden_context_release() -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-DYNAMIC-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded

    execution_id = await CaseExecutor().execute(
        case_id=case_id, case_version=version, snapshot=snapshot, source="DEBUG"
    )
    async with SessionLocal() as db:
        turns = (
            await db.execute(
                select(TurnTrace)
                .where(TurnTrace.execution_id == execution_id)
                .order_by(TurnTrace.turn_index)
            )
        ).scalars().all()
    sources = [turn.input_source for turn in turns]
    assert sources[0] == "INITIAL"
    assert any(source == "SIMULATOR" for source in sources)
    assert any(turn.simulator_thought for turn in turns)
    assert any(turn.rendered_prompt for turn in turns if turn.input_source == "SIMULATOR")


# ---------------------------------------------------------------------- #
# 环境类失败自动重试
# ---------------------------------------------------------------------- #
async def _create_bad_environment() -> int:
    """指向本机未监听端口，稳定制造“连接失败”类环境故障。"""
    async with SessionLocal() as db:
        env = Environment(
            code=f"PYTEST-BAD-ENV-{uuid.uuid4().hex[:8]}",
            name="pytest 不可达环境",
            base_url="http://127.0.0.1:9",
            health_path="/health",
            api_method="POST",
            api_path="/api/chat",
            is_stream=False,
            timeout_seconds=2,
            verify_ssl=False,
            is_active=True,
        )
        db.add(env)
        await db.commit()
        await db.refresh(env)
        return int(env.id)


async def _create_bad_case(environment_id: int) -> int:
    async with SessionLocal() as db:
        case = CaseModel(
            code=f"PYTEST-ENV-RETRY-{uuid.uuid4().hex[:8]}",
            name="pytest 环境重试用例",
            case_type="SINGLE",
            status="ACTIVE",
            query="订单 A2024001 的含税金额是多少？",
            environment_id=environment_id,
            current_version=1,
        )
        db.add(case)
        await db.flush()
        snapshot = build_snapshot(case)
        db.add(
            CaseRevision(
                case_id=case.id,
                version=1,
                change_type="CREATE",
                operator="pytest",
                summary="pytest 自动创建",
                snapshot=snapshot,
            )
        )
        await db.commit()
        return int(case.id)


async def _wait_task_finished(task_id: int, timeout: float = 60.0) -> BatchTask:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            if task is not None and task.status in {"COMPLETED", "ABORTED", "FAILED"}:
                return task
        await asyncio.sleep(0.2)
    raise TimeoutError(f"任务 {task_id} 未在 {timeout}s 内结束")


async def _cleanup(
    *, task_id: int | None = None, case_id: int | None = None, env_id: int | None = None
) -> None:
    async with SessionLocal() as db:
        if task_id is not None:
            await db.execute(
                delete(ExecutionRecord).where(ExecutionRecord.task_id == task_id)
            )
            await db.execute(delete(BatchTask).where(BatchTask.id == task_id))
        if case_id is not None:
            await db.execute(
                delete(CaseRevision).where(CaseRevision.case_id == case_id)
            )
            await db.execute(delete(CaseModel).where(CaseModel.id == case_id))
        if env_id is not None:
            await db.execute(delete(Environment).where(Environment.id == env_id))
        await db.commit()
    if task_id is not None:
        await control_store.clear(task_id)


async def test_executor_marks_env_error() -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    env_id = await _create_bad_environment()
    execution_id: int | None = None
    try:
        bad_snapshot = dict(snapshot)
        bad_snapshot["environment_id"] = env_id
        execution_id = await CaseExecutor().execute(
            case_id=case_id,
            case_version=version,
            snapshot=bad_snapshot,
            source="DEBUG",
        )
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            assert record is not None
            assert record.status == "ERROR"
            assert record.error_kind == "ENV"
            assert record.grading_mode == "LEGACY"
            assert record.attempt_no == 1
            assert "超时" in (record.error_message or "") or "HTTP" in (
                record.error_message or ""
            )
    finally:
        if execution_id is not None:
            async with SessionLocal() as db:
                await db.execute(
                    delete(ExecutionRecord).where(ExecutionRecord.id == execution_id)
                )
                await db.commit()
        await _cleanup(env_id=env_id)


async def test_scheduler_auto_retries_env_errors() -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    env_id = await _create_bad_environment()
    case_id = await _create_bad_case(env_id)
    task_id: int | None = None
    try:
        async with SessionLocal() as db:
            task = BatchTask(
                code=f"PYTEST-RETRY-{uuid.uuid4().hex[:8]}",
                name="pytest 环境重试任务",
                status="PENDING",
                control_state="RUNNING",
                environment_id=env_id,
                case_ids=[case_id],
                total_cases=1,
                concurrency=1,
                qps_limit=50,
                retry_on_env_error=True,
                max_env_retries=1,
                retry_backoff_seconds=0,
                created_by="pytest",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            task_id = int(task.id)

        assert await scheduler.start(task_id) is True
        finished = await _wait_task_finished(task_id)
        assert finished.status == "COMPLETED"

        async with SessionLocal() as db:
            records = (
                await db.execute(
                    select(ExecutionRecord)
                    .where(ExecutionRecord.task_id == task_id)
                    .order_by(ExecutionRecord.attempt_no)
                )
            ).scalars().all()
            assert [record.attempt_no for record in records] == [1, 2]
            assert [record.source for record in records] == ["BATCH", "RETRY"]
            assert all(record.status == "ERROR" for record in records)
            assert all(record.error_kind == "ENV" for record in records)
            metrics = await compute_task_metrics(db, task)
            assert metrics.env_retry_count == 1
            assert metrics.status_distribution.ERROR == 1
    finally:
        if task_id is not None:
            await _cleanup(task_id=task_id, case_id=case_id, env_id=env_id)
        else:
            await _cleanup(case_id=case_id, env_id=env_id)


async def test_scheduler_does_not_retry_business_failure() -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-FAIL")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id = loaded[0]
    task_id: int | None = None
    try:
        async with SessionLocal() as db:
            task = BatchTask(
                code=f"PYTEST-BIZ-{uuid.uuid4().hex[:8]}",
                name="pytest 业务失败不重试",
                status="PENDING",
                control_state="RUNNING",
                case_ids=[case_id],
                total_cases=1,
                concurrency=1,
                qps_limit=50,
                retry_on_env_error=True,
                max_env_retries=2,
                retry_backoff_seconds=0,
                created_by="pytest",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            task_id = int(task.id)

        assert await scheduler.start(task_id) is True
        finished = await _wait_task_finished(task_id)
        assert finished.status == "COMPLETED"

        async with SessionLocal() as db:
            records = (
                await db.execute(
                    select(ExecutionRecord).where(ExecutionRecord.task_id == task_id)
                )
            ).scalars().all()
            assert len(records) == 1, "业务失败不应触发环境重试"
            assert records[0].status == "FAIL"
            assert records[0].error_kind is None
            assert records[0].attempt_no == 1
    finally:
        if task_id is not None:
            await _cleanup(task_id=task_id)
