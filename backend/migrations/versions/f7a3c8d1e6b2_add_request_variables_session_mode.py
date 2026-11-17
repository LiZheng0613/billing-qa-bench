"""add request variables and session binding mode

Revision ID: f7a3c8d1e6b2
Revises: e9f2b6c1a3d4
Create Date: 2026-11-17 12:00:00.000000

新增能力：
- test_case.request_variables：用例级请求变量（按用例注入不同取值）
- test_case.session_mode：用例级会话绑定方式（INHERIT 跟随环境）
- environment.session_mode：环境级会话绑定方式（SERVER_RETURNED / CLIENT_GENERATED）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a3c8d1e6b2"
down_revision: str | None = "e9f2b6c1a3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_case",
        sa.Column(
            "request_variables",
            sa.JSON(),
            nullable=True,
            comment="case-level request variables injected into headers/body templates",
        ),
    )
    op.add_column(
        "test_case",
        sa.Column(
            "session_mode",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'INHERIT'"),
            comment="session binding mode: INHERIT / SERVER_RETURNED / CLIENT_GENERATED",
        ),
    )
    op.add_column(
        "environment",
        sa.Column(
            "session_mode",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'SERVER_RETURNED'"),
            comment="session binding mode: SERVER_RETURNED / CLIENT_GENERATED",
        ),
    )


def downgrade() -> None:
    op.drop_column("environment", "session_mode")
    op.drop_column("test_case", "session_mode")
    op.drop_column("test_case", "request_variables")
