"""金标准查询与事实级判分结果。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import Base, now


class GoldQuery(Base):
    """一次取证执行的结果快照，`rows` 即用例事实的轻量快照。"""

    __tablename__ = "gold_query"
    __table_args__ = (
        Index("ix_gold_query_case", "case_id", "is_active"),
        Index("ix_gold_query_capability", "capability_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), nullable=False
    )
    capability_id: Mapped[int | None] = mapped_column(
        ForeignKey("capability.id", ondelete="SET NULL"), nullable=True
    )
    capability_version: Mapped[int | None] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(
        String(16), default="CAPABILITY", comment="CAPABILITY/CAPABILITY_AUTO/AGENT_TRACE/..."
    )
    fact_ids: Mapped[list | None] = mapped_column(JSON, comment="本次取证覆盖的事实 id")
    template_params: Mapped[dict | None] = mapped_column(JSON)
    query_text: Mapped[str | None] = mapped_column(Text)
    columns: Mapped[list | None] = mapped_column(JSON)
    rows: Mapped[list | None] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    executor_version: Mapped[str | None] = mapped_column(String(32))
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FactResult(Base):
    """单条事实的判分结果，支撑事实级统计与判分器自检。"""

    __tablename__ = "fact_result"
    __table_args__ = (
        Index("ix_fact_result_execution", "execution_id"),
        Index("ix_fact_result_fact", "fact_id", "passed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("execution_record.id", ondelete="CASCADE"), nullable=False
    )
    fact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="scalar")
    label: Mapped[str | None] = mapped_column(String(200))
    expected: Mapped[dict | None] = mapped_column(JSON)
    actual: Mapped[dict | None] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(512))
    evidence_ref: Mapped[str | None] = mapped_column(String(256))
    is_gate: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
