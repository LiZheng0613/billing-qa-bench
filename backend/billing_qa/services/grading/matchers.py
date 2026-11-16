"""确定性事实匹配器：金额/集合/日期/布尔。

本模块**不依赖任何 LLM**，金额、实体、日期一律由代码判定。
"""

from __future__ import annotations

from typing import Any

from billing_qa.schemas.case import FactAtom
from billing_qa.services.grading.normalize import (
    contains_any,
    find_phrase_violation,
    money_tolerance,
    normalize_text,
    variants,
)
from billing_qa.services.grading.types import Claim, FactOutcome
from billing_qa.services.name_binding import (
    match_number,
    normalize_name,
    number_spans,
)


def fact_names(
    fact: FactAtom, envelope_aliases: dict[str, list[str]] | None = None
) -> list[str]:
    """汇总事实可接受的名称：label + 事实别名 + id + 包络级别名。"""
    names: list[str] = []
    if fact.label:
        names.append(str(fact.label))
    names.extend(str(item) for item in (getattr(fact, "aliases", None) or []))
    if fact.id:
        names.append(str(fact.id))
    for canonical, alternatives in (envelope_aliases or {}).items():
        group = [str(canonical), *[str(item) for item in alternatives or []]]
        if any(normalize_name(item) in {normalize_name(name) for name in names} for item in group):
            names.extend(group)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def match_fact(
    fact: FactAtom,
    answer: str,
    claims: list[Claim],
    tolerances: dict[str, float] | None = None,
    *,
    is_gate: bool = True,
    aliases: dict[str, list[str]] | None = None,
    vocabulary: dict[str, list[str]] | None = None,
) -> FactOutcome:
    """按事实类型选择匹配器，返回统一判定结果。"""
    kind = str(fact.kind)
    if kind == "scalar":
        return _match_scalar(fact, answer, claims, tolerances, is_gate, aliases, vocabulary)
    if kind in {"set", "topk"}:
        return _match_set(fact, answer, is_gate)
    if kind == "date":
        return _match_date(fact, answer, claims, is_gate)
    if kind in {"boolean", "text"}:
        return _match_keyword(fact, answer, is_gate)
    return FactOutcome(
        fact_id=fact.id,
        kind=kind,
        label=fact.label,
        expected=fact.value,
        actual=None,
        passed=False,
        reason=f"不支持的事实类型: {kind}",
        is_gate=is_gate,
        degraded=True,
    )


def _match_scalar(
    fact: FactAtom,
    answer: str,
    claims: list[Claim],
    tolerances: dict[str, float] | None,
    is_gate: bool,
    aliases: dict[str, list[str]] | None = None,
    vocabulary: dict[str, list[str]] | None = None,
) -> FactOutcome:
    expected = fact.value
    tolerance = money_tolerance(fact.unit, fact.tolerance, tolerances)
    numbers = [
        claim.value
        for claim in claims
        if claim.kind == "number" and isinstance(claim.value, (int, float))
    ]
    names = fact_names(fact, aliases)
    result = match_number(
        answer,
        expected,
        tolerance,
        names,
        number_spans(answer, names, kind="auto"),
        group_key=str(fact.id),
        other_groups=vocabulary,
        claims=claims,
        require_name=bool(getattr(fact, "require_name", False)),
    )
    return FactOutcome(
        fact_id=fact.id,
        kind="scalar",
        label=fact.label,
        expected={
            "value": expected,
            "unit": fact.unit,
            "tolerance": tolerance,
            "time_scope": fact.time_scope,
            "names": names,
        },
        actual={
            "numbers": numbers,
            "bound_value": result.bound_value,
            "bound_name": result.bound_name,
            "mode": result.mode,
        },
        passed=result.passed,
        reason=result.reason,
        evidence_ref=result.evidence or None,
        is_gate=is_gate,
        mode=result.mode,
        bound_name=result.bound_name,
        conflict_name=result.conflict_name,
    )


def _match_set(fact: FactAtom, answer: str, is_gate: bool) -> FactOutcome:
    aliases = getattr(fact, "aliases", None) or {}
    matched: list[str] = []
    missing: list[str] = []
    for value in fact.values:
        if contains_any(answer, variants(value, aliases)):
            matched.append(value)
        else:
            missing.append(value)
    passed = not missing
    return FactOutcome(
        fact_id=fact.id,
        kind=str(fact.kind),
        label=fact.label,
        expected={"values": list(fact.values), "time_scope": fact.time_scope},
        actual={"matched": matched, "missing": missing},
        passed=passed,
        reason="" if passed else f"缺少: {', '.join(missing)}",
        is_gate=is_gate,
    )


def _match_date(
    fact: FactAtom,
    answer: str,
    claims: list[Claim],
    is_gate: bool,
) -> FactOutcome:
    expected = str(fact.value)
    dates = [str(claim.value) for claim in claims if claim.kind == "date"]
    hit = expected in dates or expected in normalize_text(answer)
    return FactOutcome(
        fact_id=fact.id,
        kind="date",
        label=fact.label,
        expected={"value": expected, "time_scope": fact.time_scope},
        actual={"dates": dates},
        passed=hit,
        reason="" if hit else f"未在答案中找到日期 {expected}",
        is_gate=is_gate,
    )


def _match_keyword(fact: FactAtom, answer: str, is_gate: bool) -> FactOutcome:
    keywords = [item for item in (fact.keywords or []) if item]
    if not keywords:
        return FactOutcome(
            fact_id=fact.id,
            kind=str(fact.kind),
            label=fact.label,
            expected=fact.value,
            actual=None,
            passed=False,
            reason="语义类事实缺少 keywords，且当前无可用语义判定",
            is_gate=is_gate,
            degraded=True,
        )
    normalized = normalize_text(answer)
    hits = [item for item in keywords if normalize_text(item) in normalized]
    expect_present = bool(fact.value) if fact.value is not None else True
    passed = bool(hits) == expect_present
    return FactOutcome(
        fact_id=fact.id,
        kind=str(fact.kind),
        label=fact.label,
        expected={"value": fact.value, "keywords": keywords},
        actual={"hits": hits},
        passed=passed,
        reason="" if passed else "语义线索未满足",
        is_gate=is_gate,
    )


def match_banned(fact: FactAtom, answer: str) -> str | None:
    """MustNot 事实：命中即返回被命中的期望值。"""
    if str(fact.kind) in {"set", "topk"}:
        for value in fact.values:
            if contains_any(answer, variants(value, getattr(fact, "aliases", None) or {})):
                return value
        return None
    if fact.value is None:
        return None
    text = answer or ""
    if str(fact.kind) in {"text", "boolean"}:
        return find_phrase_violation(text, [str(fact.value)])
    if contains_any(text, variants(str(fact.value), None)):
        return str(fact.value)
    return None


def outcome_from_dict(item: dict[str, Any]) -> FactOutcome:
    """从持久化结构还原（供离线重判/报告回放）。"""
    return FactOutcome(
        fact_id=str(item.get("fact_id") or ""),
        kind=str(item.get("kind") or "scalar"),
        label=item.get("label"),
        expected=item.get("expected"),
        actual=item.get("actual"),
        passed=bool(item.get("passed")),
        reason=str(item.get("reason") or ""),
        evidence_ref=item.get("evidence_ref"),
        is_gate=bool(item.get("is_gate", True)),
        degraded=bool(item.get("degraded")),
        mode=str(item.get("mode") or ""),
        bound_name=item.get("bound_name"),
        conflict_name=item.get("conflict_name"),
    )
