"""统一通知：从审计事件投影业务通知，过滤登录/退出等流水。"""

from __future__ import annotations

from sqlalchemy import not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.models.user import SysAuditLog
from billing_qa.schemas.notification import NotificationOut

_NOTIFICATION_ACTIONS = {
    "user.create",
    "user.update",
    "user.assign_roles",
    "user.reset_password",
    "user.unlock",
    "role.create",
    "role.update",
    "role.update_permissions",
    "role.delete",
}


def _user_ref(row: SysAuditLog) -> str:
    detail = row.detail or {}
    username = detail.get("username")
    if isinstance(username, str) and username:
        return username
    return f"用户 #{row.resource_id}" if row.resource_id else "用户"


def _project(row: SysAuditLog) -> NotificationOut | None:
    action = row.action
    detail = row.detail or {}
    actor = row.actor_username
    resource_id = row.resource_id

    if action == "user.create":
        return NotificationOut(
            id=row.id,
            title="新建用户",
            message=f"{actor} 创建了 {_user_ref(row)}",
            level="success",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    if action == "user.update":
        before = detail.get("before") if isinstance(detail.get("before"), dict) else {}
        after = detail.get("after") if isinstance(detail.get("after"), dict) else {}
        if before.get("status") == after.get("status"):
            return None
        return NotificationOut(
            id=row.id,
            title="账号状态变更",
            message=f"{actor} 将 {_user_ref(row)} 设为 {after.get('status', '新状态')}",
            level="info",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    if action == "user.assign_roles":
        roles = detail.get("roles")
        role_text = "、".join(roles) if isinstance(roles, list) and roles else "未分配角色"
        return NotificationOut(
            id=row.id,
            title="角色变更",
            message=f"{actor} 调整了 {_user_ref(row)} 的角色：{role_text}",
            level="info",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    if action == "user.reset_password":
        return NotificationOut(
            id=row.id,
            title="密码重置",
            message=f"{actor} 重置了 {_user_ref(row)} 的密码",
            level="info",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    if action == "user.unlock":
        return NotificationOut(
            id=row.id,
            title="账号解锁",
            message=f"{actor} 解锁了 {_user_ref(row)}",
            level="success",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    if action in {"role.create", "role.update", "role.delete"}:
        titles = {
            "role.create": "新建角色",
            "role.update": "角色更新",
            "role.delete": "角色删除",
        }
        return NotificationOut(
            id=row.id,
            title=titles[action],
            message=f"{actor} {titles[action]} #{resource_id or '-'}",
            level="success" if action == "role.create" else "info",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    if action == "role.update_permissions":
        codes = detail.get("permissions")
        count = len(codes) if isinstance(codes, list) else 0
        return NotificationOut(
            id=row.id,
            title="权限变更",
            message=f"{actor} 更新了角色 #{resource_id or '-'} 的权限（{count} 项）",
            level="info",
            actor=actor,
            resource=row.resource,
            resource_id=resource_id,
            created_at=row.created_at,
        )
    return None


async def list_notifications(
    db: AsyncSession, limit: int = 5
) -> list[NotificationOut]:
    rows = (
        await db.execute(
            select(SysAuditLog)
            .where(
                not_(SysAuditLog.action.like("auth.%")),
                SysAuditLog.action.in_(sorted(_NOTIFICATION_ACTIONS)),
            )
            .order_by(SysAuditLog.created_at.desc(), SysAuditLog.id.desc())
            .limit(max(limit * 6, 30))
        )
    ).scalars().all()

    items: list[NotificationOut] = []
    for row in rows:
        item = _project(row)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items
