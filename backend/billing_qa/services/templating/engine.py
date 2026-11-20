"""可复用的模板表达式引擎（Jinja2 沙箱）。

对外能力：

- ``render_text`` / ``render_structure`` / ``render_prompt``：渲染字符串 / JSON 结构 / Prompt
- ``find_variables``：提取模板引用的变量（不含全局函数）
- ``register_filter`` / ``register_global``：运行期注册自定义过滤器与全局函数
- ``available_filters`` / ``available_globals``：列出可用函数，供前端提示或文档生成

语法与函数清单见 ``docs/TEMPLATE_EXPRESSION_SYNTAX.md``。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import jinja2
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment

from billing_qa.core.exceptions import ValidationError
from billing_qa.services.templating.filters import (
    BUILTIN_FILTERS,
    BUILTIN_GLOBALS,
    to_text,
)

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
FULL_PLACEHOLDER = re.compile(r"^\s*\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}\s*$")
# 表达式片段（非贪婪匹配到最近的一组 }}，用于缺失变量时整段保留原文）
_TEMPLATE_SEGMENT = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_REGISTRY_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_MISSING_PREFIX = "__BTMISSING"


def _build_env() -> SandboxedEnvironment:
    """构建沙箱渲染环境：禁止属性逃逸，按 JSON 口径输出值，不做 HTML 转义。"""
    env = SandboxedEnvironment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
        finalize=to_text,
        keep_trailing_newline=False,
    )
    env.filters.update(BUILTIN_FILTERS)
    env.globals.update(BUILTIN_GLOBALS)
    return env


_ENV = _build_env()


# ---------------------------------------------------------------------- #
# 运行期扩展
# ---------------------------------------------------------------------- #
def register_filter(name: str, func: Callable[..., Any]) -> None:
    """注册自定义过滤器，模板中通过 ``{{ var | name(...) }}`` 调用。"""
    if not _REGISTRY_NAME.match(name):
        raise ValueError(f"过滤器名不合法: {name!r}，需匹配 [A-Za-z_][A-Za-z0-9_]*")
    if not callable(func):
        raise TypeError(f"过滤器 {name!r} 必须是可调用对象")
    _ENV.filters[name] = func


def register_global(name: str, func: Callable[..., Any]) -> None:
    """注册全局函数，模板中通过 ``{{ name(...) }}`` 调用。"""
    if not _REGISTRY_NAME.match(name):
        raise ValueError(f"全局函数名不合法: {name!r}，需匹配 [A-Za-z_][A-Za-z0-9_]*")
    if not callable(func):
        raise TypeError(f"全局函数 {name!r} 必须是可调用对象")
    _ENV.globals[name] = func
    # 全局函数名参与“缺失变量”判定，注册后需要让模板解析缓存失效
    _template_names.cache_clear()


def available_filters() -> list[str]:
    """列出当前可用的全部过滤器（Jinja2 内置 + 平台扩展）。"""
    return sorted(_ENV.filters)


def available_globals() -> list[str]:
    """列出当前可用的全部全局函数。"""
    return sorted(_ENV.globals)


# ---------------------------------------------------------------------- #
# 模板解析与编译（按模板字符串缓存）
# ---------------------------------------------------------------------- #
@lru_cache(maxsize=1024)
def _template_names(template: str) -> frozenset[str]:
    """解析模板引用的变量名，排除 Jinja 全局函数（now / uuid / range 等）。"""
    parsed = _ENV.parse(template)
    return frozenset(meta.find_undeclared_variables(parsed) - set(_ENV.globals))


@lru_cache(maxsize=1024)
def _compile(template: str) -> jinja2.Template:
    return _ENV.from_string(template)


def _known_callables() -> frozenset[str]:
    return frozenset(_ENV.filters) | frozenset(_ENV.globals)


def _ordered_names(template: str, names: frozenset[str] | set[str]) -> list[str]:
    """按模板中的出现顺序返回变量名，解析器无法定位的追加在末尾。"""
    ordered: list[str] = []
    for name in _IDENTIFIER.findall(template):
        if name in names and name not in ordered:
            ordered.append(name)
    for name in sorted(names):
        if name not in ordered:
            ordered.append(name)
    return ordered


def _syntax_message(exc: jinja2.TemplateSyntaxError) -> str:
    detail = exc.message or str(exc)
    line = f"（第 {exc.lineno} 行）" if exc.lineno else ""
    return f"模板表达式语法错误{line}: {detail}"


# ---------------------------------------------------------------------- #
# 渲染
# ---------------------------------------------------------------------- #
def find_variables(template: str) -> list[str]:
    """提取模板中引用的全部变量（不含 Jinja 全局函数）。"""
    try:
        names = _template_names(template)
    except jinja2.TemplateSyntaxError:
        # 语法错误时退回粗粒度识别（表达式片段内的标识符，排除过滤器/全局函数），
        # 保证预览与变量来源标注在模板写错时仍然可用
        names = {
            name
            for segment in _TEMPLATE_SEGMENT.findall(template)
            for name in _IDENTIFIER.findall(segment)
        } - _known_callables()
    return _ordered_names(template, names)


def _protect_missing(template: str, absent: set[str]) -> tuple[str, dict[str, str]]:
    """把引用缺失变量的表达式片段替换成哨兵，渲染后还原为原文。

    这样已知变量正常渲染，缺失变量的表达式保留 ``{{ ... }}`` 原文，
    与历史占位符行为一致（不会被 Jinja 渲染成空串）。
    """
    if not absent:
        return template, {}
    prefix = _MISSING_PREFIX
    while prefix in template:
        prefix += "_"
    originals: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        segment = match.group(0)
        try:
            refs = _template_names(segment)
        except jinja2.TemplateSyntaxError:
            return segment
        if not refs & absent:
            return segment
        token = f"{prefix}{len(originals)}__"
        originals[token] = segment
        return token

    return _TEMPLATE_SEGMENT.sub(_replace, template), originals


def render_text(
    template: str,
    variables: dict[str, Any],
    errors: list[str] | None = None,
) -> tuple[str, list[str]]:
    """渲染字符串，缺失变量保留占位符原文并返回缺失列表。

    ``errors`` 为可选的错误收集通道：表达式语法错误、沙箱拦截等会写入该列表，
    文本本身保持原文返回（调用方据此决定是否中断请求）。
    """
    missing: list[str] = []
    if "{{" not in template and "{%" not in template:
        return template, missing

    try:
        declared = _template_names(template)
    except jinja2.TemplateSyntaxError as exc:
        if errors is not None:
            errors.append(_syntax_message(exc))
        return template, missing

    absent = {name for name in declared if variables.get(name) is None}
    if absent:
        missing = _ordered_names(template, frozenset(absent))
        if "{%" in template:
            # 含语句块时整段保留，避免 if / for 分支在缺变量时被半渲染
            return template, missing

    protected, originals = _protect_missing(template, absent)
    render_variables = {
        name: value for name, value in variables.items() if value is not None
    }
    try:
        rendered = _compile(protected).render(render_variables)
    except jinja2.TemplateSyntaxError as exc:
        if errors is not None:
            errors.append(_syntax_message(exc))
        return template, missing
    except jinja2.TemplateError as exc:
        if errors is not None:
            errors.append(f"模板表达式渲染失败: {exc}")
        return template, missing

    for token, original in originals.items():
        rendered = rendered.replace(token, original)
    return rendered, missing


def render_structure(
    value: Any,
    variables: dict[str, Any],
    missing: list[str] | None = None,
    errors: list[str] | None = None,
) -> Any:
    """递归渲染 dict/list/str；整值占位符返回原始对象。"""
    if missing is None:
        missing = []
    if isinstance(value, str):
        exact = FULL_PLACEHOLDER.match(value)
        if exact:
            name = exact.group(1)
            if variables.get(name) is not None:
                return variables[name]
            if name not in missing:
                missing.append(name)
            return value
        rendered, local_missing = render_text(value, variables, errors)
        for name in local_missing:
            if name not in missing:
                missing.append(name)
        return rendered
    if isinstance(value, dict):
        return {
            key: render_structure(item, variables, missing, errors)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [render_structure(item, variables, missing, errors) for item in value]
    return value


def render_prompt(template: str, variables: dict[str, Any], *, strict: bool = True) -> str:
    """渲染 Prompt，strict 模式下缺失变量或表达式错误直接报错。"""
    errors: list[str] = []
    rendered, missing = render_text(template, variables, errors)
    if strict:
        if errors:
            raise ValidationError("模板表达式错误: " + "; ".join(errors))
        if missing:
            raise ValidationError(f"模板变量缺失: {', '.join(missing)}")
    return rendered
