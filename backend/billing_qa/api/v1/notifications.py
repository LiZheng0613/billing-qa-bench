"""统一通知接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_permission
from billing_qa.db.session import get_db
from billing_qa.schemas.notification import NotificationOut
from billing_qa.services.notifications import list_notifications

router = APIRouter(
    prefix="/notifications",
    tags=["通知"],
    dependencies=[Depends(require_permission("audit:read"))],
)


@router.get("", response_model=list[NotificationOut], summary="统一通知列表")
async def list_notifications_api(
    limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    return await list_notifications(db, limit=limit)
