"""add RBAC tables

Revision ID: b4c8d1e6f2a9
Revises: d3b7e5f9a1c2
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c8d1e6f2a9"
down_revision: str | None = "d3b7e5f9a1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sys_user",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("username_lower", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_ip", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_user")),
    )
    op.create_index(
        "ix_sys_user_username_lower", "sys_user", ["username_lower"], unique=True
    )
    op.create_index("ix_sys_user_status", "sys_user", ["status"], unique=False)

    op.create_table(
        "sys_role",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_scope", sa.String(length=16), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_role")),
    )
    op.create_index("ix_sys_role_code", "sys_role", ["code"], unique=True)
    op.create_index("ix_sys_role_status", "sys_role", ["status"], unique=False)

    op.create_table(
        "sys_permission",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_permission")),
    )
    op.create_index("ix_sys_permission_code", "sys_permission", ["code"], unique=True)

    op.create_table(
        "sys_user_role",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"], ["sys_role.id"], name=op.f("fk_sys_user_role_role_id_sys_role"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_user.id"], name=op.f("fk_sys_user_role_user_id_sys_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name=op.f("pk_sys_user_role")),
    )

    op.create_table(
        "sys_role_permission",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["sys_permission.id"],
            name=op.f("fk_sys_role_permission_permission_id_sys_permission"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["sys_role.id"],
            name=op.f("fk_sys_role_permission_role_id_sys_role"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "role_id", "permission_id", name=op.f("pk_sys_role_permission")
        ),
    )

    op.create_table(
        "sys_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["sys_user.id"],
            name=op.f("fk_sys_audit_log_actor_id_sys_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_audit_log")),
    )
    op.create_index(
        "ix_sys_audit_created_at", "sys_audit_log", ["created_at"], unique=False
    )
    op.create_index(
        "ix_sys_audit_actor", "sys_audit_log", ["actor_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_sys_audit_action", "sys_audit_log", ["action", "created_at"], unique=False
    )
    op.create_index(
        "ix_sys_audit_resource",
        "sys_audit_log",
        ["resource", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sys_audit_resource", table_name="sys_audit_log")
    op.drop_index("ix_sys_audit_action", table_name="sys_audit_log")
    op.drop_index("ix_sys_audit_actor", table_name="sys_audit_log")
    op.drop_index("ix_sys_audit_created_at", table_name="sys_audit_log")
    op.drop_table("sys_audit_log")
    op.drop_table("sys_role_permission")
    op.drop_table("sys_user_role")
    op.drop_index("ix_sys_permission_code", table_name="sys_permission")
    op.drop_table("sys_permission")
    op.drop_index("ix_sys_role_status", table_name="sys_role")
    op.drop_index("ix_sys_role_code", table_name="sys_role")
    op.drop_table("sys_role")
    op.drop_index("ix_sys_user_status", table_name="sys_user")
    op.drop_index("ix_sys_user_username_lower", table_name="sys_user")
    op.drop_table("sys_user")
