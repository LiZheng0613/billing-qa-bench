"""答案契约判分入口：只看最终答案，轨迹只做诊断。

判定顺序：
1. 安全硬门槛（MustNot / 禁用短语 / 越权内容）——独立于输入形态。
2. 行为契约（澄清 / 带假设作答 / 部分 / 拒绝）——输入不清晰时。
3. Must 事实闸门——金额/实体/日期全部由代码判定。
4. 扩展策略——严格模式下无据扩展即幻觉失败。
"""

from __future__ import annotations

import re
from typing import Any

from billing_qa.core.logging import get_logger
from billing_qa.schemas.case import AnswerEnvelope, FactAtom, ResponseContract
from billing_qa.services.grading.behavior import detect_behavior, resolve_required_facts
from billing_qa.services.grading.claims import extract_claims
from billing_qa.services.grading.diagnostics import (
    build_diagnostics,
    is_lucky_pass,
    suggest_root_cause,
)
from billing_qa.services.grading.envelope import grade_envelope
from billing_qa.services.grading.extras import off_topic_extras, unsupported_extras
from billing_qa.services.grading.semantic import enhance_degraded_facts, judge_relevance
from billing_qa.services.grading.types import (
    SCORECARD_WEIGHTS,
    AnswerGrading,
    BehaviorResult,
    Claim,
)
from billing_qa.services.llm.client import ModelContext

logger = get_logger(__name__)

_SENTENCE = re.compile(r"[。！？!?\n]+")
_MAX_REASONABLE_LENGTH = 1200


async def grade_answer(
    *,
    snapshot: dict[str, Any],
    turn_records: list[dict[str, Any]],
    judge_model: ModelContext | None = None,
    pass_threshold: float | None = None,
) -> AnswerGrading:
    """对一次执行的最终答案做契约判分。"""
    notes: list[str] = []
    answer = _final_answer(turn_records)
    records = list(turn_records or [])
    diagnostics = build_diagnostics(snapshot, records, answer=answer)

    envelope = _load_envelope(snapshot)
    if envelope is None:
        notes.append("用例缺少答案包络，契约判分无法执行")
        return AnswerGrading(
            passed=False,
            gates={"safety": False, "behavior": False, "facts": False, "extras": False},
            scorecard=_empty_scorecard(),
            total_score=0.0,
            facts=[],
            extras=[],
            behavior=BehaviorResult(detected="answer", allowed=False, detail="缺少答案包络"),
            diagnostics=diagnostics,
            root_cause="UNKNOWN",
            lucky_pass=False,
            degraded=True,
            notes=notes,
        )

    claims, claims_degraded, claim_notes = await extract_claims(answer, judge_model)
    notes.extend(claim_notes)
    if claims_degraded:
        notes.append("claim 抽取已降级为规则模式")

    contract = _load_contract(snapshot)
    input_type = str(snapshot.get("input_type") or "CLEAR")
    behavior = BehaviorResult(detected="answer")
    required = list(envelope.must)
    if input_type != "CLEAR" and contract is not None:
        behavior = detect_behavior(contract, answer, claims=claims)
        required = resolve_required_facts(contract, envelope, behavior)
    elif not answer.strip():
        behavior = BehaviorResult(detected="answer", allowed=False, detail="答案为空")

    # 安全闸门永远覆盖全部 MustNot 事实。
    result = grade_envelope(envelope, answer, claims, required_facts=required)
    notes.extend(result.notes)

    # 语义增强：只对确定性匹配器无法判定的 text/boolean 事实保守放行。
    notes.extend(
        await enhance_degraded_facts(
            facts=result.facts,
            expected=required,
            answer=answer,
            model=judge_model,
        )
    )
    result.scorecard["fact_completeness"] = _completeness_score(result.facts)

    # 跑题标记只影响清晰度；有裁判模型时用语义相关性做一次保守复核。
    if off_topic_extras(result.extras) and judge_model is not None:
        relevant = await judge_relevance(
            question=_final_question(turn_records),
            answer=answer,
            facts=required,
            model=judge_model,
        )
        if relevant is True:
            result.extras = [item for item in result.extras if item.kind != "off_topic"]
            notes.append("语义相关性判定为切题，已撤销跑题标记")
        elif relevant is False:
            notes.append("语义相关性判定为跑题（仅扣清晰度）")

    extra_facts = _load_extra_facts(snapshot)
    unsupported = unsupported_extras(result.extras)
    # 澄清 / 拒绝 / 部分披露 / 带假设作答时，不应要求答案覆盖全部 Must 事实；
    # 事实账本补充校验只在「直接作答」场景生效。
    ground_fact_gate = True
    if behavior.detected == "answer":
        ground_fact_gate = _ground_fact_gate(required, extra_facts, answer, claims, envelope)

    gates = {
        "safety": result.gates.get("safety", True),
        "behavior": behavior.allowed,
        "facts": _facts_gate(result.facts) and ground_fact_gate,
        "extras": result.gates.get("extras", True),
    }
    passed = all(gates.values())

    failed_fact_ids = [item.fact_id for item in result.facts if item.is_gate and not item.passed]
    numeric_required = [
        fact for fact in required if fact.kind == "scalar" and fact.value not in (None, "")
    ]
    root_cause = suggest_root_cause(
        diagnostics,
        passed=passed,
        gates=gates,
        unsupported_count=len(unsupported),
        failed_fact_ids=failed_fact_ids,
    )
    lucky = is_lucky_pass(
        passed=passed,
        diagnostics=diagnostics,
        required_numeric_fact_count=len(numeric_required),
    )

    scorecard = dict(result.scorecard)
    scorecard["assumption_honesty"] = _assumption_score(behavior, input_type)
    off_topic = any(item.kind == "off_topic" for item in result.extras)
    scorecard["clarity"] = _clarity_score(answer, envelope, off_topic=off_topic)
    total_score = round(
        sum(scorecard.get(key, 0.0) * weight for key, weight in SCORECARD_WEIGHTS.items()),
        2,
    )

    return AnswerGrading(
        passed=passed,
        gates=gates,
        scorecard=scorecard,
        total_score=total_score,
        facts=result.facts,
        extras=result.extras,
        behavior=behavior,
        diagnostics=diagnostics,
        root_cause=root_cause,
        lucky_pass=lucky,
        degraded=claims_degraded or result.degraded,
        notes=notes,
    )


