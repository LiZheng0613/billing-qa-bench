"""审计日志接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_permission
from billing_qa.db.session import get_db
from billing_qa.schemas.audit import AuditLogOut
from billing_qa.schemas.common import Page
from billing_qa.services.auth.service import list_audit_logs

router = APIRouter(
    prefix="/audit-logs",
    tags=["审计日志"],
    dependencies=[Depends(require_permission("audit:read"))],
)


@router.get("", response_model=Page[AuditLogOut], summary="审计日志列表")
async def list_audit_logs_api(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    actor_id: int | None = Query(default=None),
    action: str | None = Query(default=None, max_length=64),
    resource: str | None = Query(default=None, max_length=64),
    success: bool | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Page[AuditLogOut]:
    rows, total = await list_audit_logs(
        db,
        page=page,
        page_size=page_size,
        actor_id=actor_id,
        action=action or None,
        resource=resource or None,
        success=success,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(
        items=[AuditLogOut.model_validate(row, from_attributes=True) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
