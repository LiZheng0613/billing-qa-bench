"""认证接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.context import CurrentUser
from billing_qa.core.security import (
    create_access_token,
    create_legacy_access_token,
    get_current_user,
    load_user_auth,
)
from billing_qa.db.session import get_db
from billing_qa.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserInfo,
    UserProfile,
)
from billing_qa.schemas.common import MessageResponse
from billing_qa.services.auth.service import add_audit, authenticate, change_password

router = APIRouter(prefix="/auth", tags=["认证"])


def _request_meta(request: Request) -> tuple[str | None, str | None, str]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent"), request.url.path


def _profile(
    user_id: int,
    username: str,
    display_name: str,
    roles: frozenset[str] | list[str] | set[str],
    permissions: frozenset[str] | list[str] | set[str],
    must_change_password: bool,
) -> UserProfile:
    role_list = sorted(roles)
    return UserProfile(
        id=user_id,
        username=username,
        display_name=display_name,
        roles=role_list,
        permissions=sorted(permissions),
        must_change_password=must_change_password,
    )


@router.post("/login", response_model=TokenResponse, summary="登录")
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    ip, user_agent, path = _request_meta(request)
    result = await authenticate(
        db,
        payload.username,
        payload.password,
        ip=ip,
        user_agent=user_agent,
        path=path,
    )
    if result.legacy:
        token, expires_in = create_legacy_access_token(result.username)
    else:
        token, expires_in = create_access_token(
            result.id, result.username, result.token_version
        )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        username=result.username,
        user=_profile(
            result.id,
            result.username,
            result.display_name,
            result.roles,
            result.permissions,
            result.must_change_password,
        ),
    )


@router.get("/me", response_model=UserInfo, summary="当前登录用户")
async def me(user: CurrentUser = Depends(get_current_user)) -> UserInfo:
    return UserInfo(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=sorted(user.roles),
        permissions=sorted(user.permissions),
        must_change_password=False,
        role=min(user.roles) if user.roles else "",
    )


@router.post("/change-password", response_model=TokenResponse, summary="修改密码")
async def update_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    _ip, _user_agent, path = _request_meta(request)
    updated = await change_password(
        db,
        user_id=user.id,
        old_password=payload.old_password,
        new_password=payload.new_password,
        actor=user,
        path=path,
    )
    roles, permissions = await load_user_auth(db, updated.id)
    token, expires_in = create_access_token(
        updated.id, updated.username, updated.token_version
    )
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        username=updated.username,
        user=_profile(
            updated.id,
            updated.username,
            updated.display_name,
            roles,
            permissions,
            updated.must_change_password,
        ),
    )


@router.post("/logout", response_model=MessageResponse, summary="退出登录")
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    ip, user_agent, path = _request_meta(request)
    add_audit(
        db,
        actor=user,
        actor_username=user.username,
        action="auth.logout",
        resource="auth",
        ip=ip,
        user_agent=user_agent,
        method="POST",
        path=path,
        status_code=200,
    )
    await db.commit()
    return MessageResponse(message="已退出登录")
