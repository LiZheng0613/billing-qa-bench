"""用户、角色、权限与审计日志模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import Base, TimestampMixin, now


class SysUser(Base, TimestampMixin):
    """系统账号。"""

    __tablename__ = "sys_user"
    __table_args__ = (
        Index("ix_sys_user_username_lower", "username_lower", unique=True),
        Index("ix_sys_user_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, comment="登录名，创建后不可改")
    username_lower: Mapped[str] = mapped_column(String(64), nullable=False, comment="小写登录名")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="展示名")
    email: Mapped[str | None] = mapped_column(String(128), comment="邮箱")
    phone: Mapped[str | None] = mapped_column(String(32), comment="手机号")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, comment="密码哈希")
    status: Mapped[str] = mapped_column(
        String(16), default="ACTIVE", nullable=False, comment="ACTIVE / DISABLED / LOCKED"
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="首次登录是否必须改密"
    )
    token_version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="改密/改角色/禁用时递增"
    )
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, comment="锁定截止时间")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近登录时间")
    last_login_ip: Mapped[str | None] = mapped_column(String(64), comment="最近登录 IP")
    created_by: Mapped[int | None] = mapped_column(Integer, comment="创建人 user_id")


class SysRole(Base, TimestampMixin):
    """角色。"""

    __tablename__ = "sys_role"
    __table_args__ = (
        Index("ix_sys_role_code", "code", unique=True),
        Index("ix_sys_role_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色编码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名称")
    description: Mapped[str | None] = mapped_column(Text, comment="角色说明")
    data_scope: Mapped[str] = mapped_column(
        String(16), default="ALL", nullable=False, comment="ALL / ENV / OWN（预留）"
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="内置角色")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class SysPermission(Base):
    """权限目录。"""

    __tablename__ = "sys_permission"
    __table_args__ = (Index("ix_sys_permission_code", "code", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="资源:动作")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="权限名称")
    module: Mapped[str] = mapped_column(String(32), nullable=False, comment="模块")
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="动作")
    description: Mapped[str | None] = mapped_column(String(255), comment="说明")
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="排序")


sys_user_role = Table(
    "sys_user_role",
    Base.metadata,
    Column(
        "user_id",
        Integer,
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        Integer,
        ForeignKey("sys_role.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("created_at", DateTime, default=now, nullable=False),
)


sys_role_permission = Table(
    "sys_role_permission",
    Base.metadata,
    Column(
        "role_id",
        Integer,
        ForeignKey("sys_role.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        Integer,
        ForeignKey("sys_permission.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("created_at", DateTime, default=now, nullable=False),
)


class SysAuditLog(Base):
    """追加型审计日志。"""

    __tablename__ = "sys_audit_log"
    __table_args__ = (
        Index("ix_sys_audit_created_at", "created_at"),
        Index("ix_sys_audit_actor", "actor_id", "created_at"),
        Index("ix_sys_audit_action", "action", "created_at"),
        Index("ix_sys_audit_resource", "resource", "resource_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("sys_user.id", ondelete="SET NULL"), comment="操作人 user_id"
    )
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False, comment="用户名快照")
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="动作")
    resource: Mapped[str] = mapped_column(String(64), nullable=False, comment="资源")
    resource_id: Mapped[str | None] = mapped_column(String(64), comment="资源 ID")
    method: Mapped[str | None] = mapped_column(String(8))
    path: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    status_code: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, comment="脱敏后的前后变化")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
