"""认证、用户、角色与审计的业务实现。"""

from __future__ import annotations

import logging
import re
import secrets
import string
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.config import settings
from billing_qa.core.context import CurrentUser
from billing_qa.core.exceptions import AppError, ConflictError, NotFoundError, ValidationError
from billing_qa.core.permissions import (
    ALL_PERMISSION_CODES,
    PERMISSION_BY_CODE,
    SYSTEM_ADMIN_CODE,
)
from billing_qa.core.search import keyword_condition
from billing_qa.core.security import hash_password, load_user_auth, verify_password
from billing_qa.models.enums import UserStatus
from billing_qa.models.user import (
    SysAuditLog,
    SysPermission,
    SysRole,
    SysUser,
    sys_role_permission,
    sys_user_role,
)
from billing_qa.services.auth.seed import sync_auth_seed

logger = logging.getLogger(__name__)

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
_ROLE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


@dataclass(slots=True)
class AuthResult:
    id: int
    username: str
    display_name: str
    token_version: int
    roles: frozenset[str]
    permissions: frozenset[str]
    must_change_password: bool
    legacy: bool = False


def _now() -> datetime:
    return datetime.now()


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValidationError("密码至少需要 8 位")
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise ValidationError("密码必须同时包含字母和数字")


def _generate_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    value = "".join(secrets.choice(alphabet) for _ in range(14))
    # 保证字母和数字同时存在
    return value[:12] + "A1"


def add_audit(
    db: AsyncSession,
    *,
    actor: CurrentUser | None,
    actor_username: str | None,
    action: str,
    resource: str,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    method: str | None = None,
    path: str | None = None,
    success: bool = True,
    status_code: int | None = None,
) -> None:
    db.add(
        SysAuditLog(
            actor_id=actor.id if actor and actor.id else None,
            actor_username=actor_username or (actor.username if actor else "unknown"),
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            method=method,
            path=path,
            ip=ip,
            user_agent=user_agent,
            status_code=status_code,
            success=success,
            detail=detail,
            created_at=_now(),
        )
    )


