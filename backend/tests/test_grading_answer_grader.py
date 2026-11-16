"""答案契约判分端到端测试（不依赖真实 LLM）。"""

from __future__ import annotations

import pytest

from billing_qa.services.grading.answer_grader import grade_answer
from billing_qa.services.grading.diagnostics import build_diagnostics
from billing_qa.services.grading.prompts import (
    BEHAVIOR_USER,
    CLAIM_EXTRACT_USER,
    CLARITY_USER,
)


def _snapshot(**overrides) -> dict:
    data = {
        "grading_mode": "ANSWER_CONTRACT",
        "input_type": "CLEAR",
        "as_of": "2026-10",
        "answer_envelope": {
            "must": [
                {"id": "amount", "kind": "scalar", "value": 123.45, "unit": "CNY"},
                {"id": "due", "kind": "date", "value": "2026-10-31"},
            ],
            "must_not": [{"id": "banned", "kind": "text", "value": "系统错误"}],
            "extras_policy": "lenient",
        },
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_clear_case_passes_on_final_answer() -> None:
    records = [
        {
            "query": "本期账单金额和到期日？",
            "final_answer": "本期账单金额为 123.45 元，到期日 2026-10-31。",
        }
    ]
    grading = await grade_answer(snapshot=_snapshot(), turn_records=records)
    assert grading.passed
    assert grading.gates == {"safety": True, "behavior": True, "facts": True, "extras": True}
    assert grading.total_score > 80


@pytest.mark.asyncio
async def test_wrong_amount_fails_correctness() -> None:
    records = [{"query": "本期账单金额？", "final_answer": "本期账单金额为 999.00 元。"}]
    grading = await grade_answer(snapshot=_snapshot(), turn_records=records)
    assert not grading.passed
    assert not grading.gates["facts"]
    assert grading.root_cause == "HALLUCINATION"


@pytest.mark.asyncio
async def test_unsupported_extra_under_lenient_policy_stays_passed() -> None:
    records = [
        {
            "query": "本期账单金额？",
            "final_answer": "本期账单金额为 123.45 元，到期日 2026-10-31，另赠送 50 元券。",
        }
    ]
    grading = await grade_answer(snapshot=_snapshot(), turn_records=records)
    assert grading.passed
    assert any(item.kind == "unsupported" for item in grading.extras)


@pytest.mark.asyncio
async def test_unsupported_extra_under_strict_policy_fails() -> None:
    snapshot = _snapshot()
    snapshot["answer_envelope"]["extras_policy"] = "strict"
    records = [
        {
            "query": "本期账单金额？",
            "final_answer": "本期账单金额为 123.45 元，另赠送 50 元券。",
        }
    ]
    grading = await grade_answer(snapshot=snapshot, turn_records=records)
    assert not grading.passed
    assert not grading.gates["extras"]


@pytest.mark.asyncio
async def test_ambiguous_input_requires_stated_assumption() -> None:
    snapshot = _snapshot(
        input_type="UNDERSPECIFIED",
        response_contract={
            "allowed": ["clarify", "answer_with_assumption"],
            "clarify": {"must_ask_slot": ["time_range"], "must_not_assert": ["amount"]},
            "answer_with_assumption": {
                "must_state_assumption": True,
                "scenarios": [{"assumption": "按当前账期", "must": {"amount": "123.45元"}}],
            },
        },
    )
    silent = [{"query": "账单多少？", "final_answer": "本期账单金额为 123.45 元。"}]
    grading = await grade_answer(snapshot=snapshot, turn_records=silent)
    assert not grading.passed
    assert not grading.gates["behavior"]

    stated = [
        {
            "query": "账单多少？",
            "final_answer": "按当前账期口径，本期账单金额为 123.45 元。",
        }
    ]
    grading = await grade_answer(snapshot=snapshot, turn_records=stated)
    assert grading.passed
    assert grading.behavior.detected == "assumption"


@pytest.mark.asyncio
async def test_missing_envelope_is_reported_not_crashed() -> None:
    grading = await grade_answer(
        snapshot={"grading_mode": "ANSWER_CONTRACT"}, turn_records=[]
    )
    assert not grading.passed
    assert grading.degraded
    assert "缺少答案包络" in " ".join(grading.notes)


@pytest.mark.asyncio
async def test_lucky_pass_is_diagnostic_only() -> None:
    records = [
        {
            "query": "本期账单金额？",
            "final_answer": "本期账单金额为 123.45 元，到期日 2026-10-31。",
            "tool_calls": [],
        }
    ]
    grading = await grade_answer(snapshot=_snapshot(), turn_records=records)
    assert grading.passed
    assert grading.lucky_pass
    assert grading.diagnostics["tool_call_count"] == 0


def test_prompts_render_without_brace_errors() -> None:
    """内置 Prompt 必须能被 str.format 安全渲染（JSON 示例需转义花括号）。"""
    assert "抽取" in CLAIM_EXTRACT_USER.format(answer="金额 100 元")
    assert "表达质量" in CLARITY_USER.format(question="多少钱？", answer="100 元")
    assert "行为" in BEHAVIOR_USER.format(question="多少钱？", answer="100 元")


def test_diagnostics_flags_missing_capabilities() -> None:
    snapshot = {
        "plan_spec": {
            "required_capabilities": ["bill_current", "order_detail"],
            "max_tool_calls": 3,
        },
        "as_of": "2026-10",
    }
    records = [
        {
            "tool_calls": [
                {"kind": "CYPHER", "capability": "bill_current", "rows": 1, "status": "SUCCESS"},
                {"kind": "CYPHER", "capability": "bill_current", "rows": 1, "status": "SUCCESS"},
            ]
        }
    ]
    diagnostics = build_diagnostics(snapshot, records, answer="金额 100 元", numeric_claim_count=1)
    assert diagnostics["tool_call_count"] == 2
    assert diagnostics["missing_capabilities"] == ["order_detail"]
    assert diagnostics["repeated_queries"] == []
    assert diagnostics["evidence_rows"] == 2
