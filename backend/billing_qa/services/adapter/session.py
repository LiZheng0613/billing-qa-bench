"""多轮会话绑定策略：会话方式由执行环境统一决定，用例不再单独配置。

环境 ``session_mode`` 二选一（``SERVER_RETURNED`` / ``CLIENT_GENERATED``），
用例执行时直接读取该配置，不再支持用例级覆盖或固定会话值。
"""

from __future__ import annotations

import uuid

SERVER_RETURNED = "SERVER_RETURNED"
CLIENT_GENERATED = "CLIENT_GENERATED"

VALID_MODES: frozenset[str] = frozenset({SERVER_RETURNED, CLIENT_GENERATED})


def resolve_session_mode(env_mode: str | None) -> str:
    """返回环境配置的会话方式；非法或缺失时回退服务端返回模式。"""
    mode = str(env_mode or SERVER_RETURNED).upper()
    return mode if mode in VALID_MODES else SERVER_RETURNED


def init_session_id(mode: str, execution_id: int | None) -> str | None:
    """按环境会话方式生成首轮会话值；SERVER_RETURNED 首轮为空，由响应回填。"""
    if mode == CLIENT_GENERATED:
        prefix = f"exec-{execution_id}" if execution_id else "preview"
        return f"{prefix}-{uuid.uuid4().hex[:8]}"
    return None


def session_updates_from_response(mode: str, current: str | None, returned: str | None) -> str | None:
    """仅服务端返回模式采纳响应中的会话值，其余模式保持执行期生成值。"""
    if mode == SERVER_RETURNED:
        return returned or current
    return current
