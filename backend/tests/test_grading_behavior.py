"""响应契约行为判定测试：澄清 / 假设 / 部分 / 拒绝 / 静默假设。"""

from __future__ import annotations

from billing_qa.schemas.case import (
    AnswerEnvelope,
    AssumptionScenario,
    AssumptionSpec,
    ClarifySpec,
    FactAtom,
    PartialSpec,
    RefuseSpec,
    ResponseContract,
)
from billing_qa.services.grading.behavior import detect_behavior, resolve_required_facts
from billing_qa.services.grading.claims import rule_claims


def _contract(
    allowed: tuple[str, ...] = ("clarify", "answer_with_assumption"),
) -> ResponseContract:
    return ResponseContract(
        allowed=list(allowed),
        clarify=ClarifySpec(must_ask_slot=["time_range"], must_not_assert=["amount"]),
        answer_with_assumption=AssumptionSpec(
            must_state_assumption=True,
            scenarios=[
                AssumptionScenario(assumption="按当前账期", must={"amount": "100元"})
            ],
        ),
        partial=PartialSpec(must_disclose=True, required_facts=["amount"]),
        refuse=RefuseSpec(must_reason=True),
    )


def test_clarify_covering_required_slot_is_allowed() -> None:
    result = detect_behavior(_contract(), "请问您想查询哪个账期的账单？", claims=[])
    assert result.detected == "clarify"
    assert result.allowed


def test_clarify_missing_slot_is_rejected() -> None:
    result = detect_behavior(_contract(), "请问您想查询什么？", claims=[])
    assert result.detected == "clarify"
    assert not result.allowed
    assert result.missing_slots


def test_clarify_asserting_amount_is_rejected() -> None:
    result = detect_behavior(
        _contract(), "请问您要查哪个账期？本期金额 100 元。", claims=rule_claims("100 元")
    )
    assert result.detected == "clarify"
    assert not result.allowed


def test_silent_assumption_is_rejected() -> None:
    result = detect_behavior(_contract(), "本期账单金额是 100 元。", claims=rule_claims("100 元"))
    assert result.detected == "answer"
    assert not result.allowed
    assert any("静默假设" in item for item in result.violations)


def test_stated_assumption_matches_scenario() -> None:
    answer = "按当前账期口径，本期账单金额是 100 元。"
    result = detect_behavior(_contract(), answer, claims=rule_claims(answer))
    assert result.detected == "assumption"
    assert result.allowed
    assert result.matched_scenario == "按当前账期"


def test_refuse_with_reason_is_allowed() -> None:
    contract = _contract(("clarify", "refuse"))
    result = detect_behavior(contract, "抱歉，当前没有权限查询该客户账单。", claims=[])
    assert result.detected == "refuse"
    assert result.allowed


def test_partial_disclosure_is_allowed() -> None:
    contract = _contract(("clarify", "partial"))
    result = detect_behavior(contract, "仅查到本期账单金额 100 元，明细暂时无法获取。", claims=[])
    assert result.detected == "partial"
    assert result.allowed


def test_resolve_required_facts_by_behavior() -> None:
    envelope = AnswerEnvelope(
        must=[FactAtom(id="amount", kind="scalar", value=100.0, unit="CNY")]
    )
    contract = _contract()
    clarify = detect_behavior(contract, "请问您要查哪个账期？", claims=[])
    assert resolve_required_facts(contract, envelope, clarify) == []

    answer = "按当前账期口径，本期账单金额是 100 元。"
    assumption = detect_behavior(contract, answer, claims=rule_claims(answer))
    required = resolve_required_facts(contract, envelope, assumption)
    assert required and all(fact.id.startswith("amount") for fact in required)
