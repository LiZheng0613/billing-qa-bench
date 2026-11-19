"""用户管理接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_permission
from billing_qa.core.context import CurrentUser
from billing_qa.db.session import get_db
from billing_qa.models.user import SysRole, SysUser
from billing_qa.schemas.common import Page
from billing_qa.schemas.user import (
    ResetPasswordOut,
    RoleAssignment,
    RoleBrief,
    UserCreate,
    UserDetail,
    UserOut,
    UserUpdate,
)
from billing_qa.services.auth.service import (
    create_user,
    get_user_detail,
    list_users,
    reset_password,
    set_user_roles,
    unlock_user,
    update_user,
)

router = APIRouter(
    prefix="/users",
    tags=["用户管理"],
    dependencies=[Depends(require_permission("user:read"))],
)


def _role_brief(role: SysRole) -> RoleBrief:
    return RoleBrief(id=role.id, code=role.code, name=role.name)


def _user_out(user: SysUser, roles: list[SysRole], detail: bool = False) -> UserOut:
    payload = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
        "roles": [_role_brief(role) for role in roles],
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "must_change_password": user.must_change_password,
    }
    if detail:
        return UserDetail(
            **payload,
            last_login_ip=user.last_login_ip,
            created_by=user.created_by,
            updated_at=user.updated_at,
        )
    return UserOut(**payload)


def _request_meta(request: Request) -> tuple[str | None, str | None, str]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent"), request.url.path


@router.get("", response_model=Page[UserOut], summary="用户列表")
async def list_users_api(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None),
    role_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Page[UserOut]:
    rows, total = await list_users(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword or None,
        status=status or None,
        role_id=role_id,
    )
    return Page(
        items=[_user_out(user, roles) for user, roles in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserDetail, status_code=201, summary="创建用户")
async def create_user_api(
    payload: UserCreate,
    request: Request,
    actor: CurrentUser = Depends(require_permission("user:write")),
    db: AsyncSession = Depends(get_db),
) -> UserDetail:
    ip, user_agent, path = _request_meta(request)
    user, roles = await create_user(
        db,
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        phone=payload.phone,
        password=payload.password,
        role_ids=payload.role_ids,
        actor=actor,
        ip=ip,
        user_agent=user_agent,
        path=path,
    )
    return _user_out(user, roles, detail=True)


@router.get("/{user_id}", response_model=UserDetail, summary="用户详情")
async def get_user_api(
    user_id: int,
    db: AsyncSession = Depends(get_db),
) -> UserDetail:
    user, roles = await get_user_detail(db, user_id)
    return _user_out(user, roles, detail=True)


@router.patch("/{user_id}", response_model=UserDetail, summary="更新用户")
async def update_user_api(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    actor: CurrentUser = Depends(require_permission("user:write")),
    db: AsyncSession = Depends(get_db),
) -> UserDetail:
    ip, user_agent, path = _request_meta(request)
    user = await update_user(
        db,
        user_id=user_id,
        display_name=payload.display_name,
        email=payload.email,
        phone=payload.phone,
        status=payload.status,
        actor=actor,
        ip=ip,
        user_agent=user_agent,
        path=path,
    )
    roles = await _roles_for_user(db, user.id)
    return _user_out(user, roles, detail=True)


@router.put("/{user_id}/roles", response_model=UserDetail, summary="分配角色")
async def set_roles_api(
    user_id: int,
    payload: RoleAssignment,
    request: Request,
    actor: CurrentUser = Depends(require_permission("role:assign")),
    db: AsyncSession = Depends(get_db),
) -> UserDetail:
    ip, user_agent, path = _request_meta(request)
    roles = await set_user_roles(
        db,
        user_id=user_id,
        role_ids=payload.role_ids,
        actor=actor,
        ip=ip,
        user_agent=user_agent,
        path=path,
    )
    user, _ = await get_user_detail(db, user_id)
    return _user_out(user, roles, detail=True)


@router.post(
    "/{user_id}/reset-password",
    response_model=ResetPasswordOut,
    summary="重置密码",
)
async def reset_password_api(
    user_id: int,
    request: Request,
    actor: CurrentUser = Depends(require_permission("user:reset_password")),
    db: AsyncSession = Depends(get_db),
) -> ResetPasswordOut:
    ip, user_agent, path = _request_meta(request)
    temp = await reset_password(
        db,
        user_id=user_id,
        actor=actor,
        ip=ip,
        user_agent=user_agent,
        path=path,
    )
    return ResetPasswordOut(temp_password=temp)


@router.post("/{user_id}/unlock", response_model=UserDetail, summary="解锁账号")
async def unlock_user_api(
    user_id: int,
    request: Request,
    actor: CurrentUser = Depends(require_permission("user:write")),
    db: AsyncSession = Depends(get_db),
) -> UserDetail:
    ip, user_agent, path = _request_meta(request)
    user = await unlock_user(
        db,
        user_id=user_id,
        actor=actor,
        ip=ip,
        user_agent=user_agent,
        path=path,
    )
    roles = await _roles_for_user(db, user.id)
    return _user_out(user, roles, detail=True)


async def _roles_for_user(db: AsyncSession, user_id: int) -> list[SysRole]:
    from billing_qa.services.auth.service import roles_for_users

    mapping = await roles_for_users(db, [user_id])
    return mapping.get(user_id, [])
