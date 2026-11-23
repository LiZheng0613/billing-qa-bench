"""条件解析引擎（extractors 规则对象形态）单元测试。

场景 A–E 与方案阶段的原型验证一一对应；兼容性用例保证字符串形态行为不变。
"""

from __future__ import annotations

from typing import Any

import pytest

from billing_qa.services.adapter.extraction import (
    ExtractionContext,
    evaluate_rules,
    evaluate_spec,
    is_extraction_spec,
    validate_extractors,
)
from billing_qa.services.adapter.extractor import (
    extract_all,
    extract_all_traced,
    extract_final_answer,
    extract_value,
)

# ------------------------------------------------------------------ #
# 场景数据
# ------------------------------------------------------------------ #

FRAMES_A = [
    {"type": "text", "message": "开场白", "context": "上下文1"},
    {"type": "chart", "message": "图表结论A"},
    {"type": "text", "context": "上下文2"},
    {"type": "chart", "payload": {"message": "图表结论B(子层)"}},  # 最后一次 chart
    {"type": "text", "context": "上下文3"},
]
FRAMES_B = [
    {"type": "text", "context": "上下文1"},
    {"type": "text", "message": "普通回复", "context": "上下文2"},
    {"type": "text", "context": "最后一次上下文"},
]
FRAMES_D = [
    {"type": "text", "context": "上下文1"},
    {"wrapper": {"inner": {"type": "chart", "data": [1, 2]}, "message": "chart所在包的message"}},
]
FRAMES_E = [
    {"type": "table", "summary": "表格结论"},
    {"type": "text", "context": "上下文1"},
]

CHART_RULES = [
    {
        "id": "IF-最后一次chart",
        "path": "$..[?(@.type=='chart')]",
        "occurrence": "last",
        "take": ["$.message", "$..message"],
        "take_parent": ["$.message"],
    },
    {"id": "ELSE-最后一次context", "path": "$..context", "occurrence": "last"},
]


def _ctx(frames: list[dict], content: str = "", payload: dict | None = None) -> ExtractionContext:
    return ExtractionContext(payload=payload or {}, frames=frames, content=content)


# ------------------------------------------------------------------ #
# 场景 A–E：if / elif / else 语义
# ------------------------------------------------------------------ #


def test_scene_a_last_chart_with_child_message() -> None:
    """有 chart：取最后一次 chart 的 message（同级缺省时落到下层）。"""
    value, trace = evaluate_spec(_ctx(FRAMES_A), {"rules": CHART_RULES})
    assert value == "图表结论B(子层)"
    assert trace["rule"] == "IF-最后一次chart"
    assert trace["take"] == "$..message"
    assert trace["source"] == "frame"
    assert trace["frame_index"] == 3


def test_scene_a_first_occurrence_picks_earlier_chart() -> None:
    """occurrence=first 时取第一次出现的 chart（同级 message 命中）。"""
    rules = [
        {"path": "$..[?(@.type=='chart')]", "occurrence": "first", "take": ["$.message"]},
    ]
    value, _ = evaluate_spec(_ctx(FRAMES_A), {"rules": rules})
    assert value == "图表结论A"


def test_scene_b_no_chart_falls_to_last_context() -> None:
    """没有 chart：走 else 分支，取最后一次 context。"""
    value, trace = evaluate_spec(_ctx(FRAMES_B), {"rules": CHART_RULES})
    assert value == "最后一次上下文"
    assert trace["rule"] == "ELSE-最后一次context"
    assert trace["source"] == "frame"
    assert trace["frame_index"] == 2


def test_scene_c_no_match_returns_none() -> None:
    """都没有：规则链未命中，由规格级兜底处理（引擎返回 None）。"""
    value, trace = evaluate_spec(_ctx([{"type": "text", "note": "-"}]), {"rules": CHART_RULES})
    assert value is None
    assert trace is None


def test_scene_d_parent_level_message() -> None:
    """chart 的 message 在上级：take_parent 命中。"""
    value, trace = evaluate_spec(_ctx(FRAMES_D), {"rules": CHART_RULES})
    assert value == "chart所在包的message"
    assert trace["take"] == "parent::$.message"


def test_scene_e_multi_branch_if_elif_else() -> None:
    """if chart / elif table / else context：table 分支生效。"""
    rules = [
        {
            "id": "IF-chart",
            "path": "$..[?(@.type=='chart')]",
            "occurrence": "last",
            "take": ["$.message", "$..message"],
        },
        {"id": "ELIF-table", "path": "$..[?(@.type=='table')].summary", "occurrence": "last"},
        {"id": "ELSE-context", "path": "$..context", "occurrence": "last"},
    ]
    value, trace = evaluate_spec(_ctx(FRAMES_E), {"rules": rules})
    assert value == "表格结论"
    assert trace["rule"] == "ELIF-table"


def test_direct_rule_without_take_returns_match_value() -> None:
    """不配 take 的直取规则返回匹配值本身。"""
    rules = [{"path": "$..context", "occurrence": "last"}]
    value, _ = evaluate_spec(_ctx(FRAMES_B), {"rules": rules})
    assert value == "最后一次上下文"


