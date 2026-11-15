"""add task env retry config and execution attempt tracking (PostgreSQL)

Revision ID: b7f3a1c9d2e4
Revises: 978ec02c913c
Create Date: 2026-11-13 22:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7f3a1c9d2e4"
down_revision: str | None = "978ec02c913c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_task",
        sa.Column(
            "retry_on_env_error",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="环境类失败是否自动重试",
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "max_env_retries",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
            comment="环境类失败最大自动重试次数",
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "retry_backoff_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("3"),
            comment="环境类失败重试基础退避秒数（指数递增）",
        ),
    )
    op.add_column(
        "execution_record",
        sa.Column(
            "attempt_no",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="第几次尝试（环境类失败自动重试时递增）",
        ),
    )
    op.add_column(
        "execution_record",
        sa.Column(
            "error_kind",
            sa.String(length=16),
            nullable=True,
            comment="错误分类：ENV/BUSINESS，空表示无异常",
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_record", "error_kind")
    op.drop_column("execution_record", "attempt_no")
    op.drop_column("batch_task", "retry_backoff_seconds")
    op.drop_column("batch_task", "max_env_retries")
    op.drop_column("batch_task", "retry_on_env_error")
