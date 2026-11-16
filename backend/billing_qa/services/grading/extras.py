"""答案扩展内容分类：有据扩展中性，无据扩展视为幻觉。

规则：
- 命中 Must / May 事实的句子 → ``grounded`` / ``may``，中性。
- 出现数值或日期，但无法对应任何期望事实 → ``unsupported``，严格模式判失败。
- 整段答案与期望事实零重叠 → ``off_topic``，只影响清晰度，不影响正确性。
"""

from __future__ import annotations

import re
from decimal import Decimal

from billing_qa.schemas.case import AnswerEnvelope, FactAtom
from billing_qa.services.engine.rule_evaluator import extract_claim_atoms
from billing_qa.services.grading.normalize import (
    close_enough,
    contains_any,
    money_tolerance,
    normalize_text,
    variants,
)
from billing_qa.services.grading.types import Claim, ExtraItem

_SENTENCE_SPLIT = re.compile(r"[。！？!?\n；;]+")


def classify_extras(
    answer: str,
    envelope: AnswerEnvelope,
    required_facts: list[FactAtom],
    claims: list[Claim],
    *,
    extra_facts: list[FactAtom] | None = None,
) -> list[ExtraItem]:
    """把答案里的扩展内容分成有据 / 无据 / 跑题三类。"""
    text = answer or ""
    expected = [*required_facts, *(extra_facts or [])]
    may_facts = list(envelope.may)
    all_facts = [*expected, *may_facts]

    items: list[ExtraItem] = []
    anchor_terms = _anchor_terms(all_facts)
    anchor_numbers = _anchor_numbers(all_facts, envelope.tolerances)

    for sentence in _sentences(text):
        normalized = normalize_text(sentence)
        if not normalized:
            continue
        atoms = extract_claim_atoms(sentence)
        numbers = [atom for atom in atoms if atom.get("kind") == "number"]
        dates = [atom for atom in atoms if atom.get("kind") == "date"]

        if numbers or dates:
            ungrounded = _ungrounded_numbers(numbers, anchor_numbers, envelope.tolerances)
            ungrounded += _ungrounded_dates(dates, all_facts)
            if ungrounded:
                items.append(
                    ExtraItem(
                        text=sentence.strip()[:200],
                        kind="unsupported",
                        evidence_ref=", ".join(ungrounded[:3]),
                    )
                )
                continue
            items.append(ExtraItem(text=sentence.strip()[:200], kind="grounded"))
            continue

        if may_facts and _matches_any_fact(sentence, may_facts, envelope):
            items.append(ExtraItem(text=sentence.strip()[:200], kind="may"))

    if not items and anchor_terms and not _mentions_any(text, anchor_terms):
        items.append(
            ExtraItem(text=text.strip()[:200], kind="off_topic", evidence_ref="无期望事实锚点")
        )
    return items


def unsupported_extras(items: list[ExtraItem]) -> list[ExtraItem]:
    return [item for item in items if item.kind == "unsupported"]


def off_topic_extras(items: list[ExtraItem]) -> list[ExtraItem]:
    return [item for item in items if item.kind == "off_topic"]


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]


def _anchor_terms(facts: list[FactAtom]) -> list[str]:
    terms: list[str] = []
    for fact in facts:
        if fact.label:
            terms.append(fact.label)
        terms.extend(fact.keywords or [])
        for value in fact.values or []:
            terms.append(str(value))
        if fact.kind == "text" and fact.value not in (None, ""):
            terms.append(str(fact.value))
    return [term for term in terms if term]


def _anchor_numbers(
    facts: list[FactAtom], tolerances: dict[str, float] | None
) -> list[tuple[Decimal, float]]:
    numbers: list[tuple[Decimal, float]] = []
    for fact in facts:
        if fact.kind != "scalar" or fact.value in (None, ""):
            continue
        try:
            value = Decimal(str(fact.value))
        except (ArithmeticError, ValueError):
            continue
        tolerance = money_tolerance(fact.unit, fact.tolerance, tolerances)
        numbers.append((value, tolerance))
    return numbers


def _ungrounded_numbers(
    numbers: list[dict], anchors: list[tuple[Decimal, float]], tolerances: dict[str, float] | None
) -> list[str]:
    ungrounded: list[str] = []
    for atom in numbers:
        raw = atom.get("value")
        if not any(close_enough(raw, float(value), tolerance) for value, tolerance in anchors):
            ungrounded.append(str(atom.get("text") or raw))
    return ungrounded


def _ungrounded_dates(dates: list[dict], facts: list[FactAtom]) -> list[str]:
    expected = {str(fact.value) for fact in facts if fact.kind == "date"}
    if not expected:
        return []
    return [str(atom.get("text")) for atom in dates if str(atom.get("value")) not in expected]


def _matches_any_fact(sentence: str, facts: list[FactAtom], envelope: AnswerEnvelope) -> bool:
    for fact in facts:
        for value in fact.values or []:
            if contains_any(sentence, variants(str(value), envelope.aliases)):
                return True
        if fact.value not in (None, "") and contains_any(
            sentence, variants(str(fact.value), envelope.aliases)
        ):
            return True
        if fact.label and normalize_text(fact.label) in normalize_text(sentence):
            return True
    return False


def _mentions_any(text: str, terms: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(term) in normalized for term in terms if term)
