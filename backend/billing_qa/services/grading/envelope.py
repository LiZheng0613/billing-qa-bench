"""答案包络（Must / May / MustNot）判定与闸门计算。

正确性只看最终答案；轨迹不参与本模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from billing_qa.schemas.case import AnswerEnvelope, FactAtom
from billing_qa.services.grading.extras import classify_extras, unsupported_extras
from billing_qa.services.grading.matchers import fact_names, match_banned, match_fact
from billing_qa.services.grading.normalize import find_phrase_violation
from billing_qa.services.grading.types import Claim, ExtraItem, FactOutcome

_SCORE_MAX = 100.0


@dataclass
class EnvelopeResult:
    gates: dict[str, bool]
    scorecard: dict[str, float]
    facts: list[FactOutcome]
    extras: list[ExtraItem]
    banned_hits: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    degraded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def safety_passed(self) -> bool:
        return not self.banned_hits and not self.forbidden_hits


def grade_envelope(
    envelope: AnswerEnvelope,
    answer: str,
    claims: list[Claim],
    *,
    required_facts: list[FactAtom] | None = None,
) -> EnvelopeResult:
    """按 Must / May / MustNot 判定答案，输出闸门与评分卡输入。"""
    required = list(required_facts) if required_facts is not None else list(envelope.must)
    notes: list[str] = []

    # MustNot / 禁用短语属于安全硬门槛，与输入形态无关，永远检查。
    banned_hits: list[str] = []
    for fact in envelope.must_not:
        hit = match_banned(fact, answer)
        if hit is not None:
            banned_hits.append(hit)
    forbidden_hits: list[str] = []
    forbidden = find_phrase_violation(answer, list(envelope.forbidden_phrases))
    if forbidden:
        forbidden_hits.append(forbidden)

    facts: list[FactOutcome] = []
    seen: set[str] = set()
    # 全量名称表：用于识别「数值归属了另一个业务名称」的张冠李戴。
    aliases = envelope.aliases or {}
    vocabulary = {
        str(fact.id): fact_names(fact, aliases) for fact in [*envelope.must, *envelope.may]
    }
    for fact in required:
        if fact.id in seen:
            continue
        seen.add(fact.id)
        facts.append(
            match_fact(
                fact,
                answer,
                claims,
                envelope.tolerances,
                is_gate=True,
                aliases=aliases,
                vocabulary=vocabulary,
            )
        )
    for fact in envelope.may:
        if fact.id in seen:
            continue
        seen.add(fact.id)
        facts.append(
            match_fact(
                fact,
                answer,
                claims,
                envelope.tolerances,
                is_gate=False,
                aliases=aliases,
                vocabulary=vocabulary,
            )
        )

    gate_facts = [item for item in facts if item.is_gate]
    degraded = any(item.degraded for item in gate_facts)
    effective = [item for item in gate_facts if not item.degraded]
    fact_gate = all(item.passed for item in effective)
    if not effective:
        fact_gate = True
        if degraded:
            notes.append("全部 Must 事实均无法机械判定，已降级放行（不误杀）")

    extras = classify_extras(
        answer,
        envelope,
        required,
        claims,
        extra_facts=list(envelope.may),
    ) if (required or envelope.may) else []
    unsupported = unsupported_extras(extras)
    extras_gate = envelope.extras_policy != "strict" or not unsupported
    if unsupported and envelope.extras_policy == "strict":
        notes.append("严格模式：答案包含无据扩展，判为不通过")

    completeness = _ratio([item for item in effective if item.passed], len(effective))
    may_items = [item for item in facts if not item.is_gate]
    may_effective = [item for item in may_items if not item.degraded]
    helpful_extra = _ratio([item for item in may_effective if item.passed], len(may_effective))

    scorecard = {
        "fact_completeness": completeness,
        "helpful_extra": helpful_extra,
    }
    gates = {
        "safety": not banned_hits and not forbidden_hits,
        "facts": fact_gate,
        "extras": extras_gate,
    }
    return EnvelopeResult(
        gates=gates,
        scorecard=scorecard,
        facts=facts,
        extras=extras,
        banned_hits=banned_hits,
        forbidden_hits=forbidden_hits,
        degraded=degraded,
        notes=notes,
    )


def _ratio(passed: list[Any], total: int) -> float:
    if total <= 0:
        return _SCORE_MAX
    return round(len(passed) / total * _SCORE_MAX, 2)
