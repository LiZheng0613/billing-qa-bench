"""add task schedule (immediate / scheduled / daily / weekly)

Revision ID: d8e1a4c2b7f3
Revises: c4f2b8d7e5a1
Create Date: 2026-11-16 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e1a4c2b7f3"
down_revision: str | None = "c4f2b8d7e5a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_task",
        sa.Column(
            "schedule_type",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'IMMEDIATE'"),
            comment="IMMEDIATE / SCHEDULED / DAILY / WEEKLY",
        ),
    )
    # 只为回填历史行临时加默认值；ORM 侧用 Python 默认值，保持 alembic check 无漂移。
    op.alter_column("batch_task", "schedule_type", server_default=None)
    op.add_column(
        "batch_task",
        sa.Column(
            "scheduled_at", sa.DateTime(), nullable=True, comment="指定时间执行的一次性触发时间"
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "schedule_time", sa.String(length=5), nullable=True, comment="周期执行的时刻，HH:MM"
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "schedule_weekdays",
            sa.JSON(),
            nullable=True,
            comment="每周执行的星期（1=周一 … 7=周日）",
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column("next_run_at", sa.DateTime(), nullable=True, comment="下次触发时间"),
    )
    op.add_column(
        "batch_task",
        sa.Column("last_run_at", sa.DateTime(), nullable=True, comment="最近一次触发时间"),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "schedule_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="定时计划是否继续触发",
        ),
    )
    op.alter_column("batch_task", "schedule_active", server_default=None)
    op.add_column(
        "batch_task",
        sa.Column(
            "schedule_source_id",
            sa.Integer(),
            nullable=True,
            comment="由哪个定时计划触发（周期任务每次生成新任务）",
        ),
    )
    op.create_foreign_key(
        "fk_batch_task_schedule_source_id_batch_task",
        "batch_task",
        "batch_task",
        ["schedule_source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_batch_task_next_run", "batch_task", ["schedule_type", "next_run_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_batch_task_next_run", table_name="batch_task")
    op.drop_constraint(
        "fk_batch_task_schedule_source_id_batch_task", "batch_task", type_="foreignkey"
    )
    op.drop_column("batch_task", "schedule_source_id")
    op.drop_column("batch_task", "schedule_active")
    op.drop_column("batch_task", "last_run_at")
    op.drop_column("batch_task", "next_run_at")
    op.drop_column("batch_task", "schedule_weekdays")
    op.drop_column("batch_task", "schedule_time")
    op.drop_column("batch_task", "scheduled_at")
    op.drop_column("batch_task", "schedule_type")
