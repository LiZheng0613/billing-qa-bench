"""用例候选池、判分器自检与业务口径字典。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import Base, TimestampMixin, now


class CaseCandidate(Base, TimestampMixin):
    """待转正的用例候选（生产日志 / 轨迹 / 手工）。"""

    __tablename__ = "case_candidate"
    __table_args__ = (
        Index("ix_case_candidate_status", "status", "created_at"),
        Index("ix_case_candidate_dedupe", "dedupe_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(
        String(24), default="MANUAL", comment="PRODUCTION/TRACE/LLM/MANUAL"
    )
    raw_question: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict | None] = mapped_column(JSON, comment="环境/客户/账期等标准槽位")
    trace_uid: Mapped[str | None] = mapped_column(String(36))
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environment.id", ondelete="SET NULL"), nullable=True
    )
    draft: Mapped[dict | None] = mapped_column(JSON, comment="预填的用例草稿")
    status: Mapped[str] = mapped_column(
        String(16), default="NEW", comment="NEW/TRIAGED/PROMOTED/REJECTED"
    )
    dedupe_hash: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    promoted_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case.id", ondelete="SET NULL"), nullable=True
    )


class GraderEval(Base):
    """判分器自检（变异测试 / 人工校准）结果。"""

    __tablename__ = "grader_eval"
    __table_args__ = (
        Index("ix_grader_eval_version", "grader_version", "run_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_version: Mapped[str | None] = mapped_column(String(32))
    grader_version: Mapped[str] = mapped_column(String(32), nullable=False)
    mutant_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    detected: Mapped[int] = mapped_column(Integer, default=0)
    false_kills: Mapped[int] = mapped_column(Integer, default=0)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)


class Caliber(Base, TimestampMixin):
    """业务口径字典：每次口径裁定沉淀一条，缺省以业务系统 UI 为准。"""

    __tablename__ = "caliber"
    __table_args__ = (
        Index("ix_caliber_key", "key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    canonical_source: Mapped[str] = mapped_column(
        String(32), default="BUSINESS_UI", comment="缺省基准来源"
    )
    owner: Mapped[str | None] = mapped_column(String(64))
