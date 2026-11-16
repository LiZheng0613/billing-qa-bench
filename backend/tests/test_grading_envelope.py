"""答案包络测试：Must 闸门、May 非闸门、MustNot 安全、扩展策略。"""

from __future__ import annotations

from billing_qa.schemas.case import AnswerEnvelope, FactAtom
from billing_qa.services.grading.claims import rule_claims
from billing_qa.services.grading.envelope import grade_envelope


def _envelope(**kwargs) -> AnswerEnvelope:
    data = {
        "must": [FactAtom(id="amount", kind="scalar", value=100.0, unit="CNY", label="账单金额")],
        "tolerances": {"money": 0.01, "percent": 0.1},
    }
    data.update(kwargs)
    return AnswerEnvelope(**data)


def test_must_fact_gate() -> None:
    envelope = _envelope()
    answer = "本期账单金额为 100.00 元。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert result.gates["facts"]
    assert result.scorecard["fact_completeness"] == 100.0


def test_missing_must_fact_fails_gate() -> None:
    envelope = _envelope()
    answer = "本期账单金额为 88.00 元。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert not result.gates["facts"]


def test_may_fact_is_not_a_gate() -> None:
    envelope = _envelope(
        may=[FactAtom(id="due", kind="date", value="2026-10-31", label="到期日")]
    )
    answer = "本期账单金额为 100.00 元。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert result.gates["facts"]
    assert result.scorecard["helpful_extra"] == 0.0


def test_must_not_is_independent_safety_gate() -> None:
    envelope = _envelope(
        must_not=[FactAtom(id="banned", kind="text", value="系统错误")]
    )
    answer = "金额 100 元。出现系统错误，请稍后重试。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert result.gates["facts"]
    assert not result.gates["safety"]


def test_forbidden_phrase_safety_gate() -> None:
    envelope = _envelope(forbidden_phrases=["保证全额退款"])
    answer = "金额 100 元，我们保证全额退款。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert not result.gates["safety"]


def test_strict_extras_reject_unsupported_numbers() -> None:
    envelope = _envelope(extras_policy="strict")
    answer = "本期账单金额 100 元，另外赠送 50 元优惠券。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert result.gates["facts"]
    assert not result.gates["extras"]
    assert any(item.kind == "unsupported" for item in result.extras)


def test_lenient_extras_allow_grounded_extension() -> None:
    envelope = _envelope()
    answer = "本期账单金额 100 元，账期 2026-10。"
    result = grade_envelope(envelope, answer, rule_claims(answer))
    assert result.gates["extras"]


def test_all_degraded_facts_do_not_kill() -> None:
    envelope = AnswerEnvelope(
        must=[FactAtom(id="tone", kind="text", value="服务态度")]
    )
    answer = "我们非常重视您的体验。"
    result = grade_envelope(envelope, answer, [])
    assert result.gates["facts"]
    assert result.degraded
