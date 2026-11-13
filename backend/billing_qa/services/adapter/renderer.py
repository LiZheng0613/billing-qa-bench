"""模板变量渲染：支持 {{var}} 占位符，整值占位符保留原始类型。"""

from __future__ import annotations

import re
from typing import Any

from billing_qa.core.exceptions import ValidationError

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
FULL_PLACEHOLDER = re.compile(r"^\s*\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}\s*$")


def find_variables(template: str) -> list[str]:
    """提取模板中出现的全部占位符。"""
    seen: list[str] = []
    for name in PLACEHOLDER.findall(template):
        if name not in seen:
            seen.append(name)
    return seen


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_text(template: str, variables: dict[str, Any]) -> tuple[str, list[str]]:
    """渲染字符串，缺失变量保留占位符并返回缺失列表。"""
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables and variables[name] is not None:
            return _stringify(variables[name])
        if name not in missing:
            missing.append(name)
        return match.group(0)

    return PLACEHOLDER.sub(_replace, template), missing


def render_structure(value: Any, variables: dict[str, Any], missing: list[str] | None = None) -> Any:
    """递归渲染 dict/list/str；整值占位符返回原始对象。"""
    if missing is None:
        missing = []
    if isinstance(value, str):
        exact = FULL_PLACEHOLDER.match(value)
        if exact:
            name = exact.group(1)
            if name in variables and variables[name] is not None:
                return variables[name]
            if name not in missing:
                missing.append(name)
            return value
        rendered, local_missing = render_text(value, variables)
        for name in local_missing:
            if name not in missing:
                missing.append(name)
        return rendered
    if isinstance(value, dict):
        return {key: render_structure(item, variables, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [render_structure(item, variables, missing) for item in value]
    return value


def render_prompt(template: str, variables: dict[str, Any], *, strict: bool = True) -> str:
    """渲染 Prompt，strict 模式下缺失变量直接报错。"""
    rendered, missing = render_text(template, variables)
    if strict and missing:
        raise ValidationError(f"模板变量缺失: {', '.join(missing)}")
    return rendered


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
