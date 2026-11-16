"""JSONPath 白盒响应提取器。"""

from __future__ import annotations

from typing import Any

from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.exceptions import JsonPathParserError

from billing_qa.core.logging import get_logger

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


def extract_value(payload: Any, expression: str) -> Any:
    """执行单条 JSONPath；无匹配或语法错误返回 None。"""
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
    return values[0] if len(values) == 1 else values


def extract_all(payload: Any, mapping: dict[str, str] | None) -> dict[str, Any]:
    """按映射抽取白盒字段，未命中的键为 None。"""
    mapping = mapping or DEFAULT_EXTRACTORS
    return {field: extract_value(payload, expr) for field, expr in mapping.items()}


def extract_final_answer(payload: Any, mapping: dict[str, str] | None = None) -> str | None:
    """优先用配置的 final_answer 表达式，再走兜底路径。"""
    mapping = mapping or DEFAULT_EXTRACTORS
    expr = mapping.get("final_answer")
    if expr:
        value = extract_value(payload, expr)
        if value not in (None, ""):
            return _as_text(value)
    for path in FALLBACK_ANSWER_PATHS:
        value = extract_value(payload, path)
        if value not in (None, ""):
            return _as_text(value)
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)
