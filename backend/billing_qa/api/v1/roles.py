"""角色与权限接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_permission
from billing_qa.core.context import CurrentUser
from billing_qa.db.session import get_db
from billing_qa.models.user import SysRole
from billing_qa.schemas.role import (
    PermissionOut,
    RoleCreate,
    RoleDetail,
    RoleOut,
    RolePermissionsPayload,
    RoleUpdate,
)
from billing_qa.services.auth.service import (
    create_role,
    delete_role,
    get_role_detail,
    list_permissions,
    list_roles,
    set_role_permissions,
    update_role,
)

router = APIRouter(
    prefix="/roles",
    tags=["角色与权限"],
    dependencies=[Depends(require_permission("role:read"))],
)


def _role_out(role: SysRole, user_count: int, permission_count: int) -> RoleOut:
    return RoleOut(
        id=role.id,
        code=role.code,
        name=role.name,
        description=role.description,
        data_scope=role.data_scope,
        is_system=role.is_system,
        status=role.status,
        user_count=user_count,
        permission_count=permission_count,
    )


@router.get("", response_model=list[RoleOut], summary="角色列表")
async def list_roles_api(db: AsyncSession = Depends(get_db)) -> list[RoleOut]:
    rows = await list_roles(db)
    return [_role_out(role, users, permissions) for role, users, permissions in rows]


@router.get("/{role_id}", response_model=RoleDetail, summary="角色详情")
async def get_role_api(
    role_id: int, db: AsyncSession = Depends(get_db)
) -> RoleDetail:
    role, user_count, permission_count, codes = await get_role_detail(db, role_id)
    return RoleDetail(
        **_role_out(role, user_count, permission_count).model_dump(),
        permission_codes=codes,
    )


@router.post("", response_model=RoleDetail, status_code=201, summary="创建角色")
async def create_role_api(
    payload: RoleCreate,
    request: Request,
    actor: CurrentUser = Depends(require_permission("role:write")),
    db: AsyncSession = Depends(get_db),
) -> RoleDetail:
    role = await create_role(
        db,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        data_scope=payload.data_scope,
        permission_codes=payload.permission_codes,
        actor=actor,
        path=request.url.path,
    )
    detail = await get_role_detail(db, role.id)
    return RoleDetail(
        **_role_out(detail[0], detail[1], detail[2]).model_dump(),
        permission_codes=detail[3],
    )


@router.patch("/{role_id}", response_model=RoleDetail, summary="更新角色")
async def update_role_api(
    role_id: int,
    payload: RoleUpdate,
    request: Request,
    actor: CurrentUser = Depends(require_permission("role:write")),
    db: AsyncSession = Depends(get_db),
) -> RoleDetail:
    await update_role(
        db,
        role_id=role_id,
        name=payload.name,
        description=payload.description,
        data_scope=payload.data_scope,
        status=payload.status,
        actor=actor,
        path=request.url.path,
    )
    detail = await get_role_detail(db, role_id)
    return RoleDetail(
        **_role_out(detail[0], detail[1], detail[2]).model_dump(),
        permission_codes=detail[3],
    )


@router.put("/{role_id}/permissions", response_model=RoleDetail, summary="设置角色权限")
async def set_role_permissions_api(
    role_id: int,
    payload: RolePermissionsPayload,
    request: Request,
    actor: CurrentUser = Depends(require_permission("role:write")),
    db: AsyncSession = Depends(get_db),
) -> RoleDetail:
    await set_role_permissions(
        db,
        role_id=role_id,
        permission_codes=payload.permission_codes,
        actor=actor,
        path=request.url.path,
    )
    detail = await get_role_detail(db, role_id)
    return RoleDetail(
        **_role_out(detail[0], detail[1], detail[2]).model_dump(),
        permission_codes=detail[3],
    )


@router.delete("/{role_id}", status_code=204, summary="删除角色")
async def delete_role_api(
    role_id: int,
    request: Request,
    actor: CurrentUser = Depends(require_permission("role:write")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await delete_role(db, role_id=role_id, actor=actor, path=request.url.path)


@router.get("/permissions/catalog", response_model=list[PermissionOut], summary="权限目录")
async def permission_catalog_api(
    db: AsyncSession = Depends(get_db),
) -> list[PermissionOut]:
    rows = await list_permissions(db)
    return [PermissionOut.model_validate(row, from_attributes=True) for row in rows]
