"""确定性事实匹配器测试：金额/集合/日期/布尔/禁用短语。"""

from __future__ import annotations

from billing_qa.schemas.case import FactAtom
from billing_qa.services.grading.claims import rule_claims
from billing_qa.services.grading.matchers import match_banned, match_fact


def _scalar(value: float, unit: str = "CNY", tolerance: float = 0.0) -> FactAtom:
    return FactAtom(kind="scalar", value=value, unit=unit, tolerance=tolerance)


def test_scalar_matches_amount_with_currency_and_thousands() -> None:
    answer = "本期账单金额为 1,234.50 元。"
    outcome = match_fact(_scalar(1234.5), answer, rule_claims(answer))
    assert outcome.passed


def test_scalar_uses_tolerance() -> None:
    answer = "合计 100.02 元。"
    outcome = match_fact(_scalar(100.0), answer, rule_claims(answer), {"money": 0.01})
    assert not outcome.passed
    outcome = match_fact(_scalar(100.0), answer, rule_claims(answer), {"money": 0.05})
    assert outcome.passed


def test_set_requires_every_value() -> None:
    fact = FactAtom(kind="set", values=["套餐A", "套餐B"])
    partial = match_fact(fact, "当前订购了套餐A。", [])
    assert not partial.passed
    full = match_fact(fact, "当前订购了套餐A 与套餐B。", [])
    assert full.passed


def test_date_matches_normalized_value() -> None:
    fact = FactAtom(kind="date", value="2026-10-31")
    assert match_fact(fact, "账期截止 2026-10-31。", []).passed
    assert not match_fact(fact, "账期截止 2026-11-01。", []).passed


def test_keyword_fact_requires_hit() -> None:
    fact = FactAtom(kind="boolean", value=True, keywords=["已停机"])
    assert match_fact(fact, "该号码已停机。", []).passed
    assert not match_fact(fact, "该号码正常。", []).passed


def test_keyword_fact_without_keywords_is_degraded() -> None:
    fact = FactAtom(kind="text", value="服务态度")
    outcome = match_fact(fact, "服务态度很好", [])
    assert outcome.degraded
    assert not outcome.passed


def test_banned_text_ignores_negated_occurrence() -> None:
    fact = FactAtom(kind="text", value="系统错误")
    assert match_banned(fact, "本次查询未出现系统错误。") is None
    assert match_banned(fact, "本次查询出现系统错误。") == "系统错误"
