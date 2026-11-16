"""答案契约判分的执行器端到端测试（真实 Mock 目标系统 + 落库断言）。"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import delete, select, text

from billing_qa.db.session import SessionLocal
from billing_qa.models.execution import ExecutionRecord, TurnTrace
from billing_qa.models.gold_query import FactResult
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


async def _load_demo_snapshot(code: str) -> tuple[int, int, dict] | None:
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
        return int(case.id), int(case.current_version), dict(snapshot)


def _contract_snapshot(base: dict, *, query: str) -> dict:
    """复用演示用例的环境/模型/模板，只叠加答案契约字段。"""
    snapshot = dict(base)
    snapshot.update(
        {
            "query": query,
            "grading_mode": "ANSWER_CONTRACT",
            "input_type": "CLEAR",
            "as_of": "2026-10",
            "answer_envelope": {
                "must": [
                    {
                        "id": "amount",
                        "kind": "scalar",
                        "value": 12345.67,
                        "unit": "CNY",
                        "label": "含税金额",
                    },
                    {
                        "id": "item_count",
                        "kind": "scalar",
                        "value": 3,
                        "label": "明细笔数",
                    },
                    {
                        "id": "order_no",
                        "kind": "text",
                        "value": True,
                        "keywords": ["A2024001"],
                        "label": "订单号",
                    },
                ],
                "must_not": [{"id": "banned", "kind": "text", "value": "系统错误"}],
                "extras_policy": "lenient",
            },
            "response_contract": None,
            "plan_spec": None,
        }
    )
    return snapshot


async def _cleanup_execution(execution_id: int) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(FactResult).where(FactResult.execution_id == execution_id))
        await db.execute(delete(ExecutionRecord).where(ExecutionRecord.id == execution_id))
        await db.commit()


async def test_contract_mode_passes_on_final_answer_and_persists_facts() -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_snapshot("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例 DEMO-SINGLE-001 未初始化")
    case_id, version, base = loaded
    snapshot = _contract_snapshot(base, query="请帮我查一下订单 A2024001 的含税金额和明细笔数")

    execution_id = await CaseExecutor().execute(
        case_id=case_id, case_version=version, snapshot=snapshot, source="DEBUG"
    )
    try:
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            assert record is not None
            assert record.status == "PASS", record.error_message
            assert record.pass_flag is True
            assert record.grading_mode == "ANSWER_CONTRACT"
            assert record.grader_version == "answer-grader@0.2"
            assert record.as_of == "2026-10"

            grading = record.answer_grading or {}
            assert grading.get("passed") is True
            assert grading.get("gates") == {
                "safety": True,
                "behavior": True,
                "facts": True,
                "extras": True,
            }
            assert grading.get("scorecard", {}).get("fact_completeness") == 100.0

            assert record.fact_results, "事实级结果必须冗余落库"
            fact_ids = {item["fact_id"] for item in record.fact_results}
            assert {"amount", "item_count", "order_no"} <= fact_ids
            assert all(item["passed"] for item in record.fact_results)

            rows = (
                await db.execute(
                    select(FactResult).where(FactResult.execution_id == execution_id)
                )
            ).scalars().all()
            assert {row.fact_id for row in rows} == fact_ids
            assert all(row.passed for row in rows)

            # 轨迹诊断：Mock 目标不回传 tool_calls，答案正确但无取数证据 → 幸运通过
            assert record.diagnostics is not None
            assert record.diagnostics["tool_call_count"] == 0
            assert record.lucky_pass is True
            assert record.root_cause is None

            assert record.judge_result is not None
            assert record.judge_result["mode"] == "ANSWER_CONTRACT"
            assert "quality" in record.judge_result

            turns = (
                await db.execute(
                    select(TurnTrace).where(TurnTrace.execution_id == execution_id)
                )
            ).scalars().all()
            assert turns, "应至少落库一轮对话轨迹"
    finally:
        await _cleanup_execution(execution_id)


async def test_seeded_contract_case_is_verified_and_passes() -> None:
    """演示种子用例必须已取证、已评审，并能端到端通过答案契约判分。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_snapshot("DEMO-CONTRACT-001")
    if loaded is None:
        pytest.skip("演示用例 DEMO-CONTRACT-001 未初始化")
    case_id, version, snapshot = loaded
    assert snapshot["grading_mode"] == "ANSWER_CONTRACT"
    assert snapshot["review_status"] == "APPROVED"
    assert snapshot.get("verified_facts"), "已取证事实应写入执行快照账本"
    must = snapshot["answer_envelope"]["must"]
    assert all(item.get("verification_status") == "VERIFIED" for item in must)

    execution_id = await CaseExecutor().execute(
        case_id=case_id, case_version=version, snapshot=snapshot, source="DEBUG"
    )
    try:
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            assert record is not None
            assert record.status == "PASS", record.error_message
            assert record.pass_flag is True
            assert record.grading_mode == "ANSWER_CONTRACT"
            assert record.as_of == "2026-10"
            assert record.fact_results
            assert all(item["passed"] for item in record.fact_results)
    finally:
        await _cleanup_execution(execution_id)


async def test_contract_mode_fails_when_amount_is_wrong() -> None:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_snapshot("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例 DEMO-SINGLE-001 未初始化")
    case_id, version, base = loaded
    # Mock 目标系统命中「错误金额」时故意返回 999.00，用于验证事实闸门
    snapshot = _contract_snapshot(base, query="请帮我查一下订单 A2024001 的错误金额和明细笔数")

    execution_id = await CaseExecutor().execute(
        case_id=case_id, case_version=version, snapshot=snapshot, source="DEBUG"
    )
    try:
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            assert record is not None
            assert record.status == "FAIL", record.error_message
            assert record.pass_flag is False

            grading = record.answer_grading or {}
            assert grading.get("passed") is False
            assert grading.get("gates", {}).get("facts") is False

            failed = [
                item
                for item in (record.fact_results or [])
                if item["fact_id"] == "amount" and not item["passed"]
            ]
            assert failed, "金额事实应被判定为未通过"
            assert record.root_cause == "HALLUCINATION"
            assert record.lucky_pass is False
    finally:
        await _cleanup_execution(execution_id)
