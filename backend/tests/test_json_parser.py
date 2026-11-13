"""LLM 输出 JSON 解析单元测试。"""

from __future__ import annotations

import pytest

from billing_qa.core.exceptions import ValidationError
from billing_qa.services.llm.json_parser import parse_json_object, parse_structured


def test_plain_json() -> None:
    assert parse_json_object('{"pass": true, "score": 90}') == {"pass": True, "score": 90}


def test_markdown_fenced_json() -> None:
    text = '下面是结果：\n```json\n{"pass": false, "reason": "数值不符"}\n```\n以上。'
    assert parse_json_object(text) == {"pass": False, "reason": "数值不符"}


def test_fenced_without_language() -> None:
    assert parse_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_noise_around_object() -> None:
    text = '模型输出：{"status": "CONTINUE", "query": "继续追问"} 结束'
    parsed = parse_json_object(text)
    assert parsed["status"] == "CONTINUE"
    assert parsed["query"] == "继续追问"


def test_list_first_dict() -> None:
    assert parse_json_object('[{"ok": true}]') == {"ok": True}


def test_empty_raises() -> None:
    with pytest.raises(ValidationError):
        parse_json_object("   ")


def test_unparsable_raises() -> None:
    with pytest.raises(ValidationError):
        parse_json_object("完全不是 JSON")


def test_parse_structured_missing_key() -> None:
    with pytest.raises(ValidationError) as exc:
        parse_structured('{"thought": "x", "status": "CONTINUE"}', ["thought", "status", "query"])
    assert "query" in str(exc.value)