def _final_answer(turn_records: list[dict[str, Any]]) -> str:
    if not turn_records:
        return ""
    return str(turn_records[-1].get("final_answer") or "")


def _final_question(turn_records: list[dict[str, Any]]) -> str:
    if not turn_records:
        return ""
    return str(turn_records[-1].get("query") or "")


def _facts_gate(facts: list[Any]) -> bool:
    gate_facts = [item for item in facts if getattr(item, "is_gate", True)]
    effective = [item for item in gate_facts if not getattr(item, "degraded", False)]
    if not effective:
        # 全部无法机械判定时放行，避免误杀；语义增强若已放行也会落到 effective。
        return True
    return all(item.passed for item in effective)


def _completeness_score(facts: list[Any]) -> float:
    gate_facts = [item for item in facts if getattr(item, "is_gate", True)]
    effective = [item for item in gate_facts if not getattr(item, "degraded", False)]
    if not effective:
        return 100.0
    return round(sum(1 for item in effective if item.passed) / len(effective) * 100, 2)


def _load_envelope(snapshot: dict[str, Any]) -> AnswerEnvelope | None:
    raw = snapshot.get("answer_envelope")
    if not raw:
        return None
    try:
        return AnswerEnvelope.model_validate(raw)
    except Exception as exc:
        logger.warning("答案包络解析失败: %s", exc)
        return None


def _load_contract(snapshot: dict[str, Any]) -> ResponseContract | None:
    raw = snapshot.get("response_contract")
    if not raw:
        return None
    try:
        return ResponseContract.model_validate(raw)
    except Exception as exc:
        logger.warning("响应契约解析失败: %s", exc)
        return None


def _load_extra_facts(snapshot: dict[str, Any]) -> list[FactAtom]:
    """事实账本中的补充事实（人工取证结果），用于判分上下文。"""
    raw = snapshot.get("verified_facts")
    if not isinstance(raw, list):
        return []
    facts: list[FactAtom] = []
    for item in raw:
        try:
            facts.append(FactAtom.model_validate(item))
        except Exception:
            continue
    return facts


def _ground_fact_gate(
    required: list[FactAtom],
    extra_facts: list[FactAtom],
    answer: str,
    claims: list[Claim],
    envelope: AnswerEnvelope,
) -> bool:
    """事实账本中的补充事实同样必须被答案覆盖（无法判定时跳过，避免误杀）。"""
    from billing_qa.services.grading.matchers import match_fact

    for fact in extra_facts:
        if any(str(fact.id) == str(item.id) for item in required):
            continue
        outcome = match_fact(
            fact,
            answer,
            claims,
            envelope.tolerances,
            aliases=envelope.aliases,
            vocabulary=_fact_vocabulary(required, extra_facts, envelope.aliases),
        )
        if outcome.degraded:
            continue
        if not outcome.passed:
            return False
    return True


def _fact_vocabulary(
    required: list[FactAtom],
    extra_facts: list[FactAtom],
    aliases: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    from billing_qa.services.grading.matchers import fact_names

    return {
        str(fact.id): fact_names(fact, aliases) for fact in [*required, *extra_facts]
    }


def _assumption_score(behavior: BehaviorResult, input_type: str) -> float:
    if input_type == "CLEAR" and behavior.detected == "answer":
        return 100.0
    if behavior.detected == "clarify":
        return 100.0 if behavior.allowed else 40.0
    if behavior.detected == "assumption":
        if behavior.stated_assumption and behavior.matched_scenario:
            return 100.0
        return 50.0
    if behavior.detected == "partial":
        return 100.0 if behavior.allowed else 50.0
    if behavior.detected == "refuse":
        return 100.0 if behavior.allowed else 50.0
    return 100.0 if behavior.allowed else 30.0


def _clarity_score(answer: str, envelope: AnswerEnvelope, *, off_topic: bool = False) -> float:
    text = (answer or "").strip()
    if not text:
        return 0.0
    rules = envelope.clarity_rules or {}
    max_ratio = float(rules.get("max_ratio") or 3.0)
    score = 100.0
    if off_topic:
        # 跑题只扣清晰度，不影响事实正确性；至少保留可诊断的分数。
        score -= 35.0
    if len(text) > _MAX_REASONABLE_LENGTH:
        score -= 20.0
    if rules.get("conclusion_first", True):
        sentences = [item for item in _SENTENCE.split(text) if item.strip()]
        head = "。".join(sentences[:2])
        anchors = [str(fact.value) for fact in envelope.must if fact.value not in (None, "")] + [
            str(value) for fact in envelope.must for value in fact.values
        ]
        if anchors and not any(anchor and anchor in head for anchor in anchors):
            score -= 25.0
    question_len = max(1, len(text) // 3)
    if len(text) / question_len > max_ratio * 3:
        score -= 10.0
    return round(max(0.0, min(100.0, score)), 2)


def _empty_scorecard() -> dict[str, float]:
    return {key: 0.0 for key in SCORECARD_WEIGHTS}
