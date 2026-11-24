"""add hot-path indexes for plan runs and execution queries (PostgreSQL)

Revision ID: e7b3c8f5a2d9
Revises: a1c4e7b9d2f5
Create Date: 2026-11-24 18:30:00.000000

四个索引对应全场景盘点出的高频缺口：

- batch_task(schedule_source_id)：周期计划「历次执行」列表（count + 分页 +
  状态聚合）、任务列表派生次数统计、调度器派发前的上一轮状态检查都按
  schedule_source_id 过滤；该表每触发一次 +1 行、只增不减，无索引时随
  行数线性变慢（任务详情页展开历次执行慢的主因）。
- batch_task(environment_id)：环境删除保护按该列统计任务引用，外键在
  PostgreSQL 不自动建索引。
- execution_record(task_id, case_id, id)：覆盖「每用例最新一次」
  （max(id) GROUP BY case_id，指标计算/列表未完成数/明细分页共用）与
  单用例 top-1（WHERE task_id+case_id ORDER BY id DESC LIMIT 1）；
  尾列 id 让两者都能走 index-only scan。
- execution_record(started_at)：大盘通过率/时延/趋势/环境对比等全窗口
  聚合按时间窗过滤；此前 started_at 只作为 (case_id, started_at) 的
  第二列存在，无法服务纯时间窗查询。

生产大表首次上线时，如担心建索引期间的写入阻塞，可临时把 create_index
加上 postgresql_concurrently=True 并在非事务环境（transaction_per_migration
或 autocommit_block）执行；普通规模直接升级即可。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e7b3c8f5a2d9"
down_revision: str | None = "a1c4e7b9d2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_batch_task_schedule_source",
        "batch_task",
        ["schedule_source_id"],
    )
    op.create_index(
        "ix_batch_task_env",
        "batch_task",
        ["environment_id"],
    )
    op.create_index(
        "ix_execution_task_case",
        "execution_record",
        ["task_id", "case_id", "id"],
    )
    op.create_index(
        "ix_execution_started",
        "execution_record",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_started", table_name="execution_record")
    op.drop_index("ix_execution_task_case", table_name="execution_record")
    op.drop_index("ix_batch_task_env", table_name="batch_task")
    op.drop_index("ix_batch_task_schedule_source", table_name="batch_task")
