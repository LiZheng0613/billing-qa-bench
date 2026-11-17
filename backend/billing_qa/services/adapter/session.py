"""多轮会话绑定策略：模式解析与初始会话值生成。

会话模式的优先级：
1. FIXED —— 用例 ``request_variables`` 显式提供 ``session_id``（最高优先）
2. 用例 ``session_mode`` 非 INHERIT 时使用用例配置
3. 否则跟随环境 ``session_mode``（默认 SERVER_RETURNED）
"""

from __future__ import annotations

import uuid
from typing import Any

SERVER_RETURNED = "SERVER_RETURNED"
CLIENT_GENERATED = "CLIENT_GENERATED"
INHERIT = "INHERIT"
FIXED = "FIXED"

VALID_MODES: frozenset[str] = frozenset({SERVER_RETURNED, CLIENT_GENERATED})
VALID_CASE_MODES: frozenset[str] = frozenset({INHERIT, SERVER_RETURNED, CLIENT_GENERATED})


def resolve_session_mode(
    case_mode: str | None,
    env_mode: str | None,
    case_vars: dict[str, Any] | None = None,
) -> str:
    """解析本次执行最终生效的会话模式。"""
    variables = case_vars or {}
    fixed = variables.get("session_id")
    if fixed not in (None, ""):
        return FIXED
    mode = str(case_mode or INHERIT).upper()
    if mode != INHERIT and mode in VALID_MODES:
        return mode
    env = str(env_mode or SERVER_RETURNED).upper()
    return env if env in VALID_MODES else SERVER_RETURNED


def init_session_id(
    mode: str, case_vars: dict[str, Any] | None, execution_id: int | None
) -> str | None:
    """按模式生成首轮会话值；SERVER_RETURNED 首轮为空，由响应回填。"""
    variables = case_vars or {}
    if mode == FIXED:
        value = variables.get("session_id")
        return str(value) if value not in (None, "") else None
    if mode == CLIENT_GENERATED:
        prefix = f"exec-{execution_id}" if execution_id else "preview"
        return f"{prefix}-{uuid.uuid4().hex[:8]}"
    return None


def session_updates_from_response(mode: str, current: str | None, returned: str | None) -> str | None:
    """仅服务端返回模式采纳响应中的会话值，其余模式保持执行期生成值。"""
    if mode == SERVER_RETURNED:
        return returned or current
    return current
