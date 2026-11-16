"""响应契约判定：输入不清晰时，「对」指的是行为得体且假设自洽。

P0 采用确定性规则（疑问句 / 澄清词 / 拒绝词 / 假设声明 / 部分披露），
不依赖 LLM；规则无法判定时按「直接作答」处理并由契约检查是否漏了声明。
"""

from __future__ import annotations

import re
from typing import Any

from billing_qa.schemas.case import (
    AnswerEnvelope,
    AssumptionScenario,
    FactAtom,
    ResponseContract,
)
from billing_qa.services.grading.matchers import match_fact
from billing_qa.services.grading.types import BehaviorResult, Claim

_CLARIFY_MARKERS = (
    "请问",
    "您是",
    "您指",
    "您是想",
    "需要确认",
    "方便告诉",
    "能否提供",
    "请提供",
    "您说的",
    "麻烦确认",
)
_REFUSE_MARKERS = (
    "无法",
    "不能",
    "不支持",
    "无权",
    "抱歉",
    "超出",
    "不在服务范围",
    "没有权限",
)
_PARTIAL_MARKERS = ("仅", "只查到", "部分", "暂时只能", "未能查询", "未能获取")
_ASSUMPTION_MARKERS = ("按", "假设", "口径", "默认", "视为", "以账单", "按照")
_AMOUNT_PATTERN = re.compile(r"(?:¥|￥|RMB|人民币)?\s*-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?\s*(?:万元|万|元)")


def detect_behavior(
    contract: ResponseContract,
    answer: str,
    *,
    claims: list[Claim] | None = None,
) -> BehaviorResult:
    """按契约识别答案行为，并检查是否「答得体」。"""
    text = answer or ""
    claims = claims or []
    violations: list[str] = []
    detected = "answer"

    if _looks_clarify(text):
        detected = "clarify"
        clarify = contract.clarify
        if clarify is not None:
            missing = [slot for slot in clarify.must_ask_slot if not _slot_mentioned(slot, text)]
            if missing:
                violations.append(f"澄清未问到关键槽位: {', '.join(missing)}")
                return BehaviorResult(
                    detected=detected,
                    allowed=False,
                    missing_slots=missing,
                    violations=violations,
                    detail="澄清未覆盖必须确认的槽位",
                )
            if "amount" in clarify.must_not_assert and _AMOUNT_PATTERN.search(text):
                violations.append("澄清时不应先给出金额")
            if clarify.must_not_assert and _asserted_any(clarify.must_not_assert, text):
                violations.append("澄清时不应先给出结论")
    elif _looks_partial(text):
        detected = "partial"
    elif _looks_refuse(text):
        detected = "refuse"
    elif _looks_assumption(text):
        detected = "assumption"

    if detected == "answer":
        # 契约要求「带假设作答」时，直接作答且未声明假设 = 静默假设，必须判失败
        spec = contract.answer_with_assumption
        if spec is not None and spec.must_state_assumption:
            violations.append("歧义输入未声明假设即直接作答（静默假设）")
            return BehaviorResult(
                detected=detected,
                allowed=False,
                violations=violations,
                detail="缺少必要的假设声明",
            )

    matched_scenario, scenario_facts = _pick_scenario(
        contract, text, claims, detected=detected
    )
    allowed = detected in _detected_to_allowed(detected) or _is_allowed_alias(
        contract, detected
    )
    if not allowed:
        violations.append(f"行为 {detected} 不在允许集合 {contract.allowed} 内")

    if detected == "assumption":
        spec = contract.answer_with_assumption
        if spec is not None and spec.must_state_assumption and not _looks_assumption(text):
            violations.append("未声明假设")
        if matched_scenario is None:
            violations.append("声明的口径未匹配任何允许场景")
            allowed = False

    return BehaviorResult(
        detected=detected,
        allowed=allowed and not violations,
        stated_assumption=detected == "assumption" or _looks_assumption(text),
        matched_scenario=matched_scenario,
        scenario_facts=scenario_facts,
        violations=violations,
        detail="",
    )


def resolve_required_facts(
    contract: ResponseContract,
    envelope: AnswerEnvelope,
    behavior: BehaviorResult,
) -> list[FactAtom]:
    """按行为决定必须校验的事实集合。"""
    if behavior.detected in {"clarify", "refuse"}:
        return []
    if behavior.detected == "assumption":
        facts = [_fact_from_dict(item) for item in behavior.scenario_facts]
        return _enrich_from_envelope(facts, envelope)
    if behavior.detected == "partial":
        spec = contract.partial
        required_ids = set(spec.required_facts) if spec and spec.required_facts else set()
        if required_ids:
            return [fact for fact in envelope.must if fact.id in required_ids]
        return []
    return list(envelope.must)


