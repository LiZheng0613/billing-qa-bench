"""请求级当前用户上下文。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    is_admin: bool
