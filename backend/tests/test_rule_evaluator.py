"""规则硬校验单元测试。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from billing_qa.services.engine.rule_evaluator import (
    evaluate_rules,
    extract_amounts,
    extract_counts,
    extract_dates,
    merge_rule_results,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("含税金额为 ¥12,345.67", [Decimal("12345.67")]),
        ("合计 5678.90 元", [Decimal("5678.90")]),
        ("金额约 1.2 万元", [Decimal("12000.0")]),
        ("RMB 1000 元，另有 ¥200", [Decimal(1000), Decimal(200)]),
        ("没有金额", []),
    ],
)
def test_extract_amounts(text: str, expected: list[Decimal]) -> None:
    assert extract_amounts(text) == expected


def test_extract_counts_and_dates() -> None:
    text = "共 3 笔明细，2 张发票，账期为 2026-10-01 至 2026/10/31。"
    assert extract_counts(text) == [3, 2]
    assert extract_dates(text) == ["2026-10-01", "2026-10-31"]


def test_rules_all_pass() -> None:
    text = "订单 A2024001 含税金额 ¥12,345.67，共 3 笔明细，账期 2026-10-01。"
    result = evaluate_rules(
        text,
        {
            "amounts": [{"label": "含税金额", "value": 12345.67, "tolerance": 0.01}],
            "counts": [{"label": "笔数", "value": 3}],
            "dates": ["2026-10-01"],
            "must_contain": ["A2024001"],
            "must_not_contain": ["系统异常"],
        },
    )
    assert result.passed is True
    assert len(result.checks) == 5
    assert all(item.passed for item in result.checks)
    assert result.extracted["amounts"] == ["12345.67"]


def test_rules_amount_mismatch() -> None:
    result = evaluate_rules(
        "含税金额 ¥999.00，共 3 笔。",
        {
            "amounts": [{"label": "含税金额", "value": 12345.67, "tolerance": 0.01}],
            "counts": [{"label": "笔数", "value": 3}],
        },
    )
    assert result.passed is False
    assert result.failing_labels() == ["含税金额"]


def test_rules_empty_config_passes() -> None:
    result = evaluate_rules("任意文本", None)
    assert result.passed is True
    assert result.checks == []


def test_merge_rule_results_prefixes_turns() -> None:
    first = evaluate_rules("金额 ¥100 元", {"amounts": [{"label": "金额", "value": 100}]})
    second = evaluate_rules("金额 ¥200 元", {"amounts": [{"label": "金额", "value": 200}]})
    merged = merge_rule_results([first, second])
    assert merged.passed is True
    assert [item.label for item in merged.checks] == ["T1 · 金额", "T2 · 金额"]
    assert merged.extracted["amounts"] == ["100", "200"]
