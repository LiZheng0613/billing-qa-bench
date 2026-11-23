"""JSONPath 白盒响应提取器：字符串表达式 + 条件规则两种形态。

- 字符串值（如 ``"$.data.answer"``）：走 :func:`extract_value`，行为与历史版本一致；
- 条件规则对象（含 ``rules`` 键，见 ``extraction.py``）：按序求值规则，
  首个产出非空值的规则生效，即 if / elif / else 语义。

提取入参既支持原始 payload（兼容旧调用），也支持
:class:`~billing_qa.services.adapter.extraction.ExtractionContext`
（流式场景携带有序 SSE 帧与聚合文本）。
"""

from __future__ import annotations

import json
from typing import Any

from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.exceptions import JsonPathParserError

from billing_qa.core.logging import get_logger
from billing_qa.services.adapter.extraction import (
    ExtractionContext,
    evaluate_spec,
    is_extraction_spec,
)

logger = get_logger(__name__)

DEFAULT_EXTRACTORS: dict[str, str] = {
    "final_answer": "$.data.answer",
    "session_id": "$.data.session_id",
    "intent": "$.data.intent",
    "generated_sql": "$.data.generated_sql",
    "api_params": "$.data.api_params",
    "raw_db_data": "$.data.raw_db_data",
    "tool_calls": "$.data.tool_calls",
}

FALLBACK_ANSWER_PATHS: tuple[str, ...] = (
    "$.data.answer",
    "$.answer",
    "$.data.content",
    "$.content",
    "$.data.text",
    "$.text",
    "$.result",
    "$.data.result",
)


def extract_value(payload: Any, expression: str, occurrence: Any = "first") -> Any:
    """执行单条 JSONPath；无匹配或语法错误返回 None。

    ``occurrence`` 为 ``first``（默认，历史行为）时：单命中返回标量，
    多命中返回列表；``last`` 返回最后一个命中；整数下标返回指定命中。
    """
    if payload is None or not expression:
        return None
    try:
        matches = jsonpath_parse(expression).find(payload)
    except (JsonPathParserError, Exception) as exc:
        logger.warning("JSONPath 解析失败 %s: %s", expression, exc)
        return None
    if not matches:
        return None
    values = [match.value for match in matches]
    if occurrence == "last":
        return values[-1]
    if occurrence not in (None, "first"):
        try:
            index = int(occurrence)
        except (TypeError, ValueError):
            index = None
        if index is not None:
            return values[index] if 0 <= index < len(values) else None
    return values[0] if len(values) == 1 else values


def _as_context(payload: Any) -> ExtractionContext:
    """兼容旧调用：非 ExtractionContext 入参包装为上下文。"""
    if isinstance(payload, ExtractionContext):
        return payload
    return ExtractionContext(payload=payload)


def _extract_field(
    context: ExtractionContext, expression: Any
) -> tuple[Any, dict[str, Any] | None]:
    """按单个 extractor 值（字符串或条件规则）求值。"""
    if is_extraction_spec(expression):
        return evaluate_spec(context, expression)
    doc = context.document()
    return extract_value(doc, str(expression)), None


def extract_all(payload: Any, mapping: dict[str, Any] | None) -> dict[str, Any]:
    """按映射抽取白盒字段，未命中的键为 None（兼容旧签名）。"""
    fields, _traces = extract_all_traced(payload, mapping)
    return fields


def extract_all_traced(
    payload: Any, mapping: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按映射抽取白盒字段，并返回条件规则的命中轨迹（供 Trace 排查）。"""
    mapping = mapping or DEFAULT_EXTRACTORS
    context = _as_context(payload)
    fields: dict[str, Any] = {}
    traces: dict[str, Any] = {}
    for field, expression in mapping.items():
        value, trace = _extract_field(context, expression)
        fields[field] = value
        if trace is not None:
            traces[field] = trace
    return fields, traces


def extract_final_answer(payload: Any, mapping: dict[str, Any] | None = None) -> str | None:
    """优先用配置的 final_answer 表达式/规则，再走兜底路径。

    - 字符串表达式：表达式 -> FALLBACK_ANSWER_PATHS -> 流式聚合文本；
    - 条件规则：规则链 -> （legacy_fallback 时 FALLBACK_ANSWER_PATHS）
      -> （fallback_content 时聚合文本，默认开）。
    """
    mapping = mapping or DEFAULT_EXTRACTORS
    context = _as_context(payload)
    spec = mapping.get("final_answer")
    if is_extraction_spec(spec):
        value, _trace = evaluate_spec(context, spec)
        if value not in (None, ""):
            return _as_text(value)
        if spec.get("legacy_fallback"):
            for path in FALLBACK_ANSWER_PATHS:
                value = extract_value(context.document(), path)
                if value not in (None, ""):
                    return _as_text(value)
        if spec.get("fallback_content", True) and context.content:
            return _as_text(context.content)
        return None
    doc = context.document()
    expr = spec
    if expr:
        value = extract_value(doc, str(expr))
        if value not in (None, ""):
            return _as_text(value)
    for path in FALLBACK_ANSWER_PATHS:
        value = extract_value(doc, path)
        if value not in (None, ""):
            return _as_text(value)
    # 流式聚合文本兜底（历史行为由调用方兜底，这里统一收口，语义一致）
    if context.content:
        return _as_text(context.content)
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