async def authenticate(
    db: AsyncSession,
    username: str,
    password: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> AuthResult:
    normalized = _normalize_username(username)
    if not normalized:
        raise AppError("用户名或密码错误", code="AUTH_FAILED", status_code=401)

    try:
        user = (
            await db.execute(select(SysUser).where(SysUser.username_lower == normalized))
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.warning("认证查询失败，尝试引导管理员兼容: %s", exc)
        if username.strip() == settings.admin_username and password == settings.admin_password:
            return AuthResult(
                id=0,
                username=settings.admin_username,
                display_name="管理员",
                token_version=0,
                roles=frozenset({SYSTEM_ADMIN_CODE}),
                permissions=ALL_PERMISSION_CODES,
                must_change_password=False,
                legacy=True,
            )
        raise AppError("用户名或密码错误", code="AUTH_FAILED", status_code=401) from exc

    if user is None and username.strip() == settings.admin_username and password == settings.admin_password:
        try:
            await sync_auth_seed(db)
            user = (
                await db.execute(select(SysUser).where(SysUser.username_lower == normalized))
            ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.warning("引导管理员写入失败，退回环境变量登录: %s", exc)
            return AuthResult(
                id=0,
                username=settings.admin_username,
                display_name="管理员",
                token_version=0,
                roles=frozenset({SYSTEM_ADMIN_CODE}),
                permissions=ALL_PERMISSION_CODES,
                must_change_password=False,
                legacy=True,
            )

    if user is None:
        add_audit(
            db,
            actor=None,
            actor_username=username,
            action="auth.login_failed",
            resource="auth",
            success=False,
            ip=ip,
            user_agent=user_agent,
            method="POST",
            path=path,
            status_code=401,
        )
        await db.commit()
        raise AppError("用户名或密码错误", code="AUTH_FAILED", status_code=401)

    if user.locked_until and user.locked_until > _now():
        add_audit(
            db,
            actor=None,
            actor_username=user.username,
            action="auth.login_failed",
            resource="auth",
            detail={"reason": "locked"},
            success=False,
            ip=ip,
            user_agent=user_agent,
            method="POST",
            path=path,
            status_code=401,
        )
        await db.commit()
        raise AppError("账号暂时锁定，请稍后再试", code="ACCOUNT_LOCKED", status_code=401)

    if user.status != UserStatus.ACTIVE.value:
        add_audit(
            db,
            actor=None,
            actor_username=user.username,
            action="auth.login_failed",
            resource="auth",
            detail={"reason": user.status},
            success=False,
            ip=ip,
            user_agent=user_agent,
            method="POST",
            path=path,
            status_code=401,
        )
        await db.commit()
        raise AppError("账号不可用，请联系系统管理员", code="ACCOUNT_DISABLED", status_code=401)

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = _now() + timedelta(minutes=LOCK_MINUTES)
            user.status = UserStatus.LOCKED.value
        add_audit(
            db,
            actor=None,
            actor_username=user.username,
            action="auth.login_failed",
            resource="auth",
            detail={"failed_count": user.failed_login_count},
            success=False,
            ip=ip,
            user_agent=user_agent,
            method="POST",
            path=path,
            status_code=401,
        )
        await db.commit()
        raise AppError("用户名或密码错误", code="AUTH_FAILED", status_code=401)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    user.last_login_ip = ip
    roles, permissions = await load_user_auth(db, user.id)
    add_audit(
        db,
        actor=None,
        actor_username=user.username,
        action="auth.login",
        resource="auth",
        success=True,
        ip=ip,
        user_agent=user_agent,
        method="POST",
        path=path,
        status_code=200,
    )
    await db.commit()
    return AuthResult(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        token_version=user.token_version,
        roles=roles,
        permissions=permissions,
        must_change_password=user.must_change_password,
    )


async def get_user(db: AsyncSession, user_id: int) -> SysUser:
    user = await db.get(SysUser, user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    return user


async def roles_for_users(
    db: AsyncSession, user_ids: list[int]
) -> dict[int, list[SysRole]]:
    if not user_ids:
        return {}
    rows = await db.execute(
        select(sys_user_role.c.user_id, SysRole)
        .join(SysRole, SysRole.id == sys_user_role.c.role_id)
        .where(sys_user_role.c.user_id.in_(user_ids))
        .order_by(SysRole.id)
    )
    mapping: dict[int, list[SysRole]] = defaultdict(list)
    for user_id, role in rows.all():
        mapping[user_id].append(role)
    return mapping


async def list_users(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    status: str | None = None,
    role_id: int | None = None,
) -> tuple[list[tuple[SysUser, list[SysRole]]], int]:
    filters = []
    keyword_filter = keyword_condition(
        (SysUser.username, SysUser.display_name, SysUser.email), keyword
    )
    if keyword_filter is not None:
        filters.append(keyword_filter)
    if status:
        filters.append(SysUser.status == status)

    stmt = select(SysUser)
    count_stmt = select(func.count(func.distinct(SysUser.id))).select_from(SysUser)
    if role_id is not None:
        stmt = stmt.join(sys_user_role, sys_user_role.c.user_id == SysUser.id).where(
            sys_user_role.c.role_id == role_id
        )
        count_stmt = count_stmt.join(
            sys_user_role, sys_user_role.c.user_id == SysUser.id
        ).where(sys_user_role.c.role_id == role_id)
    for item in filters:
        stmt = stmt.where(item)
        count_stmt = count_stmt.where(item)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(SysUser.id).offset((page - 1) * page_size).limit(page_size)
    users = (await db.execute(stmt)).scalars().all()
    role_map = await roles_for_users(db, [u.id for u in users])
    return [(u, role_map.get(u.id, [])) for u in users], total


async def get_user_detail(
    db: AsyncSession, user_id: int
) -> tuple[SysUser, list[SysRole]]:
    user = await get_user(db, user_id)
    role_map = await roles_for_users(db, [user_id])
    return user, role_map.get(user_id, [])


async def _validate_role_ids(db: AsyncSession, role_ids: list[int]) -> list[SysRole]:
    if not role_ids:
        raise ValidationError("至少需要分配一个角色")
    rows = await db.execute(select(SysRole).where(SysRole.id.in_(role_ids)))
    roles = list(rows.scalars().all())
    if len({r.id for r in roles}) != len(set(role_ids)):
        raise ValidationError("包含不存在的角色")
    return roles


async def _viewer_role(db: AsyncSession) -> SysRole:
    role = (
        await db.execute(select(SysRole).where(SysRole.code == "VIEWER"))
    ).scalar_one_or_none()
    if role is None:
        raise ValidationError("默认只读角色不存在，请先初始化权限数据")
    return role


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    display_name: str,
    email: str | None,
    phone: str | None,
    password: str,
    role_ids: list[int],
    actor: CurrentUser | None,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> tuple[SysUser, list[SysRole]]:
    normalized = _normalize_username(username)
    if not _USERNAME_RE.match(username.strip()):
        raise ValidationError("用户名只能包含字母、数字、下划线、点和短横线，长度 3–32")
    exists = (
        await db.execute(select(SysUser).where(SysUser.username_lower == normalized))
    ).scalar_one_or_none()
    if exists is not None:
        raise ConflictError("用户名已存在")
    _validate_password(password)
    roles = (
        await _validate_role_ids(db, role_ids)
        if role_ids
        else [await _viewer_role(db)]
    )
    user = SysUser(
        username=username.strip(),
        username_lower=normalized,
        display_name=display_name.strip(),
        email=email,
        phone=phone,
        password_hash=hash_password(password),
        status=UserStatus.ACTIVE.value,
        must_change_password=True,
        token_version=1,
        created_by=actor.id if actor and actor.id else None,
    )
    db.add(user)
    await db.flush()
    for role in roles:
        await db.execute(
            sys_user_role.insert().values(user_id=user.id, role_id=role.id)
        )
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="user.create",
        resource="user",
        resource_id=user.id,
        detail={"username": user.username, "roles": [r.code for r in roles]},
        ip=ip,
        user_agent=user_agent,
        method="POST",
        path=path,
        status_code=201,
    )
    await db.commit()
    return user, roles


async def _is_system_admin(db: AsyncSession, user_id: int) -> bool:
    count = (
        await db.execute(
            select(func.count())
            .select_from(sys_user_role)
            .join(SysRole, SysRole.id == sys_user_role.c.role_id)
            .where(
                sys_user_role.c.user_id == user_id,
                SysRole.code == SYSTEM_ADMIN_CODE,
            )
        )
    ).scalar_one()
    return count > 0


async def _count_active_admins(db: AsyncSession, exclude_user_id: int | None = None) -> int:
    stmt = (
        select(func.count(func.distinct(SysUser.id)))
        .select_from(SysUser)
        .join(sys_user_role, sys_user_role.c.user_id == SysUser.id)
        .join(SysRole, SysRole.id == sys_user_role.c.role_id)
        .where(
            SysRole.code == SYSTEM_ADMIN_CODE,
            SysUser.status == UserStatus.ACTIVE.value,
        )
    )
    if exclude_user_id is not None:
        stmt = stmt.where(SysUser.id != exclude_user_id)
    return (await db.execute(stmt)).scalar_one()


async def update_user(
    db: AsyncSession,
    *,
    user_id: int,
    display_name: str | None,
    email: str | None,
    phone: str | None,
    status: str | None,
    actor: CurrentUser | None,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> SysUser:
    user = await get_user(db, user_id)
    before = {
        "display_name": user.display_name,
        "email": user.email,
        "phone": user.phone,
        "status": user.status,
    }
    if status is not None and status not in {s.value for s in UserStatus}:
        raise ValidationError("无效的账号状态")
    if status == UserStatus.DISABLED.value:
        if actor and actor.id == user.id:
            raise ConflictError("不能禁用当前登录账号")
        if await _is_system_admin(db, user.id) and await _count_active_admins(db, user.id) == 0:
            raise ConflictError("至少保留一个启用状态的系统管理员")

    if display_name is not None:
        user.display_name = display_name.strip()
    if email is not None:
        user.email = email
    if phone is not None:
        user.phone = phone
    if status is not None:
        user.status = status
        if status != UserStatus.ACTIVE.value:
            user.token_version += 1
    if status == UserStatus.ACTIVE.value:
        user.locked_until = None
        user.failed_login_count = 0

    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="user.update",
        resource="user",
        resource_id=user.id,
        detail={
            "before": before,
            "after": {
                "display_name": user.display_name,
                "email": user.email,
                "phone": user.phone,
                "status": user.status,
            },
        },
        ip=ip,
        user_agent=user_agent,
        method="PATCH",
        path=path,
        status_code=200,
    )
    await db.commit()
    return user


async def set_user_roles(
    db: AsyncSession,
    *,
    user_id: int,
    role_ids: list[int],
    actor: CurrentUser | None,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> list[SysRole]:
    user = await get_user(db, user_id)
    if actor and actor.id == user.id:
        raise ConflictError("不能修改自己的角色")
    roles = await _validate_role_ids(db, role_ids)
    was_admin = await _is_system_admin(db, user.id)
    will_admin = any(role.code == SYSTEM_ADMIN_CODE for role in roles)
    if was_admin and not will_admin and await _count_active_admins(db, user.id) == 0:
        raise ConflictError("至少保留一个启用状态的系统管理员")

    await db.execute(delete(sys_user_role).where(sys_user_role.c.user_id == user.id))
    for role in roles:
        await db.execute(
            sys_user_role.insert().values(user_id=user.id, role_id=role.id)
        )
    user.token_version += 1
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="user.assign_roles",
        resource="user",
        resource_id=user.id,
        detail={"roles": [r.code for r in roles]},
        ip=ip,
        user_agent=user_agent,
        method="PUT",
        path=path,
        status_code=200,
    )
    await db.commit()
    return roles


async def reset_password(
    db: AsyncSession,
    *,
    user_id: int,
    actor: CurrentUser | None,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> str:
    user = await get_user(db, user_id)
    temp = _generate_temp_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    user.token_version += 1
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="user.reset_password",
        resource="user",
        resource_id=user.id,
        ip=ip,
        user_agent=user_agent,
        method="POST",
        path=path,
        status_code=200,
    )
    await db.commit()
    return temp


async def unlock_user(
    db: AsyncSession,
    *,
    user_id: int,
    actor: CurrentUser | None,
    ip: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> SysUser:
    user = await get_user(db, user_id)
    user.status = UserStatus.ACTIVE.value
    user.failed_login_count = 0
    user.locked_until = None
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="user.unlock",
        resource="user",
        resource_id=user.id,
        ip=ip,
        user_agent=user_agent,
        method="POST",
        path=path,
        status_code=200,
    )
    await db.commit()
    return user


async def change_password(
    db: AsyncSession,
    *,
    user_id: int,
    old_password: str,
    new_password: str,
    actor: CurrentUser | None,
    path: str | None = None,
) -> SysUser:
    user = await get_user(db, user_id)
    if not verify_password(old_password, user.password_hash):
        raise AppError("原密码错误", code="AUTH_FAILED", status_code=400)
    _validate_password(new_password)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="auth.change_password",
        resource="auth",
        resource_id=user.id,
        path=path,
        status_code=200,
    )
    await db.commit()
    return user


async def role_counts(db: AsyncSession) -> tuple[dict[int, int], dict[int, int]]:
    user_counts = dict(
        (
            await db.execute(
                select(sys_user_role.c.role_id, func.count()).group_by(
                    sys_user_role.c.role_id
                )
            )
        ).all()
    )
    permission_counts = dict(
        (
            await db.execute(
                select(sys_role_permission.c.role_id, func.count()).group_by(
                    sys_role_permission.c.role_id
                )
            )
        ).all()
    )
    return user_counts, permission_counts


async def list_roles(db: AsyncSession) -> list[tuple[SysRole, int, int]]:
    roles = (await db.execute(select(SysRole).order_by(SysRole.id))).scalars().all()
    user_counts, permission_counts = await role_counts(db)
    return [
        (role, user_counts.get(role.id, 0), permission_counts.get(role.id, 0))
        for role in roles
    ]


async def role_permission_codes(db: AsyncSession, role_id: int) -> list[str]:
    rows = await db.execute(
        select(SysPermission.code)
        .join(sys_role_permission, sys_role_permission.c.permission_id == SysPermission.id)
        .where(sys_role_permission.c.role_id == role_id)
        .order_by(SysPermission.sort)
    )
    return list(rows.scalars().all())


async def get_role_detail(
    db: AsyncSession, role_id: int
) -> tuple[SysRole, int, int, list[str]]:
    role = await db.get(SysRole, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    user_counts, permission_counts = await role_counts(db)
    codes = await role_permission_codes(db, role.id)
    return role, user_counts.get(role.id, 0), permission_counts.get(role.id, 0), codes


async def _validate_permission_codes(db: AsyncSession, codes: list[str]) -> list[SysPermission]:
    if not codes:
        return []
    rows = await db.execute(select(SysPermission).where(SysPermission.code.in_(codes)))
    permissions = list(rows.scalars().all())
    if len({p.code for p in permissions}) != len(set(codes)):
        raise ValidationError("包含不存在的权限码")
    return permissions


async def create_role(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    description: str | None,
    data_scope: str,
    permission_codes: list[str],
    actor: CurrentUser | None,
    path: str | None = None,
) -> SysRole:
    code = code.strip()
    if not _ROLE_CODE_RE.match(code):
        raise ValidationError("角色编码必须为大写字母、数字或下划线，长度 2–64")
    exists = (await db.execute(select(SysRole).where(SysRole.code == code))).scalar_one_or_none()
    if exists is not None:
        raise ConflictError("角色编码已存在")
    if data_scope not in {"ALL", "ENV", "OWN"}:
        raise ValidationError("无效的数据范围")
    permissions = await _validate_permission_codes(db, permission_codes)
    role = SysRole(
        code=code,
        name=name.strip(),
        description=description,
        data_scope=data_scope,
        is_system=False,
        status="ACTIVE",
    )
    db.add(role)
    await db.flush()
    for permission in permissions:
        await db.execute(
            sys_role_permission.insert().values(
                role_id=role.id, permission_id=permission.id
            )
        )
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="role.create",
        resource="role",
        resource_id=role.id,
        detail={"code": role.code, "permissions": permission_codes},
        method="POST",
        path=path,
        status_code=201,
    )
    await db.commit()
    return role


async def update_role(
    db: AsyncSession,
    *,
    role_id: int,
    name: str | None,
    description: str | None,
    data_scope: str | None,
    status: str | None,
    actor: CurrentUser | None,
    path: str | None = None,
) -> SysRole:
    role = await db.get(SysRole, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    if data_scope is not None and data_scope not in {"ALL", "ENV", "OWN"}:
        raise ValidationError("无效的数据范围")
    if status is not None and status not in {"ACTIVE", "DISABLED"}:
        raise ValidationError("无效的角色状态")
    if role.code == SYSTEM_ADMIN_CODE and status == "DISABLED":
        raise ConflictError("系统管理员角色不能禁用")
    if name is not None:
        role.name = name.strip()
    if description is not None:
        role.description = description
    if data_scope is not None:
        role.data_scope = data_scope
    if status is not None:
        role.status = status
        if status == "DISABLED":
            await db.execute(
                SysUser.__table__.update()
                .where(
                    SysUser.id.in_(
                        select(sys_user_role.c.user_id).where(
                            sys_user_role.c.role_id == role.id
                        )
                    )
                )
                .values(token_version=SysUser.token_version + 1)
            )
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="role.update",
        resource="role",
        resource_id=role.id,
        detail={"name": role.name, "data_scope": role.data_scope, "status": role.status},
        method="PATCH",
        path=path,
        status_code=200,
    )
    await db.commit()
    return role


async def set_role_permissions(
    db: AsyncSession,
    *,
    role_id: int,
    permission_codes: list[str],
    actor: CurrentUser | None,
    path: str | None = None,
) -> list[str]:
    role = await db.get(SysRole, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    if role.code == SYSTEM_ADMIN_CODE:
        raise ConflictError("系统管理员权限固定为全量，不能修改")
    permissions = await _validate_permission_codes(db, permission_codes)
    await db.execute(
        delete(sys_role_permission).where(sys_role_permission.c.role_id == role.id)
    )
    for permission in permissions:
        await db.execute(
            sys_role_permission.insert().values(
                role_id=role.id, permission_id=permission.id
            )
        )
    await db.execute(
        SysUser.__table__.update()
        .where(
            SysUser.id.in_(
                select(sys_user_role.c.user_id).where(sys_user_role.c.role_id == role.id)
            )
        )
        .values(token_version=SysUser.token_version + 1)
    )
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="role.update_permissions",
        resource="role",
        resource_id=role.id,
        detail={"permissions": sorted(set(permission_codes))},
        method="PUT",
        path=path,
        status_code=200,
    )
    await db.commit()
    return sorted(set(permission_codes))


async def delete_role(
    db: AsyncSession,
    *,
    role_id: int,
    actor: CurrentUser | None,
    path: str | None = None,
) -> None:
    role = await db.get(SysRole, role_id)
    if role is None:
        raise NotFoundError("角色不存在")
    if role.is_system:
        raise ConflictError("内置角色不能删除")
    user_count = (
        await db.execute(
            select(func.count()).select_from(sys_user_role).where(
                sys_user_role.c.role_id == role.id
            )
        )
    ).scalar_one()
    if user_count:
        raise ConflictError("角色仍有关联用户，不能删除")
    await db.execute(delete(sys_role_permission).where(sys_role_permission.c.role_id == role.id))
    await db.delete(role)
    add_audit(
        db,
        actor=actor,
        actor_username=actor.username if actor else None,
        action="role.delete",
        resource="role",
        resource_id=role_id,
        method="DELETE",
        path=path,
        status_code=204,
    )
    await db.commit()


async def list_permissions(db: AsyncSession) -> list[SysPermission]:
    rows = await db.execute(select(SysPermission).order_by(SysPermission.sort, SysPermission.id))
    return list(rows.scalars().all())


async def list_audit_logs(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    actor_id: int | None = None,
    action: str | None = None,
    resource: str | None = None,
    success: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[list[SysAuditLog], int]:
    filters = []
    if actor_id is not None:
        filters.append(SysAuditLog.actor_id == actor_id)
    action_filter = keyword_condition((SysAuditLog.action,), action)
    if action_filter is not None:
        filters.append(action_filter)
    if resource:
        filters.append(SysAuditLog.resource == resource)
    if success is not None:
        filters.append(SysAuditLog.success == success)
    if date_from is not None:
        filters.append(SysAuditLog.created_at >= date_from)
    if date_to is not None:
        filters.append(SysAuditLog.created_at <= date_to)

    count_stmt = select(func.count()).select_from(SysAuditLog)
    stmt = select(SysAuditLog)
    for item in filters:
        count_stmt = count_stmt.where(item)
        stmt = stmt.where(item)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = (
        stmt.order_by(SysAuditLog.created_at.desc(), SysAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await db.execute(stmt)).scalars().all()), total


def permission_catalog_rows() -> list[dict[str, Any]]:
    return [
        {
            "code": item.code,
            "name": item.name,
            "module": item.module,
            "action": item.action,
            "description": item.description,
            "sort": item.sort,
        }
        for item in PERMISSION_BY_CODE.values()
    ]
