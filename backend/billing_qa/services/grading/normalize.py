"""答案契约判分的归一化工具：别名、单位、金额容差、文本比较。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# 全角标点与常见分隔符，比较前统一剔除
_PUNCTUATION = re.compile(r"[\s,，。、；;：:！!？?（）()【】\[\]“”\"'‘’《》<>·|/\\]+")
_NEGATIONS = ("未", "没有", "不会", "并非", "不是", "不存在", "未曾", "无任何")


def normalize_text(text: str | None) -> str:
    """压缩空白与标点，便于中文短实体匹配。"""
    if not text:
        return ""
    return _PUNCTUATION.sub("", str(text)).lower()


def variants(term: str, aliases: dict[str, list[str]] | None) -> list[str]:
    """返回某个期望值的全部等价写法（含别名与原文）。"""
    result: list[str] = [str(term)]
    for canonical, alternatives in (aliases or {}).items():
        group = [canonical, *alternatives]
        if any(str(term) == item for item in group):
            for item in group:
                if item not in result:
                    result.append(str(item))
    return result


def contains_any(text: str, terms: list[str]) -> str | None:
    """返回首个在文本中命中的写法。"""
    normalized = normalize_text(text)
    for term in terms:
        candidate = normalize_text(term)
        if candidate and candidate in normalized:
            return term
    return None


def to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, TypeError):
        return None


def close_enough(actual: Any, expected: Any, tolerance: float) -> bool:
    left = to_decimal(actual)
    right = to_decimal(expected)
    if left is None or right is None:
        return str(actual).strip() == str(expected).strip()
    return abs(left - right) <= Decimal(str(tolerance or 0)) + Decimal("1e-9")


def money_tolerance(
    unit: str | None, tolerance: float, tolerances: dict[str, float] | None
) -> float:
    """按单位取默认容差：金额 0.01，百分比 0.1。"""
    if tolerance:
        return tolerance
    table = tolerances or {}
    if (unit or "CNY").upper() in {"CNY", "RMB", "元", "YUAN"}:
        return float(table.get("money", 0.01))
    if (unit or "").upper() in {"%", "PERCENT"}:
        return float(table.get("percent", 0.1))
    return 0.0


def is_negated(text: str, phrase: str, window: int = 6) -> bool:
    """判断短语是否处在否定语境中（例如「未出现系统错误」不算违规）。"""
    index = text.find(phrase)
    while index >= 0:
        prefix = text[max(0, index - window) : index]
        if any(negation in prefix for negation in _NEGATIONS):
            index = text.find(phrase, index + 1)
            continue
        return False
    return True


def find_phrase_violation(text: str, phrases: list[str], window: int = 6) -> str | None:
    """返回第一个未被否定的违规短语。"""
    for phrase in phrases or []:
        if not phrase:
            continue
        if phrase in text and not is_negated(text, phrase, window):
            return phrase
    return None


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
