"""add task execution error retry config (PostgreSQL)

Revision ID: d1e4f7a9b2c6
Revises: c5d9e2f7a1b3
Create Date: 2026-11-19 18:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e4f7a9b2c6"
down_revision: str | None = "c5d9e2f7a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_task",
        sa.Column(
            "retry_on_error",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="执行异常是否自动重试（含非环境异常）",
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "max_error_retries",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
            comment="执行异常最大自动重试次数",
        ),
    )
    # 历史任务沿用原来的环境重试开关，保证升级前后行为一致。
    op.execute(
        "UPDATE batch_task "
        "SET retry_on_error = retry_on_env_error, "
        "max_error_retries = max_env_retries"
    )
    op.alter_column(
        "batch_task",
        "retry_backoff_seconds",
        existing_type=sa.Float(),
        comment="失败重试基础退避秒数（指数递增）",
    )


def downgrade() -> None:
    op.alter_column(
        "batch_task",
        "retry_backoff_seconds",
        existing_type=sa.Float(),
        comment="环境类失败重试基础退避秒数（指数递增）",
    )
    op.drop_column("batch_task", "max_error_retries")
    op.drop_column("batch_task", "retry_on_error")
