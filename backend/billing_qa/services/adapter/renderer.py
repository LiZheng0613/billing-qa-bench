"""模板渲染（向后兼容层）。

渲染能力已抽取到可复用包 :mod:`billing_qa.services.templating`；
本模块保留历史导入路径，并继续提供适配器领域的对话历史格式化。
新代码请直接从 ``billing_qa.services.templating`` 导入。
"""

from __future__ import annotations

from typing import Any

from billing_qa.services.templating import (
    FULL_PLACEHOLDER,
    PLACEHOLDER,
    available_filters,
    available_globals,
    find_variables,
    register_filter,
    register_global,
    render_prompt,
    render_structure,
    render_text,
)

__all__ = [
    "FULL_PLACEHOLDER",
    "PLACEHOLDER",
    "available_filters",
    "available_globals",
    "find_variables",
    "format_history",
    "register_filter",
    "register_global",
    "render_prompt",
    "render_structure",
    "render_text",
]


def format_history(history: list[dict[str, str]], style: str = "text") -> Any:
    """把对话历史转成 Prompt 文本或 OpenAI messages。"""
    if style == "messages":
        return [
            {"role": "assistant" if item.get("role") == "assistant" else "user",
             "content": item.get("content", "")}
            for item in history
        ]
    lines: list[str] = []
    for item in history:
        role = "用户" if item.get("role") == "user" else "客服"
        lines.append(f"{role}: {item.get('content', '')}")
    return "\n".join(lines) if lines else "（暂无对话）"
