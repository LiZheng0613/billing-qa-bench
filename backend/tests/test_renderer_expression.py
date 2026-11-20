"""模板表达式（Jinja2 过滤器）渲染测试。

覆盖：用户场景（变量拼接 + 去空格）、内置过滤器、平台扩展过滤器、
缺失变量/语法错误/沙箱逃逸的降级行为，以及历史占位符兼容性。
"""

from __future__ import annotations

import re

import pytest

from billing_qa.core.exceptions import ValidationError
from billing_qa.services.adapter.renderer import (
    find_variables,
    render_prompt,
    render_structure,
    render_text,
)


def test_header_prefix_and_space_removal() -> None:
    """x-agent-usr 场景：固定前缀 + 变量去空格拼接。"""
    rendered = render_structure(
        {"x-agent-usr": "admin_{{ custCode | replace(' ', '') }}"},
        {"custCode": "abc xxx"},
    )
    assert rendered == {"x-agent-usr": "admin_abcxxx"}


def test_expression_inside_nested_json() -> None:
    payload = {
        "headers": {"x-agent-usr": "admin_{{ custCode | replace(' ', '') }}"},
        "body": {"meta": {"round": "第 {{ turn }} 轮", "upper": "{{ name | upper }}"}},
        "history": "{{ history }}",
    }
    variables = {
        "custCode": "abc xxx",
        "turn": 2,
        "name": "zhang",
        "history": [{"role": "user", "content": "hi"}],
    }
    rendered = render_structure(payload, variables)
    assert rendered["headers"]["x-agent-usr"] == "admin_abcxxx"
    assert rendered["body"]["meta"] == {"round": "第 2 轮", "upper": "ZHANG"}
    # 整值占位符仍然保留原始类型
    assert rendered["history"] == [{"role": "user", "content": "hi"}]


def test_builtin_string_filters() -> None:
    assert render_text("{{ v | trim }}", {"v": "  x  "}) == ("x", [])
    assert render_text("{{ v | upper }}", {"v": "abc"}) == ("ABC", [])
    assert render_text("{{ v | lower }}", {"v": "ABC"}) == ("abc", [])
    assert render_text("{{ v | replace('a', 'b') }}", {"v": "a-a"}) == ("b-b", [])
    assert render_text("{{ v | replace('张三', '李四') }}", {"v": "你好张三"}) == ("你好李四", [])
    assert render_text("{{ v | length }}", {"v": "abc"}) == ("3", [])


def test_length_supports_structured_values() -> None:
    """结构体变量做表达式时按原类型计算，而不是按 JSON 字符串长度。"""
    assert render_text("{{ v | length }}", {"v": [1, 2, 3]}) == ("3", [])
    assert render_text("{{ v | length }}", {"v": {"a": 1, "b": 2}}) == ("2", [])


def test_custom_encoding_filters() -> None:
    assert render_text("{{ v | md5 }}", {"v": "abc"}) == (
        "900150983cd24fb0d6963f7d28e17f72",
        [],
    )
    assert render_text("{{ v | sha256 }}", {"v": "abc"}) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        [],
    )
    assert render_text("{{ v | base64 }}", {"v": "abc"}) == ("YWJj", [])
    assert render_text("{{ v | base64_encode }}", {"v": "abc"}) == ("YWJj", [])
    assert render_text("{{ v | base64_decode }}", {"v": "YWJj"}) == ("abc", [])
    assert render_text("{{ v | url_encode }}", {"v": "a b/中"}) == ("a%20b%2F%E4%B8%AD", [])
    assert render_text("{{ v | url_decode }}", {"v": "a%20b"}) == ("a b", [])


def test_time_and_uuid_globals() -> None:
    year, missing = render_text("{{ now('%Y') }}", {})
    assert re.fullmatch(r"\d{4}", year)
    assert missing == []

    value, missing = render_text("{{ uuid() }}", {})
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value)
    assert missing == []


def test_missing_variable_in_expression_keeps_literal() -> None:
    template = "admin_{{ custCode | replace(' ', '') }}"
    rendered, missing = render_text(template, {})
    assert rendered == template
    assert missing == ["custCode"]


def test_partial_render_keeps_only_missing_placeholder() -> None:
    """历史行为兼容：已知变量照常渲染，缺失变量保留原文。"""
    rendered, missing = render_text("你好 {{name}}，目标：{{goal}}", {"name": "张三"})
    assert rendered == "你好 张三，目标：{{goal}}"
    assert missing == ["goal"]


def test_none_value_is_treated_as_missing() -> None:
    rendered, missing = render_text("{{ v }}", {"v": None})
    assert rendered == "{{ v }}"
    assert missing == ["v"]


def test_syntax_error_is_reported_and_text_kept() -> None:
    errors: list[str] = []
    rendered, missing = render_text("{{ custCode | }}", {"custCode": "abc"}, errors)
    assert rendered == "{{ custCode | }}"
    assert missing == []
    assert errors and "语法错误" in errors[0]


def test_sandbox_blocks_attribute_escape() -> None:
    errors: list[str] = []
    template = "{{ ''.__class__ }}"
    rendered, missing = render_text(template, {}, errors)
    assert rendered == template
    assert missing == []
    assert errors and "unsafe" in errors[0]


def test_render_prompt_strict_raises_on_expression_error() -> None:
    with pytest.raises(ValidationError, match="模板表达式错误"):
        render_prompt("{{ custCode | }}", {"custCode": "abc"})


def test_bool_and_none_are_json_style() -> None:
    assert render_text("{{ v }}", {"v": True}) == ("true", [])
    assert render_text("{{ v }}", {"v": False}) == ("false", [])


def test_find_variables_supports_expressions_and_ignores_globals() -> None:
    template = "admin_{{ custCode | replace(' ', '') }} {{ turn }} {{ now('%Y') }}"
    assert find_variables(template) == ["custCode", "turn"]
    assert find_variables("{{ a }} {{ b }} {{ a }}") == ["a", "b"]


def test_find_variables_falls_back_when_syntax_invalid() -> None:
    # 语法错误时退回旧版占位符识别，保证预览变量来源仍可用
    assert find_variables("{{ custCode | }}") == ["custCode"]


def test_render_structure_collects_missing_and_errors() -> None:
    missing: list[str] = []
    errors: list[str] = []
    payload = {
        "header": "{{ custCode | replace(' ', '') }}",
        "body": {"x": "{{ goal }}"},
    }
    rendered = render_structure(payload, {}, missing, errors)
    assert rendered == payload
    assert missing == ["custCode", "goal"]
    assert errors == []
