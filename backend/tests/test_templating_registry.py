"""模板表达式能力包（templating）的扩展与兼容性测试。"""

from __future__ import annotations

import pytest

from billing_qa.services import templating
from billing_qa.services.adapter.renderer import render_text as legacy_render_text
from billing_qa.services.templating import (
    available_filters,
    available_globals,
    find_variables,
    register_filter,
    register_global,
    render_text,
)


def test_legacy_renderer_reexports_capability() -> None:
    """renderer 兼容层与能力包指向同一实现。"""
    assert legacy_render_text is render_text


def test_available_filters_and_globals() -> None:
    filters = set(available_filters())
    assert {"replace", "upper", "lower", "trim", "length", "md5", "url_encode"} <= filters
    assert {"now", "uuid"} <= set(available_globals())


def test_register_filter_is_reusable() -> None:
    register_filter("admin_user_test", lambda code: f"admin_{str(code).replace(' ', '')}")
    rendered, missing = render_text(
        "{{ custCode | admin_user_test }}", {"custCode": "abc xxx"}
    )
    assert rendered == "admin_abcxxx"
    assert missing == []
    assert "admin_user_test" in available_filters()


def test_register_global_excluded_from_variable_detection() -> None:
    register_global("req_label_test", lambda prefix: f"{prefix}-id")
    template = "{{ req_label_test('req') }}"
    assert find_variables(template) == []
    assert render_text(template, {}) == ("req-id", [])


def test_register_rejects_invalid_names() -> None:
    with pytest.raises(ValueError):
        register_filter("bad-name", lambda value: value)
    with pytest.raises(ValueError):
        register_global("1bad", lambda: "x")


def test_register_rejects_non_callable() -> None:
    with pytest.raises(TypeError):
        register_filter("not_callable_test", "x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        register_global("not_callable_global_test", 1)  # type: ignore[arg-type]


def test_capability_package_exports_public_api() -> None:
    assert set(templating.__all__) == {
        "FULL_PLACEHOLDER",
        "PLACEHOLDER",
        "available_filters",
        "available_globals",
        "find_variables",
        "register_filter",
        "register_global",
        "render_prompt",
        "render_structure",
        "render_text",
    }
