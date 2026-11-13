"""模板渲染与 JSONPath 提取单元测试。"""

from __future__ import annotations

import pytest

from billing_qa.core.exceptions import ValidationError
from billing_qa.services.adapter.extractor import (
    extract_all,
    extract_final_answer,
    extract_value,
)
from billing_qa.services.adapter.renderer import (
    find_variables,
    format_history,
    render_prompt,
    render_structure,
    render_text,
)


def test_find_variables_dedup_order() -> None:
    assert find_variables("{{a}} {{b}} {{ a }} {{c}}") == ["a", "b", "c"]


def test_render_text_reports_missing() -> None:
    rendered, missing = render_text("你好 {{name}}，目标：{{goal}}", {"name": "张三"})
    assert rendered == "你好 张三，目标：{{goal}}"
    assert missing == ["goal"]


def test_render_structure_keeps_type_for_full_placeholder() -> None:
    payload = {
        "query": "{{query}}",
        "history": "{{history}}",
        "meta": {"count": 3, "note": "第 {{turn}} 轮"},
    }
    rendered = render_structure(
        payload, {"query": "查账", "history": [{"role": "user", "content": "hi"}], "turn": 2}
    )
    assert rendered["query"] == "查账"
    assert rendered["history"] == [{"role": "user", "content": "hi"}]
    assert rendered["meta"] == {"count": 3, "note": "第 2 轮"}


def test_render_prompt_strict_raises() -> None:
    with pytest.raises(ValidationError):
        render_prompt("{{missing}}", {})


def test_render_prompt_non_strict_keeps_placeholder() -> None:
    assert render_prompt("{{missing}}", {}, strict=False) == "{{missing}}"


def test_format_history_styles() -> None:
    history = [
        {"role": "user", "content": "查账"},
        {"role": "assistant", "content": "请提供订单号"},
    ]
    assert format_history(history, style="text") == "用户: 查账\n客服: 请提供订单号"
    assert format_history(history, style="messages") == history
    assert format_history([], style="text") == "（暂无对话）"


def test_extract_value_and_fallbacks() -> None:
    payload = {
        "data": {
            "answer": "金额 ¥100",
            "raw_db_data": {"total": 100},
            "session_id": "s-1",
        }
    }
    assert extract_value(payload, "$.data.answer") == "金额 ¥100"
    assert extract_value(payload, "$.data.raw_db_data.total") == 100
    assert extract_value(payload, "$.not.exists") is None
    assert extract_value(payload, "not a valid jsonpath") is None
    assert extract_final_answer(payload) == "金额 ¥100"

    extracted = extract_all(payload, {"final_answer": "$.data.answer", "session_id": "$.data.session_id"})
    assert extracted == {"final_answer": "金额 ¥100", "session_id": "s-1"}


def test_extract_final_answer_fallback_paths() -> None:
    assert extract_final_answer({"content": "兜底文本"}) == "兜底文本"
    assert extract_final_answer({"data": {"content": "二级兜底"}}) == "二级兜底"
