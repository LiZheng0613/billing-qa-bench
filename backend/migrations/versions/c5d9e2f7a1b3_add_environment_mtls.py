"""add environment mtls

Revision ID: c5d9e2f7a1b3
Revises: b4c8d1e6f2a9
Create Date: 2026-11-19 15:00:00.000000

新增能力：
- environment.mtls：被测接口 mTLS 双向证书配置。客户端证书与私钥以加密 PEM
  存储，执行时落为临时文件并交给 httpx 建立 TLS 客户端证书链；可选私钥口令与
  自定义 CA 证书。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d9e2f7a1b3"
down_revision: str | None = "b4c8d1e6f2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "environment",
        sa.Column(
            "mtls",
            sa.JSON(),
            nullable=True,
            comment="mTLS client cert/key encrypted PEM + optional password and CA",
        ),
    )


def downgrade() -> None:
    op.drop_column("environment", "mtls")