def test_nested_condition_inside_frames() -> None:
    """帧内嵌套结构（非帧根）也能被 $.. 递归命中。"""
    frames = [{"data": {"contents": [{"type": "chart", "message": "嵌套图表"}]}}]
    rules = [{"path": "$..[?(@.type=='chart')]", "take": ["$.message"]}]
    value, _ = evaluate_spec(_ctx(frames), {"rules": rules})
    assert value == "嵌套图表"


def test_occurrence_index_selection() -> None:
    """occurrence 支持整数下标。"""
    rules = [{"path": "$..[?(@.type=='chart')]", "occurrence": 0, "take": ["$.message"]}]
    value, _ = evaluate_spec(_ctx(FRAMES_A), {"rules": rules})
    assert value == "图表结论A"
    rules_oob = [{"path": "$..[?(@.type=='chart')]", "occurrence": 9, "take": ["$.message"]}]
    value, _ = evaluate_spec(_ctx(FRAMES_A), {"rules": rules_oob})
    assert value is None


def test_frames_scoped_path_only_matches_frames() -> None:
    """$._frames[?...] 只扫帧；payload 内的 chart 不受影响（反向隔离）。"""
    ctx = _ctx(FRAMES_A, payload={"type": "chart", "message": "合并元数据里的chart"})
    rules = [
        {
            "path": "$._frames[?(@.type=='chart')]",
            "occurrence": "last",
            "take": ["$.message", "$..message"],
        }
    ]
    value, _ = evaluate_spec(ctx, {"rules": rules})
    assert value == "图表结论B(子层)"


def test_payload_matches_with_empty_frames() -> None:
    """非流式（frames 为空）：规则在 JSON body 上求值。"""
    payload = {"data": {"contents": [{"type": "chart"}, {"type": "chart", "message": "第二个"}]}}
    rules = [
        {"path": "$.data.contents[?(@.type=='chart')]", "occurrence": "last", "take": ["$.message"]}
    ]
    value, _ = evaluate_spec(ExtractionContext(payload=payload), {"rules": rules})
    assert value == "第二个"


def test_invalid_runtime_spec_degrades_to_none() -> None:
    """运行期非法规则降级为未命中，不抛异常。"""
    value, trace = evaluate_spec(_ctx(FRAMES_A), {"rules": [{"path": "not valid {"}]})
    assert value is None
    assert trace is None
    # 规格缺 rules 同样按未命中处理
    value, _ = evaluate_spec(_ctx(FRAMES_A), {"fallback_content": True})
    assert value is None


def test_evaluate_rules_returns_none_for_empty_rules() -> None:
    assert evaluate_rules({"a": 1}, []) == (None, None)


# ------------------------------------------------------------------ #
# extractor 兼容性：字符串形态行为不变
# ------------------------------------------------------------------ #


def test_extract_value_occurrence_param() -> None:
    payload = {"data": {"list": ["a", "b", "c"]}}
    assert extract_value(payload, "$.data.list[*]") == ["a", "b", "c"]  # 多命中返回列表（历史行为）
    assert extract_value(payload, "$.data.list[*]", occurrence="last") == "c"
    assert extract_value(payload, "$.data.list[*]", occurrence=1) == "b"
    assert extract_value(payload, "$.data.list[*]", occurrence=9) is None
    assert extract_value(payload, "$.not.exists") is None
    assert extract_value(payload, "not a valid jsonpath") is None


def test_extract_final_answer_string_behavior_unchanged() -> None:
    """字符串 final_answer：表达式 + 内置兜底路径，行为与历史一致。"""
    assert extract_final_answer({"data": {"answer": "金额 ¥100"}}) == "金额 ¥100"
    assert extract_final_answer({"content": "兜底文本"}) == "兜底文本"
    assert extract_final_answer({"data": {"content": "二级兜底"}}) == "二级兜底"


def test_extract_final_answer_spec_chain() -> None:
    """规则对象：规则链 -> legacy_fallback -> fallback_content 依次兜底。"""
    spec = {"rules": [{"path": "$..[?(@.type=='chart')]", "take": ["$.message"]}]}
    # 命中规则
    ctx = ExtractionContext(payload={}, frames=[{"type": "chart", "message": "图表答案"}])
    assert extract_final_answer(ctx, {"final_answer": spec}) == "图表答案"
    # 未命中 + fallback_content 默认开 -> 聚合文本
    ctx2 = ExtractionContext(payload={}, frames=[{"type": "text"}], content="聚合文本")
    assert extract_final_answer(ctx2, {"final_answer": spec}) == "聚合文本"
    # fallback_content 关闭 -> 不兜底
    spec_off = {**spec, "fallback_content": False}
    assert extract_final_answer(ctx2, {"final_answer": spec_off}) is None
    # legacy_fallback 开 -> 走内置兜底路径
    spec_legacy = {**spec, "fallback_content": False, "legacy_fallback": True}
    ctx3 = ExtractionContext(payload={"content": "内置兜底文本"}, frames=[])
    assert extract_final_answer(ctx3, {"final_answer": spec_legacy}) == "内置兜底文本"


