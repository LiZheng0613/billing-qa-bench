"""审计日志 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    created_at: datetime
    actor_id: int | None = None
    actor_username: str
    action: str
    resource: str
    resource_id: str | None = None
    method: str | None = None
    path: str | None = None
    ip: str | None = None
    status_code: int | None = None
    success: bool = True
    detail: dict[str, Any] | None = None
