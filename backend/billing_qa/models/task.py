"""批量测试任务。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import Base, TimestampMixin


class BatchTask(Base, TimestampMixin):
    """一次批量回归任务。"""

    __tablename__ = "batch_task"
    __table_args__ = (
        Index("ix_batch_task_status", "status"),
        Index("ix_batch_task_created", "created_at"),
        Index("ix_batch_task_next_run", "schedule_type", "next_run_at"),
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
    retry_on_env_error: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="环境类失败是否自动重试"
    )
    max_env_retries: Mapped[int] = mapped_column(
        Integer, default=2, comment="环境类失败最大自动重试次数"
    )
    retry_backoff_seconds: Mapped[float] = mapped_column(
        Float, default=3.0, comment="环境类失败重试基础退避秒数（指数递增）"
    )
    # ---------- 执行方式：立即 / 指定时间 / 每日 / 每周 ----------
    schedule_type: Mapped[str] = mapped_column(
        String(16), default="IMMEDIATE", comment="IMMEDIATE / SCHEDULED / DAILY / WEEKLY"
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="指定时间执行的一次性触发时间"
    )
    schedule_time: Mapped[str | None] = mapped_column(
        String(5), comment="周期执行的时刻，HH:MM"
    )
    schedule_weekdays: Mapped[list | None] = mapped_column(
        JSON, comment="每周执行的星期（1=周一 … 7=周日）"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, comment="下次触发时间")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近一次触发时间")
    schedule_active: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="定时计划是否继续触发"
    )
    schedule_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_task.id", ondelete="SET NULL"),
        comment="由哪个定时计划触发（周期任务每次生成新任务）",
    )

    created_by: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[dict | None] = mapped_column(JSON, comment="指标汇总快照")
    error_message: Mapped[str | None] = mapped_column(Text)
