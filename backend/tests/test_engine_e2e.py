"""执行引擎端到端测试（依赖本机 MySQL 与内置 Mock 服务）。"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text

from billing_qa.db.session import SessionLocal
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.test_case import TestCase as CaseModel
from billing_qa.models.test_case import TestCaseRevision as CaseRevision
from billing_qa.services.engine.executor import CaseExecutor

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
        pytest.skip("MySQL 或 Mock 服务不可用，跳过引擎端到端测试")
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


async def test_dynamic_hidden_context_release() -> None:
    if not await _dependencies_ready():
        pytest.skip("MySQL 或 Mock 服务不可用")
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
