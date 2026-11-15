"""add model connection type and OpenCode Serve settings

Revision ID: c4f2b8d7e5a1
Revises: b7f3a1c9d2e4
Create Date: 2026-11-15 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f2b8d7e5a1"
down_revision: str | None = "b7f3a1c9d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_config",
        sa.Column(
            "connection_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'OPENAI_COMPATIBLE'"),
            comment="OPENAI_COMPATIBLE / OPENCODE_SERVE",
        ),
    )
    # 只为回填历史行临时加默认值；ORM 侧用 Python 默认值，保持 alembic check 无漂移。
    op.alter_column("model_config", "connection_type", server_default=None)
    op.add_column(
        "model_config",
        sa.Column(
            "serve_url",
            sa.String(length=512),
            nullable=True,
            comment="OpenCode Serve 地址，例如 http://127.0.0.1",
        ),
    )
    op.add_column(
        "model_config",
        sa.Column(
            "serve_port",
            sa.Integer(),
            nullable=True,
            comment="OpenCode Serve 端口",
        ),
    )
    op.add_column(
        "model_config",
        sa.Column(
            "serve_username",
            sa.String(length=128),
            nullable=True,
            comment="OpenCode Serve Basic Auth 用户名",
        ),
    )
    op.add_column(
        "model_config",
        sa.Column(
            "serve_password_encrypted",
            sa.Text(),
            nullable=True,
            comment="OpenCode Serve Basic Auth 密码（加密）",
        ),
    )
    op.add_column(
        "model_config",
        sa.Column(
            "opencode_agent",
            sa.String(length=128),
            nullable=True,
            comment="OpenCode agent ID",
        ),
    )
    op.add_column(
        "model_config",
        sa.Column(
            "opencode_model",
            sa.String(length=128),
            nullable=True,
            comment="OpenCode 模型，格式 provider/model",
        ),
    )
    op.add_column(
        "model_config",
        sa.Column(
            "opencode_directory",
            sa.String(length=512),
            nullable=True,
            comment="OpenCode session location.directory",
        ),
    )
    op.alter_column(
        "model_config",
        "base_url",
        comment="OpenAI 兼容 base_url；OpenCode 模式存 serve 根地址",
    )


def downgrade() -> None:
    op.drop_column("model_config", "opencode_directory")
    op.drop_column("model_config", "opencode_model")
    op.drop_column("model_config", "opencode_agent")
    op.drop_column("model_config", "serve_password_encrypted")
    op.drop_column("model_config", "serve_username")
    op.drop_column("model_config", "serve_port")
    op.drop_column("model_config", "serve_url")
    op.drop_column("model_config", "connection_type")
