"""关键词筛选：分号分隔多关键词的解析与过滤条件构造。"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from billing_qa.core.search import keyword_condition, split_keywords
from billing_qa.models.test_case import TestCase as CaseModel


def test_split_keywords_supports_half_and_full_width_semicolons() -> None:
    assert split_keywords("账单;话费；余额") == ["账单", "话费", "余额"]


def test_split_keywords_trims_and_deduplicates() -> None:
    assert split_keywords("  账单 ; ;账单； 话费  ") == ["账单", "话费"]
    assert split_keywords("账单;Billing;billing") == ["账单", "Billing"]
    assert split_keywords(";;；") == []
    assert split_keywords(None) == []


def test_keyword_condition_is_or_across_terms_and_columns() -> None:
    condition = keyword_condition((CaseModel.name, CaseModel.code), "账单;话费")
    assert condition is not None

    sql = str(condition.compile(dialect=postgresql.dialect()))
    assert sql.count(" ILIKE ") == 4
    assert " OR " in sql


def test_keyword_condition_returns_none_for_empty_input() -> None:
    assert keyword_condition((CaseModel.name,), "  ;  ") is None
