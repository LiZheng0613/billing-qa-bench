"""JWT 鉴权与当前用户加载。

新 Token 使用 ``sub=<user_id>``、``ver=<token_version>``；
旧 Token（sub 为用户名）只对引导管理员保留兼容。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.config import settings
from billing_qa.core.context import CurrentUser
from billing_qa.core.permissions import ALL_PERMISSION_CODES, SYSTEM_ADMIN_CODE
from billing_qa.db.session import get_db
from billing_qa.models.enums import UserStatus
from billing_qa.models.user import (
    SysPermission,
    SysRole,
    SysUser,
    sys_role_permission,
    sys_user_role,
)

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """bcrypt 哈希；调用方应限制密码长度不超过 72 字节。"""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:72], password_hash.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: int,
    username: str,
    token_version: int,
    expires_minutes: int | None = None,
) -> tuple[str, int]:
    """返回 (token, 过期秒数)。"""
    minutes = expires_minutes or settings.jwt_expire_minutes
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
    payload = {
        "sub": str(user_id),
        "username": username,
        "ver": token_version,
        "jti": uuid.uuid4().hex,
        "iat": datetime.now(UTC),
        "exp": expire,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, minutes * 60


def create_legacy_access_token(
    username: str, expires_minutes: int | None = None
) -> tuple[str, int]:
    """引导管理员 / 无表兼容模式使用的旧格式 Token。"""
    minutes = expires_minutes or settings.jwt_expire_minutes
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
    payload = {
        "sub": username,
        "username": username,
        "jti": uuid.uuid4().hex,
        "iat": datetime.now(UTC),
        "exp": expire,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, minutes * 60


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的访问令牌") from exc


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _synthetic_admin(username: str) -> CurrentUser:
    return CurrentUser(
        id=0,
        username=username,
        display_name="管理员",
        roles=frozenset({SYSTEM_ADMIN_CODE}),
        permissions=ALL_PERMISSION_CODES,
        is_admin=True,
    )


async def load_user_auth(
    db: AsyncSession, user_id: int
) -> tuple[frozenset[str], frozenset[str]]:
    role_rows = await db.execute(
        select(SysRole.code)
        .join(sys_user_role, sys_user_role.c.role_id == SysRole.id)
        .where(
            sys_user_role.c.user_id == user_id,
            SysRole.status == "ACTIVE",
        )
    )
    roles = frozenset(role_rows.scalars().all())

    perm_rows = await db.execute(
        select(SysPermission.code)
        .join(sys_role_permission, sys_role_permission.c.permission_id == SysPermission.id)
        .join(SysRole, SysRole.id == sys_role_permission.c.role_id)
        .join(sys_user_role, sys_user_role.c.role_id == SysRole.id)
        .where(
            sys_user_role.c.user_id == user_id,
            SysRole.status == "ACTIVE",
        )
    )
    permissions = frozenset(perm_rows.scalars().all())
    if SYSTEM_ADMIN_CODE in roles:
        permissions = permissions | ALL_PERMISSION_CODES
    return roles, permissions


async def _load_user_from_db(db: AsyncSession, user_id: int, token_version: int | None) -> CurrentUser:
    user = await db.get(SysUser, user_id)
    if user is None:
        raise _unauthorized("账号不存在或已被移除")
    if user.status != UserStatus.ACTIVE.value:
        raise _unauthorized("账号不可用，请联系系统管理员")
    if token_version is None or user.token_version != token_version:
        raise _unauthorized("登录状态已失效，请重新登录")

    roles, permissions = await load_user_auth(db, user.id)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles=roles,
        permissions=permissions,
        is_admin=SYSTEM_ADMIN_CODE in roles,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("未登录或缺少访问令牌")

    payload = decode_access_token(credentials.credentials)
    return await resolve_token_user(payload, db=db)


async def resolve_token_user(
    payload: dict,
    *,
    db: AsyncSession | None = None,
) -> CurrentUser:
    """按解码后的 Token 载荷解析当前用户。

    与 :func:`get_current_user` 同一套校验（账号状态、``token_version``、
    旧 Token 兼容规则），供 WebSocket 等非 HTTP 依赖场景复用——避免 WS
    只解码 JWT 而不校验账号已被禁用 / 角色已回收的长连接漏洞。
    """
    if db is not None:
        return await _resolve_token_user(db, payload)
    from billing_qa.db.session import SessionLocal

    async with SessionLocal() as session:
        return await _resolve_token_user(session, payload)


async def _resolve_token_user(db: AsyncSession, payload: dict) -> CurrentUser:
    subject = str(payload.get("sub") or "")
    if not subject:
        raise _unauthorized("访问令牌缺少主体信息")

    # 新 Token：sub 为 user_id
    if subject.isdigit():
        version = payload.get("ver")
        return await _load_user_from_db(
            db, int(subject), int(version) if version is not None else None
        )

    # 旧 Token / 引导管理员兼容
    username = str(payload.get("username") or subject)
    try:
        row = (
            await db.execute(
                select(SysUser).where(SysUser.username_lower == username.lower())
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        row = None

    if row is not None:
        # 数据库已启用新 Token；旧格式 Token 不允许继续访问，强制重新登录。
        raise _unauthorized("登录状态已失效，请重新登录")

    if username == settings.admin_username:
        return _synthetic_admin(username)

    raise _unauthorized("无效的访问令牌")


async def get_current_operator(
    user: CurrentUser = Depends(get_current_user),
) -> str:
    """兼容壳：现有业务接口继续拿用户名。"""
    return user.username
