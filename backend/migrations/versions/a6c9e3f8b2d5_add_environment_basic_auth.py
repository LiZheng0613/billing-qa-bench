"""add environment basic auth

Revision ID: a6c9e3f8b2d5
Revises: f7a3c8d1e6b2
Create Date: 2026-11-17 18:00:00.000000

新增能力：
- environment.basic_auth：被测接口 Basic 鉴权配置（username + 密码加密存储），
  执行时自动生成 Authorization: Basic <base64(账号:密码)> 请求头。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c9e3f8b2d5"
down_revision: str | None = "f7a3c8d1e6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environment",
        sa.Column(
            "basic_auth",
            sa.JSON(),
            nullable=True,
            comment="Basic auth config: username + encrypted password",
        ),
    )


def downgrade() -> None:
    op.drop_column("environment", "basic_auth")
