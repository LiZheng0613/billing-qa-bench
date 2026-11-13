"""认证接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from billing_qa.core.exceptions import AppError
from billing_qa.core.security import (
    create_access_token,
    get_current_operator,
    verify_credentials,
)
from billing_qa.schemas.auth import LoginRequest, TokenResponse, UserInfo

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", response_model=TokenResponse, summary="管理员登录")
async def login(payload: LoginRequest) -> TokenResponse:
    if not verify_credentials(payload.username, payload.password):
        raise AppError("用户名或密码错误", code="AUTH_FAILED", status_code=401)
    token, expires_in = create_access_token(payload.username)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        username=payload.username,
    )


@router.get("/me", response_model=UserInfo, summary="当前登录用户")
async def me(operator: str = Depends(get_current_operator)) -> UserInfo:
    return UserInfo(username=operator)
