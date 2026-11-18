"""add batch task repeat rounds

Revision ID: c8e5d3a1f2b7
Revises: b1d4f7c9e2a6
Create Date: 2026-11-18 18:00:00.000000

连续多轮执行：
- batch_task.repeat_rounds：一次触发要连续执行的轮数（含第一轮）
- batch_task.round_index：当前任务属于第几轮
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e5d3a1f2b7"
down_revision: str | None = "b1d4f7c9e2a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_task",
        sa.Column(
            "repeat_rounds",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="连续执行轮数（含第一轮）",
        ),
    )
    op.add_column(
        "batch_task",
        sa.Column(
            "round_index",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="当前任务属于第几轮",
        ),
    )


def downgrade() -> None:
    op.drop_column("batch_task", "round_index")
    op.drop_column("batch_task", "repeat_rounds")
