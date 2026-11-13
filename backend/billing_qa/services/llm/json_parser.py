"""LLM 输出 JSON 解析：兼容 Markdown 代码块与前后噪声。"""

from __future__ import annotations

import json
import re
from typing import Any

from billing_qa.core.exceptions import ValidationError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _try_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出中稳健解析 JSON 对象。"""
    if text is None:
        raise ValidationError("模型输出为空")
    raw = text.strip()
    if not raw:
        raise ValidationError("模型输出为空")

    fenced = _FENCE.search(raw)
    if fenced:
        candidate = fenced.group(1).strip()
        loaded = _try_loads(candidate)
        if isinstance(loaded, dict):
            return loaded
        if isinstance(loaded, list) and loaded and isinstance(loaded[0], dict):
            return loaded[0]

    loaded = _try_loads(raw)
    if isinstance(loaded, dict):
        return loaded

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValidationError(f"模型输出无法解析为 JSON 对象: {raw[:300]}")


def parse_structured(text: str, required_keys: list[str]) -> dict[str, Any]:
    """解析并校验必需字段。"""
    data = parse_json_object(text)
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise ValidationError(f"模型输出缺少字段: {', '.join(missing)}")
    return data
