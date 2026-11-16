"""语义等价与相关性增强（可选，LLM 不可用时保持确定性降级）。

定位：
- 仅用于确定性匹配器无法判定（``degraded``）的 text / boolean 事实；
- LLM 判定为等价时才「放行」，判定为不等价时保持降级，避免误杀；
- 跑题标记只影响清晰度，语义判定为切题时撤销标记。
"""

from __future__ import annotations

from typing import Any

from billing_qa.core.logging import get_logger
from billing_qa.schemas.case import FactAtom
from billing_qa.services.grading.prompts import (
    RELEVANCE_SYSTEM,
    RELEVANCE_USER,
    SEMANTIC_EQUIVALENCE_SYSTEM,
    SEMANTIC_EQUIVALENCE_USER,
)
from billing_qa.services.grading.types import FactOutcome
from billing_qa.services.llm.client import LLMClient, ModelContext
from billing_qa.services.llm.json_parser import parse_json_object

logger = get_logger(__name__)

DEFAULT_CONFIDENCE = 0.7


async def enhance_degraded_facts(
    *,
    facts: list[FactOutcome],
    expected: list[FactAtom],
    answer: str,
    model: ModelContext | None,
    confidence_threshold: float = DEFAULT_CONFIDENCE,
) -> list[str]:
    """只对 degraded 的 text/boolean 事实做保守语义判定；返回诊断备注。"""
    notes: list[str] = []
    if model is None or not (answer or "").strip():
        return notes
    expected_map = {str(fact.id): fact for fact in expected}
    for outcome in facts:
        if not outcome.degraded or outcome.kind not in {"text", "boolean"}:
            continue
        factor = expected_map.get(str(outcome.fact_id))
        if factor is None:
            continue
        verdict = await _equivalence(factor, answer, model, confidence_threshold)
        if verdict is None:
            continue
        if verdict:
            outcome.passed = True
            outcome.degraded = False
            outcome.reason = "语义等价判定通过"
            notes.append(f"事实 {outcome.fact_id} 经语义判定放行")
        else:
            notes.append(f"事实 {outcome.fact_id} 语义判定不等价，仍按降级处理（不误杀）")
    return notes


async def judge_relevance(
    *,
    question: str,
    answer: str,
    facts: list[FactAtom],
    model: ModelContext | None,
    confidence_threshold: float = DEFAULT_CONFIDENCE,
) -> bool | None:
    """返回 True/False；无法判断或置信度不足时返回 None。"""
    if model is None or not (answer or "").strip():
        return None
    fact_lines = "\n".join(_describe_fact(fact) for fact in facts) or "（无）"
    prompt = RELEVANCE_USER.format(
        question=question or "（未提供）", answer=answer, facts=fact_lines
    )
    data = await _chat_json(model, RELEVANCE_SYSTEM, prompt)
    if data is None:
        return None
    value = data.get("relevant")
    if not isinstance(value, bool):
        return None
    confidence = _confidence(data)
    if confidence is not None and confidence < confidence_threshold:
        return None
    return value


async def _equivalence(
    fact: FactAtom,
    answer: str,
    model: ModelContext,
    confidence_threshold: float,
) -> bool | None:
    prompt = SEMANTIC_EQUIVALENCE_USER.format(
        label=fact.label or fact.id,
        kind=str(fact.kind),
        expected=_expected_text(fact),
        answer=answer,
    )
    data = await _chat_json(model, SEMANTIC_EQUIVALENCE_SYSTEM, prompt)
    if data is None:
        return None
    value = data.get("equivalent")
    if not isinstance(value, bool):
        return None
    confidence = _confidence(data)
    if confidence is not None and confidence < confidence_threshold:
        return None
    return value


async def _chat_json(model: ModelContext, system: str, user: str) -> dict[str, Any] | None:
    try:
        async with LLMClient(model) as client:
            result = await client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                json_mode=True,
                temperature=0.0,
            )
        data = parse_json_object(result.content)
    except Exception as exc:
        logger.warning("语义判定降级: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _expected_text(fact: FactAtom) -> str:
    parts: list[str] = []
    if fact.keywords:
        parts.append("关键词：" + "、".join(fact.keywords))
    if fact.values:
        parts.append("候选值：" + "、".join(fact.values))
    if fact.value not in (None, ""):
        parts.append(f"期望值：{fact.value}")
    return "；".join(parts) or "（未提供）"


def _describe_fact(fact: FactAtom) -> str:
    return f"- {fact.label or fact.id}（{fact.kind}）：{_expected_text(fact)}"


def _confidence(data: dict[str, Any]) -> float | None:
    raw = data.get("confidence")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
