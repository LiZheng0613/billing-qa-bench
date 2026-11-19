"""统一通知 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: int
    title: str
    message: str
    level: Literal["info", "success", "warning", "error"] = "info"
    actor: str | None = None
    resource: str
    resource_id: str | None = None
    created_at: datetime