def build_scenario_facts(scenario: AssumptionScenario) -> list[dict[str, Any]]:
    """把场景的 {事实名: 期望值} 转成可机械判定的 FactAtom。"""
    facts: list[FactAtom] = []
    for key, value in scenario.must.items():
        fact_id = f"{key}"
        if isinstance(value, bool):
            facts.append(FactAtom(id=fact_id, kind="boolean", value=value, keywords=[key]))
        elif isinstance(value, int | float):
            facts.append(FactAtom(id=fact_id, kind="scalar", value=float(value)))
        elif isinstance(value, list):
            facts.append(FactAtom(id=fact_id, kind="set", values=[str(item) for item in value]))
        else:
            raw = str(value)
            number = _extract_number(raw)
            text_part = _strip_number(raw)
            if number is None:
                facts.append(FactAtom(id=fact_id, kind="set", values=[raw]))
                continue
            if text_part:
                facts.append(FactAtom(id=f"{fact_id}.reason", kind="set", values=[text_part]))
            facts.append(
                FactAtom(
                    id=f"{fact_id}.amount",
                    kind="scalar",
                    value=number,
                    unit="CNY" if ("元" in raw or "¥" in raw or "￥" in raw) else None,
                )
            )
    return [fact.model_dump() for fact in facts]


def _fact_from_dict(item: dict[str, Any]) -> FactAtom:
    return FactAtom.model_validate(item)


def _enrich_from_envelope(facts: list[FactAtom], envelope: AnswerEnvelope) -> list[FactAtom]:
    """把场景事实的 id 对齐到答案包络里的业务名称，避免名称绑定误判归属。

    场景定义用 ``bill_total`` 这类接口字段名，包络事实用 ``账单总额`` 这类业务名。
    两者指向同一事实时，把包络事实的 label / aliases 并入场景事实，判分才能按
    「账单总额为 ¥268.50」正确锚定；否则会因只出现了另一个名称而误报归属冲突。
    """
    from billing_qa.services.name_binding import dedupe_names, normalize_name

    index: dict[str, FactAtom] = {}
    for fact in [*envelope.must, *envelope.may]:
        for name in [fact.label, *fact.aliases, fact.id]:
            key = normalize_name(str(name or ""))
            if key:
                index.setdefault(key, fact)
    enriched: list[FactAtom] = []
    for fact in facts:
        source = index.get(normalize_name(str(fact.id)))
        if source is not None:
            names = dedupe_names(
                [
                    *(source.aliases or []),
                    *([source.label] if source.label else []),
                    *([source.id] if source.id else []),
                ]
            )
            merged = dedupe_names([*(fact.aliases or []), *names])
            if merged:
                fact.aliases = merged
        enriched.append(fact)
    return enriched


def _pick_scenario(
    contract: ResponseContract,
    answer: str,
    claims: list[Claim],
    *,
    detected: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    spec = contract.answer_with_assumption
    if spec is None or not spec.scenarios:
        return None, []
    best_name: str | None = None
    best_facts: list[dict[str, Any]] = []
    best_score = -1
    for scenario in spec.scenarios:
        facts = [FactAtom.model_validate(item) for item in build_scenario_facts(scenario)]
        score = sum(1 for fact in facts if match_fact(fact, answer, claims).passed)
        if score > best_score:
            best_score = score
            best_name = scenario.assumption
            best_facts = [fact.model_dump() for fact in facts]
    if detected != "assumption" and best_score <= 0:
        return None, []
    return best_name, best_facts


def _detected_to_allowed(detected: str) -> set[str]:
    return {
        "answer": {"answer_with_assumption"} if detected == "assumption" else set(),
        "clarify": {"clarify"},
        "assumption": {"answer_with_assumption"},
        "partial": {"partial"},
        "refuse": {"refuse"},
    }.get(detected, set())


def _is_allowed_alias(contract: ResponseContract, detected: str) -> bool:
    if detected == "assumption":
        return "answer_with_assumption" in contract.allowed
    return detected in contract.allowed


def _looks_clarify(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith(("?", "？")):
        return True
    return any(marker in stripped for marker in _CLARIFY_MARKERS)


def _looks_refuse(text: str) -> bool:
    if not text.strip():
        return False
    return any(marker in text for marker in _REFUSE_MARKERS)


def _looks_partial(text: str) -> bool:
    return any(marker in text for marker in _PARTIAL_MARKERS)


def _looks_assumption(text: str) -> bool:
    return any(marker in text for marker in _ASSUMPTION_MARKERS)


def _slot_mentioned(slot: str, text: str) -> bool:
    aliases = {
        "time_range": ("时间", "账期", "月份", "哪个月", "期间", "本月", "上月", "几月"),
        "focus": ("账单", "明细", "用量", "套餐", "哪方面", "想看", "关注"),
        "customer": ("客户", "号码", "手机", "账号", "哪个"),
        "object": ("套餐", "资费", "业务", "哪一项"),
        "caliber": ("口径", "含税", "不含税", "账期", "自然月"),
    }
    keywords = aliases.get(slot)
    if keywords is None:
        return slot in text
    return any(keyword in text for keyword in keywords)


def _asserted_any(names: list[str], text: str) -> bool:
    """must_not_assert 中的语义槽位是否在答案里被断言。"""
    mapping = {
        "amount": bool(_AMOUNT_PATTERN.search(text)),
        "conclusion": any(word in text for word in ("所以", "结论", "主要是", "原因是")),
        "entity": False,
    }
    return any(mapping.get(name, False) for name in names)


def _extract_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    value = float(match.group(0))
    if "万" in text:
        value *= 10000
    return value


def _strip_number(text: str) -> str:
    cleaned = re.sub(r"-?\d+(?:,\d{3})*(?:\.\d+)?", "", text)
    cleaned = re.sub(r"[¥￥元万元/，,。;；]+", " ", cleaned).strip()
    return cleaned
