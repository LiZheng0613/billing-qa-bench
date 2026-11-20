"""关键词筛选辅助。

界面上的搜索框统一支持用分号分隔多个关键词：命中任意一个关键词即视为匹配。
兼容半角分号 ``;`` 与全角分号 ``；``，并忽略首尾空白与重复词。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, or_

_KEYWORD_SEPARATOR = re.compile(r"[;；]+")
MAX_KEYWORD_TERMS = 20


def split_keywords(keyword: str | None, *, limit: int = MAX_KEYWORD_TERMS) -> list[str]:
    """把搜索框输入拆成关键词列表；空输入返回空列表。"""
    if not keyword:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for chunk in _KEYWORD_SEPARATOR.split(keyword):
        term = chunk.strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def keyword_condition(
    columns: Sequence[Any], keyword: str | None
) -> ColumnElement[bool] | None:
    """构造「任一分号分隔关键词命中任一列」的过滤条件。

    多个关键词之间是 OR 关系，便于一次性搜索多个目标；单个关键词内部仍按
    ``LIKE %keyword%`` 做模糊匹配，与原有行为保持一致。
    """
    terms = split_keywords(keyword)
    if not terms:
        return None
    return or_(
        *(
            or_(*(column.ilike(f"%{term}%") for column in columns))
            for term in terms
        )
    )
