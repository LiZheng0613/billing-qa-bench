"""认证种子：权限目录、内置角色与引导管理员。"""

from __future__ import annotations

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.config import settings
from billing_qa.core.permissions import BUILTIN_ROLES, PERMISSION_CATALOG, SYSTEM_ADMIN_CODE
from billing_qa.core.security import hash_password
from billing_qa.db.base import now
from billing_qa.models.user import (
    SysPermission,
    SysRole,
    SysUser,
    sys_role_permission,
    sys_user_role,
)


async def sync_auth_seed(db: AsyncSession) -> None:
    """幂等同步权限目录与内置角色，并在空库时创建引导管理员。"""
    permission_rows = (
        await db.execute(select(SysPermission).order_by(SysPermission.sort))
    ).scalars().all()
    permission_by_code = {row.code: row for row in permission_rows}

    for item in PERMISSION_CATALOG:
        row = permission_by_code.get(item.code)
        if row is None:
            row = SysPermission(
                code=item.code,
                name=item.name,
                module=item.module,
                action=item.action,
                description=item.description,
                sort=item.sort,
            )
            db.add(row)
            permission_by_code[item.code] = row
        else:
            row.name = item.name
            row.module = item.module
            row.action = item.action
            row.description = item.description
            row.sort = item.sort
    await db.flush()

    role_rows = (await db.execute(select(SysRole))).scalars().all()
    role_by_code = {row.code: row for row in role_rows}
    for spec in BUILTIN_ROLES:
        role = role_by_code.get(spec.code)
        created = role is None
        if role is None:
            role = SysRole(
                code=spec.code,
                name=spec.name,
                description=spec.description,
                data_scope=spec.data_scope,
                is_system=spec.is_system,
                status="ACTIVE",
            )
            db.add(role)
            await db.flush()
            role_by_code[spec.code] = role
        else:
            role.name = spec.name
            role.description = spec.description
            role.data_scope = spec.data_scope
            role.is_system = spec.is_system

        if not created and spec.code != SYSTEM_ADMIN_CODE:
            continue

        existing_permission_ids = set(
            (
                await db.execute(
                    select(sys_role_permission.c.permission_id).where(
                        sys_role_permission.c.role_id == role.id
                    )
                )
            ).scalars().all()
        )
        for code in spec.permissions:
            permission = permission_by_code.get(code)
            if permission is None or permission.id in existing_permission_ids:
                continue
            await db.execute(
                insert(sys_role_permission).values(
                    role_id=role.id,
                    permission_id=permission.id,
                    created_at=now(),
                )
            )

    user_count = (
        await db.execute(select(func.count()).select_from(SysUser))
    ).scalar_one()
    if user_count == 0:
        admin_role = role_by_code[SYSTEM_ADMIN_CODE]
        admin = SysUser(
            username=settings.admin_username,
            username_lower=settings.admin_username.lower(),
            display_name="管理员",
            password_hash=hash_password(settings.admin_password),
            status="ACTIVE",
            must_change_password=True,
            token_version=1,
        )
        db.add(admin)
        await db.flush()
        await db.execute(
            insert(sys_user_role).values(
                user_id=admin.id,
                role_id=admin_role.id,
                created_at=now(),
            )
        )

    await db.commit()

