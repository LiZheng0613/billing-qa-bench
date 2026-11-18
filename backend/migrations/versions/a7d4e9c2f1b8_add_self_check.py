"""add post-failure self-check (task toggle + execution result)

Revision ID: a7d4e9c2f1b8
Revises: c8e5d3a1f2b7
Create Date: 2026-11-18 10:00:00.000000

新增「失败追问自检」能力：
- batch_task.enable_self_check：任务级开关，LLM 裁决失败后在同一会话追问被测 Agent 自检原因；
- execution_record.self_check：追问自检结果（状态 / 触发原因 / 会话 / 渲染 Prompt / Agent 结论）。

内置 Prompt 模板 SELF_CHECK_DEFAULT 由 `python -m billing_qa.db.init_db` 幂等写入
（seed_builtin_prompts），本迁移只负责结构变更。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d4e9c2f1b8"
down_revision: str | None = "c8e5d3a1f2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_task",
        sa.Column(
            "enable_self_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="LLM 裁决失败时在同一会话追问被测 Agent 自检",
        ),
    )
    op.alter_column("batch_task", "enable_self_check", server_default=None)
    op.add_column(
        "execution_record",
        sa.Column(
            "self_check",
            sa.JSON(),
            nullable=True,
            comment="追问自检结果（状态/触发原因/会话/Prompt/结论）",
        ),
    )


def downgrade() -> None:
    op.drop_column("execution_record", "self_check")
    op.drop_column("batch_task", "enable_self_check")
