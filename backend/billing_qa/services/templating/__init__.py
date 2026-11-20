"""模板表达式能力包：可复用的 Jinja2 沙箱渲染引擎。

新代码请直接从这里导入：

    from billing_qa.services.templating import render_structure

扩展函数：

    from billing_qa.services.templating import register_filter

    register_filter("admin_user", lambda code: f"admin_{str(code).replace(' ', '')}")
    # 模板中：{{ custCode | admin_user }}
"""

from billing_qa.services.templating.engine import (
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
    "register_filter",
    "register_global",
    "render_prompt",
    "render_structure",
    "render_text",
]
