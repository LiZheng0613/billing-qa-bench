"""规则硬校验：金额 / 笔数 / 日期 / 关键词的精确提取比对。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# 金额：带货币符号（单位可选）或带单位后缀（符号可选）
_CURRENCY_AMOUNT = re.compile(
    r"(?:¥|￥|RMB|人民币)\s*"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(万元|万|元)?"
)
_UNIT_AMOUNT = re.compile(
    r"(?<![\d.])"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(万元|万|元)"
)
_COUNT_PATTERN = re.compile(r"(\d+)\s*(笔|条|单|次|个|张|份|项)")
_DATE_PATTERN = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?")


@dataclass
class RuleCheck:
    type: str
    label: str
    expected: Any
    actual: Any
    passed: bool
    evidence: str = ""


@dataclass
class RuleResult:
    passed: bool = True
    checks: list[RuleCheck] = field(default_factory=list)
    extracted: dict[str, list] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "type": item.type,
                    "label": item.label,
                    "expected": item.expected,
                    "actual": item.actual,
                    "passed": item.passed,
                    "evidence": item.evidence,
                }
                for item in self.checks
            ],
            "extracted": self.extracted,
        }

    def failing_labels(self) -> list[str]:
        return [item.label for item in self.checks if not item.passed]


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None


def extract_amounts(text: str) -> list[Decimal]:
    """提取文本中所有金额，统一换算为元；符号/单位两种写法都覆盖。"""
    matched: dict[int, Decimal] = {}
    for pattern in (_CURRENCY_AMOUNT, _UNIT_AMOUNT):
        for match in pattern.finditer(text or ""):
            # 以数字本身的起始位置去重，避免「¥100」与「100 元」重复计数
            if match.start(1) in matched:
                continue
            number = _to_decimal(match.group(1))
            if number is None:
                continue
            unit = match.group(2)
            if unit in {"万", "万元"}:
                number = number * Decimal(10000)
            matched[match.start(1)] = number
    return [matched[key] for key in sorted(matched)]


def extract_counts(text: str) -> list[int]:
    return [int(match.group(1)) for match in _COUNT_PATTERN.finditer(text or "")]


def extract_dates(text: str) -> list[str]:
    dates: list[str] = []
    for match in _DATE_PATTERN.finditer(text or ""):
        year, month, day = match.groups()
        dates.append(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    return dates


def evaluate_rules(text: str, config: dict[str, Any] | None) -> RuleResult:
    """执行规则校验；config 为空时视为通过。"""
    result = RuleResult()
    text = text or ""
    amounts = extract_amounts(text)
    counts = extract_counts(text)
    dates = extract_dates(text)
    result.extracted = {
        "amounts": [str(item) for item in amounts],
        "counts": counts,
        "dates": dates,
    }
    if not config:
        return result

    for item in config.get("amounts") or []:
        expected = Decimal(str(item.get("value")))
        tolerance = Decimal(str(item.get("tolerance") or 0))
        matched = next((value for value in amounts if abs(value - expected) <= tolerance), None)
        result.checks.append(
            RuleCheck(
                type="amount",
                label=str(item.get("label") or "金额"),
                expected=str(expected),
                actual=[str(value) for value in amounts],
                passed=matched is not None,
                evidence=_evidence(text, str(item.get("value"))),
            )
        )

    for item in config.get("counts") or []:
        expected = int(item.get("value"))
        result.checks.append(
            RuleCheck(
                type="count",
                label=str(item.get("label") or "笔数"),
                expected=expected,
                actual=counts,
                passed=expected in counts,
                evidence=_evidence(text, str(expected)),
            )
        )

    for expected_date in config.get("dates") or []:
        normalized = _normalize_date(str(expected_date))
        result.checks.append(
            RuleCheck(
                type="date",
                label=f"日期 {expected_date}",
                expected=normalized,
                actual=dates,
                passed=normalized in dates if normalized else False,
                evidence=_evidence(text, str(expected_date)),
            )
        )

    for keyword in config.get("must_contain") or []:
        result.checks.append(
            RuleCheck(
                type="must_contain",
                label=f"必须包含「{keyword}」",
                expected=keyword,
                actual=None,
                passed=str(keyword) in text,
                evidence=_evidence(text, str(keyword)),
            )
        )

    for keyword in config.get("must_not_contain") or []:
        result.checks.append(
            RuleCheck(
                type="must_not_contain",
                label=f"不得包含「{keyword}」",
                expected=keyword,
                actual=None,
                passed=str(keyword) not in text,
            )
        )

    result.passed = all(item.passed for item in result.checks)
    return result


def _normalize_date(value: str) -> str | None:
    match = _DATE_PATTERN.search(value)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return None


def _evidence(text: str, needle: str, window: int = 40) -> str:
    index = text.find(needle)
    if index < 0:
        return ""
    start = max(0, index - window)
    end = min(len(text), index + len(needle) + window)
    return text[start:end].replace("\n", " ")


def merge_rule_results(results: list[RuleResult]) -> RuleResult:
    """合并多轮规则校验结果。"""
    merged = RuleResult()
    for index, item in enumerate(results, start=1):
        for check in item.checks:
            merged.checks.append(
                RuleCheck(
                    type=check.type,
                    label=f"T{index} · {check.label}",
                    expected=check.expected,
                    actual=check.actual,
                    passed=check.passed,
                    evidence=check.evidence,
                )
            )
        for key, values in item.extracted.items():
            merged.extracted.setdefault(key, [])
            merged.extracted[key].extend(
                value for value in values if value not in merged.extracted[key]
            )
    merged.passed = all(item.passed for item in merged.checks)
    return merged