def test_extract_final_answer_spec_object_value_serialized() -> None:
    """message 为对象时按 JSON 序列化参与判分。"""
    spec = {"rules": [{"path": "$..[?(@.type=='chart')]", "take": ["$.message"]}]}
    ctx = ExtractionContext(payload={}, frames=[{"type": "chart", "message": {"title": "卡片"}}])
    assert extract_final_answer(ctx, {"final_answer": spec}) == '{"title": "卡片"}'


def test_extract_all_mixes_string_and_spec() -> None:
    """extract_all 字段级混用：字符串与规则对象互不影响。"""
    mapping = {
        "final_answer": {
            "rules": [
                {
                    "path": "$..[?(@.type=='chart')]",
                    "occurrence": "last",
                    "take": ["$.message", "$..message"],
                },
                {"path": "$..context", "occurrence": "last"},
            ]
        },
        "session_id": "$.data.sessionid",
    }
    ctx = ExtractionContext(
        payload={"data": {"sessionid": "s-1"}},
        frames=[{"type": "chart", "payload": {"message": "答案"}}],
    )
    extracted = extract_all(ctx, mapping)
    assert extracted["final_answer"] == "答案"
    assert extracted["session_id"] == "s-1"


def test_extract_all_traced_returns_trace() -> None:
    mapping = {
        "final_answer": {
            "rules": [{"id": "r1", "path": "$..[?(@.type=='chart')]", "take": ["$.message"]}]
        },
        "session_id": "$.data.sessionid",
    }
    ctx = ExtractionContext(payload={}, frames=[{"type": "chart", "message": "命中"}])
    fields, traces = extract_all_traced(ctx, mapping)
    assert fields["final_answer"] == "命中"
    assert traces["final_answer"]["rule"] == "r1"
    assert "session_id" not in traces  # 字符串形态不产生轨迹


def test_extract_all_legacy_payload_still_works() -> None:
    """旧签名（传 payload 而非 ExtractionContext）行为不变。"""
    payload = {"data": {"answer": "金额 ¥100", "session_id": "s-1"}}
    extracted = extract_all(
        payload, {"final_answer": "$.data.answer", "session_id": "$.data.session_id"}
    )
    assert extracted == {"final_answer": "金额 ¥100", "session_id": "s-1"}


def test_is_extraction_spec() -> None:
    assert is_extraction_spec({"rules": []}) is True
    assert is_extraction_spec("$.data.answer") is False
    assert is_extraction_spec({"final_answer": "$"}) is False  # 不含 rules 键的对象不视为规格
    assert is_extraction_spec(None) is False


# ------------------------------------------------------------------ #
# 保存期校验
# ------------------------------------------------------------------ #


def test_validate_extractors_accepts_valid_config() -> None:
    mapping = {
        "final_answer": {
            "rules": [
                {
                    "id": "if-chart",
                    "path": "$..[?(@.type=='chart')]",
                    "occurrence": "last",
                    "take": ["$.message", "$..message"],
                    "take_parent": ["$.message"],
                },
                {"path": "$..context", "occurrence": "last"},
            ],
            "fallback_content": True,
            "legacy_fallback": False,
        },
        "session_id": "$.data.sessionid",
    }
    assert validate_extractors(mapping) is mapping
    assert validate_extractors(None) is None


def test_validate_extractors_rejects_bad_spec() -> None:
    cases: list[Any] = [
        {"final_answer": {}},  # 缺 rules
        {"final_answer": {"rules": []}},  # 空 rules
        {"final_answer": {"rules": [{"take": ["$.message"]}]}},  # 缺 path
        {"final_answer": {"rules": [{"path": "not valid {"}]}},  # 非法 JSONPath
        {"final_answer": {"rules": [{"path": "$.a", "occurrence": "middle"}]}},  # 非法 occurrence
        {"final_answer": {"rules": [{"path": "$.a", "take": "message"}]}},  # take 非数组
        {"final_answer": {"rules": [{"path": "$.a", "take": ["message"]}]}},  # 相对路径缺 $ 前缀
        {"final_answer": {"rules": [{}], "unknown_key": 1}},  # 规格未知键
        {"final_answer": {"rules": [{"path": "$.a", "foo": 1}]}},  # 规则未知键
        {"final_answer": {"rules": [{"path": "$.a", "occurrence": -1}]}},  # 负下标
    ]
    for mapping in cases:
        with pytest.raises(ValueError):
            validate_extractors(mapping)


def test_validate_extractors_string_expression_never_blocks() -> None:
    """字符串表达式保存不阻断（历史兼容），即使语法非法。"""
    mapping = {"final_answer": "not valid {"}
    assert validate_extractors(mapping) is mapping


def test_validate_extractors_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        validate_extractors(["$.a"])
    with pytest.raises(ValueError):
        validate_extractors({"final_answer": 123})
