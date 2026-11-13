"""批量测试任务。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import MYSQL_TABLE_OPTS, Base, TimestampMixin


class BatchTask(Base, TimestampMixin):
    """一次批量回归任务。"""

    __tablename__ = "batch_task"
    __table_args__ = (
        Index("ix_batch_task_status", "status"),
        Index("ix_batch_task_created", "created_at"),
        MYSQL_TABLE_OPTS,  # type: ignore[arg-type]
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="任务编号")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    control_state: Mapped[str] = mapped_column(
        String(16), default="RUNNING", comment="RUNNING/PAUSED/ABORTING"
    )
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environment.id", ondelete="SET NULL"), nullable=True
    )
    case_ids: Mapped[list] = mapped_column(JSON, nullable=False, comment="用例 id 列表")
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    finished_cases: Mapped[int] = mapped_column(Integer, default=0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    max_turns_count: Mapped[int] = mapped_column(Integer, default=0)
    concurrency: Mapped[int] = mapped_column(Integer, default=3)
    qps_limit: Mapped[float] = mapped_column(Float, default=5.0)
    created_by: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[dict | None] = mapped_column(JSON, comment="指标汇总快照")
    error_message: Mapped[str | None] = mapped_column(Text)
