"""用例事实验证与冻结闸门。

事实未经验证（VERIFIED）或显式豁免（WAIVED）时，禁止评审通过 / 冻结进数据集。
验证信息存放在 ``test_case.answer_envelope`` 的 JSON 内，不额外建表。
"""

from __future__ import annotations

from typing import Any

from billing_qa.core.exceptions import ValidationError
from billing_qa.schemas.case import AnswerEnvelope

VERIFIED_STATUSES: frozenset[str] = frozenset({"VERIFIED", "WAIVED"})
FACT_SECTIONS: tuple[str, ...] = ("must", "may", "must_not")


def envelope_to_dict(envelope: AnswerEnvelope | dict[str, Any] | None) -> dict[str, Any]:
    if envelope is None:
        return {}
    if isinstance(envelope, AnswerEnvelope):
        return envelope.model_dump()
    return dict(envelope)


def iter_envelope_facts(
    envelope: AnswerEnvelope | dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any]]]:
    """按 must / may / must_not 顺序返回 (section, fact)。"""
    raw = envelope_to_dict(envelope)
    items: list[tuple[str, dict[str, Any]]] = []
    for section in FACT_SECTIONS:
        for fact in raw.get(section) or []:
            if isinstance(fact, dict):
                items.append((section, fact))
    return items


def find_fact(
    envelope: AnswerEnvelope | dict[str, Any] | None, fact_id: str
) -> tuple[str, dict[str, Any]] | None:
    for section, fact in iter_envelope_facts(envelope):
        if str(fact.get("id")) == str(fact_id):
            return section, fact
    return None


def fact_status(fact: dict[str, Any]) -> str:
    return str(fact.get("verification_status") or "PENDING").upper()


def unverified_must_facts(
    envelope: AnswerEnvelope | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    raw = envelope_to_dict(envelope)
    return [
        fact
        for fact in raw.get("must") or []
        if isinstance(fact, dict) and fact_status(fact) not in VERIFIED_STATUSES
    ]


def assert_must_facts_verified(envelope: AnswerEnvelope | dict[str, Any] | None) -> None:
    pending = unverified_must_facts(envelope)
    if not pending:
        return
    labels = [
        str(fact.get("label") or fact.get("id") or "未命名事实") for fact in pending[:6]
    ]
    suffix = f" 等 {len(pending)} 条" if len(pending) > len(labels) else ""
    raise ValidationError(
        "以下 Must 事实尚未取证（VERIFIED）或豁免（WAIVED），不能评审通过或冻结："
        + "、".join(labels)
        + suffix
    )


def ensure_review_gate(case_data: dict[str, Any]) -> None:
    """创建 / 编辑 / 导入 / 评审时统一执行冻结闸门。"""
    if str(case_data.get("review_status") or "").upper() != "APPROVED":
        return
    if str(case_data.get("grading_mode") or "LEGACY").upper() != "ANSWER_CONTRACT":
        return
    assert_must_facts_verified(case_data.get("answer_envelope"))


def build_verified_ledger(envelope: AnswerEnvelope | dict[str, Any] | None) -> list[dict[str, Any]]:
    """供执行快照使用的事实账本：只纳入已取证且需要证据的 Must 事实。"""
    raw = envelope_to_dict(envelope)
    return [
        fact
        for fact in raw.get("must") or []
        if isinstance(fact, dict)
        and fact_status(fact) in VERIFIED_STATUSES
        and bool(fact.get("evidence_required", True))
    ]
