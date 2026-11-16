"""轨迹诊断：0 权重，只用于根因定位与优化，不参与 pass 判定。

诊断输入全部来自执行轨迹（工具调用、取数行数、计划覆盖），
即使轨迹为空也不影响正确性判定。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from billing_qa.models.enums import RootCause

_SUSPICIOUS_STATUS = {"FAILED", "ERROR", "TIMEOUT", "DENIED"}


def collect_tool_calls(turn_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """汇总所有轮次的工具调用（兼容 dict / list 两种落库形态）。"""
    calls: list[dict[str, Any]] = []
    for record in turn_records or []:
        raw = record.get("tool_calls")
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                calls.append(item)
    return calls


def build_diagnostics(
    snapshot: dict[str, Any],
    turn_records: list[dict[str, Any]],
    *,
    answer: str = "",
    numeric_claim_count: int = 0,
) -> dict[str, Any]:
    """产出可解释的诊断报告（不改变 pass）。"""
    calls = collect_tool_calls(turn_records)
    kinds = Counter(str(item.get("kind") or "UNKNOWN") for item in calls)
    statuses = Counter(str(item.get("status") or "SUCCESS").upper() for item in calls)
    capabilities = [
        str(item.get("capability"))
        for item in calls
        if item.get("capability")
    ]
    repeated = [
        query
        for query, count in Counter(
            str(item.get("query") or "").strip()
            for item in calls
            if str(item.get("query") or "").strip()
        ).items()
        if count > 1
    ]
    rows = 0
    for item in calls:
        try:
            rows += int(item.get("rows") or item.get("row_count") or 0)
        except (TypeError, ValueError):
            continue

    plan = snapshot.get("plan_spec") or {}
    required = [str(item) for item in (plan.get("required_capabilities") or [])]
    missing = [item for item in required if item not in capabilities]
    unexpected = [item for item in capabilities if required and item not in required]
    max_calls = plan.get("max_tool_calls")
    over_budget = bool(max_calls and len(calls) > int(max_calls))

    errors = [
        str(item.get("error") or item.get("message") or "")
        for item in calls
        if str(item.get("status") or "SUCCESS").upper() in _SUSPICIOUS_STATUS
    ]

    no_tool_call_but_claims = not calls and numeric_claim_count > 0
    evidence_gap = bool(numeric_claim_count) and rows == 0 and not calls

    return {
        "tool_call_count": len(calls),
        "tool_calls_by_kind": dict(kinds),
        "tool_status": dict(statuses),
        "capabilities_used": sorted(set(capabilities)),
        "missing_capabilities": missing,
        "unexpected_capabilities": unexpected,
        "over_budget": over_budget,
        "repeated_queries": repeated[:10],
        "evidence_rows": rows,
        "tool_errors": [item for item in errors if item][:10],
        "no_tool_call_but_claims": no_tool_call_but_claims,
        "evidence_gap": evidence_gap,
        "turns": len(turn_records or []),
        "as_of": snapshot.get("as_of"),
        "plan": {
            "required_capabilities": required,
            "max_tool_calls": max_calls,
        },
    }


def suggest_root_cause(
    diagnostics: dict[str, Any],
    *,
    passed: bool,
    gates: dict[str, bool],
    unsupported_count: int = 0,
    failed_fact_ids: list[str] | None = None,
) -> str | None:
    """根据答案 + 轨迹给出失败根因（通过时为 None）。"""
    if passed:
        return None
    if not gates.get("behavior", True):
        return RootCause.BEHAVIOR
    if not gates.get("safety", True) or unsupported_count > 0:
        return RootCause.HALLUCINATION
    if failed_fact_ids:
        if diagnostics.get("missing_capabilities") or diagnostics.get("tool_errors"):
            return RootCause.RETRIEVAL
        if diagnostics.get("no_tool_call_but_claims") or diagnostics.get("evidence_gap"):
            return RootCause.RETRIEVAL
        if diagnostics.get("evidence_rows", 0) > 0:
            return RootCause.SYNTHESIS
    return RootCause.UNKNOWN


def is_lucky_pass(
    *,
    passed: bool,
    diagnostics: dict[str, Any],
    required_numeric_fact_count: int,
) -> bool:
    """答案正确但完全没有取数证据：标记为幸运通过（仅诊断）。"""
    if not passed or required_numeric_fact_count <= 0:
        return False
    return diagnostics.get("tool_call_count", 0) == 0 or diagnostics.get("evidence_gap", False)
