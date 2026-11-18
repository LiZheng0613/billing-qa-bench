"""add round chain link for repeat rounds self-heal

Revision ID: d3b7e5f9a1c2
Revises: a7d4e9c2f1b8
Create Date: 2026-11-18 18:40:00.000000

连续多轮自愈：
- batch_task.round_prev_id：指向上一轮任务，用于幂等补齐下一轮
- 唯一索引：同一轮只能派生一个下一轮，避免重试/恢复扫描重复创建
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3b7e5f9a1c2"
down_revision: str | None = "a7d4e9c2f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "batch_task",
        sa.Column(
            "round_prev_id",
            sa.Integer(),
            sa.ForeignKey("batch_task.id", ondelete="SET NULL"),
            nullable=True,
            comment="连续轮次上一轮任务 id",
        ),
    )
    # 回填历史轮次链：按 (来源, 轮次) 分组后按 id 顺序「第 k 条配上一轮第 k 条」，
    # 既能还原串行链，也能兼容同一根任务被重跑产生的并行链；映射到同一上一轮的
    # 只会有一条，满足唯一索引。
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT id, schedule_source_id, round_index
            FROM batch_task
            WHERE schedule_source_id IS NOT NULL AND round_index > 1
            ORDER BY schedule_source_id, round_index, id
            """
        )
    ).all()
    groups: dict[tuple[int, int], list[int]] = {}
    for task_id, source_id, round_index in rows:
        groups.setdefault((int(source_id), int(round_index)), []).append(int(task_id))

    round1_rows = conn.execute(
        sa.text(
            """
            SELECT id, schedule_source_id
            FROM batch_task
            WHERE round_index = 1
            ORDER BY id
            """
        )
    ).all()
    for task_id, source_id in round1_rows:
        task_id = int(task_id)
        if source_id is not None:
            groups.setdefault((int(source_id), 1), []).append(task_id)
        else:
            # 立即执行链：第 1 轮即根任务自身
            groups.setdefault((task_id, 1), [task_id])
    for key in groups:
        groups[key].sort()

    updates: list[dict[str, int]] = []
    for (source_id, round_index), ids in groups.items():
        if round_index <= 1:
            continue
        previous = groups.get((source_id, round_index - 1), [])
        for position, task_id in enumerate(ids):
            if position < len(previous):
                updates.append({"task_id": task_id, "prev_id": previous[position]})
    for item in updates:
        conn.execute(
            sa.text("UPDATE batch_task SET round_prev_id = :prev_id WHERE id = :task_id"),
            item,
        )
    op.create_index(
        "ux_batch_task_round_prev",
        "batch_task",
        ["round_prev_id"],
        unique=True,
        postgresql_where=sa.text("round_prev_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_batch_task_round_prev", table_name="batch_task")
    op.drop_column("batch_task", "round_prev_id")
