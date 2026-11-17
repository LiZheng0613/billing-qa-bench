"""drop unused simulator_model_id from test_case

模拟器模型改为「全局唯一启用」，不再按用例选择，该列已无业务含义。

Revision ID: b1d4f7c9e2a6
Revises: a6c9e3f8b2d5
Create Date: 2026-11-17 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1d4f7c9e2a6"
down_revision: str | None = "a6c9e3f8b2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("test_case", "simulator_model_id")


def downgrade() -> None:
    op.add_column(
        "test_case",
        sa.Column("simulator_model_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_test_case_simulator_model_id_model_config",
        "test_case",
        "model_config",
        ["simulator_model_id"],
        ["id"],
        ondelete="SET NULL",
    )
