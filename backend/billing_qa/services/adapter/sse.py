"""SSE（text/event-stream）解析器：精确计时首字延迟（TTFT）并聚合文本。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from billing_qa.core.logging import get_logger

logger = get_logger(__name__)
MAX_FRAMES = 300


@dataclass
class SseResult:
    content: str = ""
    ttft_ms: float | None = None
    frames: list[Any] = field(default_factory=list)
    merged_metadata: dict[str, Any] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)


def _deep_merge(base: dict[str, Any], other: dict[str, Any]) -> None:
    for key, value in other.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _extract_delta_text(payload: Any) -> str | None:
    """从单个数据帧中提取增量文本。"""
    if isinstance(payload, str):
        return payload or None
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        for holder in ("delta", "message"):
            node = first.get(holder)
            if isinstance(node, dict):
                content = node.get("content")
                if isinstance(content, str) and content:
                    return content
        if isinstance(first.get("text"), str) and first["text"]:
            return first["text"]
    # 部分问账系统直接把增量放在 data.content / data.text
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("delta", "content", "text"):
            value = data.get(key)
            if isinstance(value, str) and value and not payload.get("type"):
                return value
    return None


def _merge_frame(result: SseResult, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    result.frames.append(payload)
    if len(result.frames) > MAX_FRAMES:
        result.frames = result.frames[-MAX_FRAMES:]
    _deep_merge(result.merged_metadata, payload)


async def consume_sse(response: Any, started_at: float) -> SseResult:
    """消费 httpx 流式响应。

    started_at 为发起请求的 time.perf_counter()，用于计算 TTFT。
    """
    result = SseResult()
    data_buffer: list[str] = []
    event_name: str | None = None
    content_parts: list[str] = []

    def dispatch() -> None:
        nonlocal data_buffer, event_name
        if not data_buffer:
            event_name = None
            return
        data_str = "\n".join(data_buffer).strip()
        data_buffer = []
        current_event = event_name
        event_name = None
        if not data_str or data_str == "[DONE]":
            return
        payload: Any
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            payload = data_str
        if not isinstance(payload, dict):
            text = payload if isinstance(payload, str) else str(payload)
            if text and current_event not in {"metadata", "meta"}:
                _append_content(result, content_parts, text, started_at)
            result.raw_lines.append(data_str)
            return
        result.raw_lines.append(data_str)
        _merge_frame(result, payload)
        delta = _extract_delta_text(payload)
        if delta and current_event not in {"metadata", "meta"}:
            _append_content(result, content_parts, delta, started_at)

    async for line in response.aiter_lines():
        if line == "":
            dispatch()
            continue
        if line.startswith(":"):
            # 心跳/注释行
            continue
        if line.startswith("data:"):
            data_buffer.append(line[5:].lstrip())
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith(("id:", "retry:")):
            continue
        else:
            # 容错：部分服务端直接返回裸 JSON 行
            data_buffer.append(line.strip())
    dispatch()
    result.content = "".join(content_parts).strip()
    return result


def _append_content(result: SseResult, parts: list[str], text: str, started_at: float) -> None:
    if result.ttft_ms is None and text.strip():
        result.ttft_ms = round((time.perf_counter() - started_at) * 1000, 2)
    parts.append(text)
