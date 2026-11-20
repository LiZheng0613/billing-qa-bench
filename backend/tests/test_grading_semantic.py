"""清晰度扣分与语义增强降级（LLM 不可用时不误杀）。"""

from __future__ import annotations

from typing import Any

import pytest

from billing_qa.schemas.case import AnswerEnvelope, FactAtom
from billing_qa.services.grading.answer_grader import _clarity_score, grade_answer
from billing_qa.services.grading.semantic import enhance_degraded_facts, judge_relevance
from billing_qa.services.grading.types import FactOutcome
from billing_qa.services.llm.client import ModelContext

SNAPSHOT: dict[str, Any] = {
    "grading_mode": "ANSWER_CONTRACT",
    "input_type": "CLEAR",
    "as_of": "2026-10",
    "answer_envelope": {
        "must": [{"id": "amount", "kind": "scalar", "value": 123.45, "unit": "CNY"}],
        "must_not": [],
        "extras_policy": "lenient",
    },
}


def _envelope() -> AnswerEnvelope:
    return AnswerEnvelope.model_validate(SNAPSHOT["answer_envelope"])


def _unreachable_model() -> ModelContext:
    return ModelContext(
        id=None,
        code="down",
        name="不可达模型",
        role="JUDGE",
        base_url="http://127.0.0.1:9/v1",
        api_key=None,
        model_name="mock",
        temperature=0.0,
        top_p=None,
        max_tokens=64,
        timeout_seconds=0.5,
        rpm_limit=0,
        max_retries=0,
        response_format_json=True,
    )


def test_off_topic_deducts_clarity_only() -> None:
    answer = "本期账单金额 123.45 元。"
    baseline = _clarity_score(answer, _envelope())
    off_topic = _clarity_score(answer, _envelope(), off_topic=True)
    assert off_topic < baseline
    assert off_topic == round(baseline - 35.0, 2)


def test_verbosity_relative_to_question_deducts_clarity() -> None:
    """答案长度远超问题长度（max_ratio 的 3 倍）时扣清晰度。"""
    envelope = _envelope()
    envelope.clarity_rules = {"conclusion_first": False, "max_ratio": 3.0}
    concise = "本期账单金额 123.45 元。"
    verbose = (
        "本期账单金额 123.45 元。除此之外，我还想提醒您关注以下几点内容："
        "第一，账单周期与出账日的对应关系；第二，各类优惠的叠加规则；"
        "第三，历史账单的查询方式；第四，纸质账单的邮寄流程，请您知悉。"
    )
    # 不带问题上下文时无从判断冗长，保持满分口径
    assert _clarity_score(verbose, envelope) == 100.0
    # 带短问题时，冗长答案相对问题（4 字）超过 3.0*3 倍，扣 10 分
    assert _clarity_score(verbose, envelope, question="账单多少") == 90.0
    # 简短答案不受冗长检查影响
    assert _clarity_score(concise, envelope, question="本期账单金额是多少") == 100.0


async def test_off_topic_answer_does_not_change_fact_gate() -> None:
    """跑题只影响清晰度；金额正确时事实闸门仍然通过。"""
    grading = await grade_answer(
        snapshot=SNAPSHOT,
        turn_records=[{"query": "本期账单金额？", "final_answer": "本期账单金额 123.45 元。"}],
    )
    assert grading.gates["facts"] is True


async def test_semantic_enhancement_skips_without_model() -> None:
    facts = [
        FactOutcome(
            fact_id="note",
            kind="text",
            label="备注",
            expected=True,
            actual=None,
            passed=False,
            degraded=True,
        )
    ]
    expected = [FactAtom(id="note", kind="text", label="备注", keywords=["已受理"])]
    notes = await enhance_degraded_facts(
        facts=facts, expected=expected, answer="业务已受理", model=None
    )
    assert notes == []
    assert facts[0].degraded is True
    assert facts[0].passed is False


async def test_semantic_enhancement_degrades_on_unreachable_model() -> None:
    facts = [
        FactOutcome(
            fact_id="note",
            kind="text",
            label="备注",
            expected=True,
            actual=None,
            passed=False,
            degraded=True,
        )
    ]
    expected = [FactAtom(id="note", kind="text", label="备注", keywords=["已受理"])]
    notes = await enhance_degraded_facts(
        facts=facts, expected=expected, answer="业务已受理", model=_unreachable_model()
    )
    assert notes == []
    # LLM 不可用时保持降级状态，不误杀
    assert facts[0].degraded is True


async def test_relevance_degrades_to_none() -> None:
    assert (
        await judge_relevance(
            question="账单金额？", answer="123.45 元", facts=[], model=None
        )
        is None
    )
    assert (
        await judge_relevance(
            question="账单金额？",
            answer="123.45 元",
            facts=[],
            model=_unreachable_model(),
        )
        is None
    )


@pytest.mark.parametrize("answer", ["", "   "])
def test_empty_answer_clarity_is_zero(answer: str) -> None:
    assert _clarity_score(answer, _envelope()) == 0.0
