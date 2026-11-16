"""答案断言抽取：规则正则为主，LLM 语义抽取为可选增强。"""

from __future__ import annotations

from typing import Any

from billing_qa.core.logging import get_logger
from billing_qa.services.engine.rule_evaluator import extract_claim_atoms
from billing_qa.services.grading.prompts import CLAIM_EXTRACT_SYSTEM, CLAIM_EXTRACT_USER
from billing_qa.services.grading.types import Claim
from billing_qa.services.llm.client import LLMClient, ModelContext
from billing_qa.services.llm.json_parser import parse_json_object

logger = get_logger(__name__)


def rule_claims(answer: str) -> list[Claim]:
    """确定性抽取：金额 / 笔数 / 日期。"""
    claims: list[Claim] = []
    for atom in extract_claim_atoms(answer or ""):
        claims.append(
            Claim(
                text=str(atom.get("text") or ""),
                kind=str(atom.get("kind") or "text"),
                value=atom.get("value"),
                unit=atom.get("unit"),
                evidence=str(atom.get("evidence") or ""),
                source="rule",
            )
        )
    return claims


async def extract_claims(
    answer: str,
    model: ModelContext | None = None,
    *,
    temperature: float = 0.0,
) -> tuple[list[Claim], bool, list[str]]:
    """返回 (claims, degraded, notes)。

    LLM 失败不阻断判分：规则抽取结果仍然可用，并把 ``degraded`` 标记出去。
    """
    claims = rule_claims(answer)
    notes: list[str] = []
    if model is None:
        return claims, True, ["未配置可用裁判模型，仅使用规则抽取"]

    prompt = CLAIM_EXTRACT_USER.format(answer=answer or "")
    try:
        async with LLMClient(model) as client:
            result = await client.chat(
                [
                    {"role": "system", "content": CLAIM_EXTRACT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                json_mode=True,
                temperature=temperature,
            )
        data = parse_json_object(result.content)
        llm_claims = _coerce_claims(data.get("claims"))
    except Exception as exc:
        logger.warning("claim 抽取降级: %s", exc)
        return claims, True, [f"LLM 抽取失败，已降级为规则抽取: {type(exc).__name__}"]

    merged = _dedupe([*claims, *llm_claims])
    return merged, False, notes


def _coerce_claims(raw: Any) -> list[Claim]:
    if not isinstance(raw, list):
        return []
    claims: list[Claim] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not text and not evidence:
            continue
        claims.append(
            Claim(
                text=text or evidence,
                kind=str(item.get("kind") or "text"),
                value=item.get("value"),
                unit=item.get("unit"),
                evidence=evidence or text,
                source="llm",
            )
        )
    return claims


def _dedupe(claims: list[Claim]) -> list[Claim]:
    seen: set[tuple[str, str, str]] = set()
    ordered: list[Claim] = []
    for claim in claims:
        key = (claim.kind, str(claim.value), claim.text)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(claim)
    return ordered
