"""业务口径字典：口径裁定与基准来源维护。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_module_access
from billing_qa.core.exceptions import ConflictError, NotFoundError
from billing_qa.core.search import keyword_condition
from billing_qa.db.session import get_db
from billing_qa.models.candidate import Caliber
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.ops import CaliberCreate, CaliberOut, CaliberUpdate

router = APIRouter(
    prefix="/calibers",
    tags=["业务口径"],
    dependencies=[Depends(require_module_access("caliber:read", ("caliber:write",)))],
)


async def _get_caliber(db: AsyncSession, caliber_id: int) -> Caliber:
    caliber = await db.get(Caliber, caliber_id)
    if caliber is None:
        raise NotFoundError("业务口径不存在")
    return caliber


@router.get("", response_model=Page[CaliberOut], summary="口径列表")
async def list_calibers(
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CaliberOut]:
    conditions = []
    keyword_filter = keyword_condition((Caliber.key, Caliber.name), keyword)
    if keyword_filter is not None:
        conditions.append(keyword_filter)
    total = (
        await db.execute(select(func.count()).select_from(Caliber).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(Caliber)
            .where(*conditions)
            .order_by(Caliber.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[CaliberOut](
        items=[CaliberOut.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=CaliberOut, summary="新增口径")
async def create_caliber(
    payload: CaliberCreate, db: AsyncSession = Depends(get_db)
) -> CaliberOut:
    exists = (
        await db.execute(select(Caliber.id).where(Caliber.key == payload.key))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"口径键 {payload.key} 已存在")
    caliber = Caliber(**payload.model_dump())
    db.add(caliber)
    await db.commit()
    await db.refresh(caliber)
    return CaliberOut.model_validate(caliber)


@router.get("/{caliber_id}", response_model=CaliberOut, summary="口径详情")
async def get_caliber(caliber_id: int, db: AsyncSession = Depends(get_db)) -> CaliberOut:
    return CaliberOut.model_validate(await _get_caliber(db, caliber_id))


@router.put("/{caliber_id}", response_model=CaliberOut, summary="编辑口径")
async def update_caliber(
    caliber_id: int, payload: CaliberUpdate, db: AsyncSession = Depends(get_db)
) -> CaliberOut:
    caliber = await _get_caliber(db, caliber_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(caliber, key, value)
    await db.commit()
    await db.refresh(caliber)
    return CaliberOut.model_validate(caliber)


@router.delete("/{caliber_id}", response_model=MessageResponse, summary="删除口径")
async def delete_caliber(
    caliber_id: int, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    caliber = await _get_caliber(db, caliber_id)
    key = caliber.key
    await db.delete(caliber)
    await db.commit()
    return MessageResponse(message=f"口径 {key} 已删除")
