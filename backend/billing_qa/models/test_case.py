"""测试用例与变更审计快照。"""

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from billing_qa.db.base import MYSQL_TABLE_OPTS, Base, TimestampMixin, now


class TestCase(Base, TimestampMixin):
    """多形态测试用例。"""

    __tablename__ = "test_case"
    __table_args__ = (
        Index("ix_test_case_code", "code", unique=True),
        Index("ix_test_case_type_status", "case_type", "status"),
        Index("ix_test_case_env", "environment_id"),
        MYSQL_TABLE_OPTS,  # type: ignore[arg-type]
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="用例编号")
    name: Mapped[str] = mapped_column(String(200), nullable=False, comment="用例名称")
    description: Mapped[str | None] = mapped_column(Text)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SINGLE")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    priority: Mapped[str] = mapped_column(String(16), default="P1")
    tags: Mapped[list | None] = mapped_column(JSON, comment="业务域标签")

    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environment.id", ondelete="SET NULL"), nullable=True
    )

    # ---- 单轮 ----
    query: Mapped[str | None] = mapped_column(Text, comment="固定输入问题")
    expected_answer: Mapped[str | None] = mapped_column(Text, comment="预期口径/数值")
    expected_rules: Mapped[dict | None] = mapped_column(JSON, comment="规则硬校验配置")

    # ---- 静态多轮 ----
    turns: Mapped[list | None] = mapped_column(
        JSON, comment='[{"index":1,"query":"...","expected_rules":{...}}]'
    )
    session_expected: Mapped[dict | None] = mapped_column(JSON, comment="会话级最终预期")
    fail_fast: Mapped[bool] = mapped_column(Boolean, default=False, comment="单轮失败立即中断")

    # ---- 动态多轮 ----
    user_goal: Mapped[str | None] = mapped_column(Text, comment="核心测试目标")
    initial_query: Mapped[str | None] = mapped_column(Text, comment="首轮破冰问题")
    persona_description: Mapped[str | None] = mapped_column(Text, comment="用户画像")
    hidden_context: Mapped[list | None] = mapped_column(
        JSON, comment='[{"key":"order_no","content":"...","release_hint":"..."}]'
    )
    max_turns: Mapped[int] = mapped_column(Integer, default=5, comment="最大交互轮次")
    terminate_conditions: Mapped[dict | None] = mapped_column(JSON, comment="提前终止条件")

    # ---- Prompt 引用 ----
    simulator_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_template.id", ondelete="SET NULL"), nullable=True
    )
    simulator_template_strategy: Mapped[str] = mapped_column(String(16), default="LATEST")
    simulator_template_version: Mapped[int | None] = mapped_column(Integer)
    judge_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_template.id", ondelete="SET NULL"), nullable=True
    )
    judge_template_strategy: Mapped[str] = mapped_column(String(16), default="LATEST")
    judge_template_version: Mapped[int | None] = mapped_column(Integer)
    prompt_overrides: Mapped[dict | None] = mapped_column(JSON, comment="单用例局部覆写正文")

    # ---- 模型与裁决 ----
    simulator_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_config.id", ondelete="SET NULL"), nullable=True
    )
    judge_model_ids: Mapped[list | None] = mapped_column(JSON, comment="多裁判模型 id 列表")
    judge_strategy: Mapped[str] = mapped_column(String(16), default="AVG")
    pass_threshold: Mapped[float | None] = mapped_column(Float, comment="通过分数线")

    # ---- 版本 ----
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    revisions: Mapped[list[TestCaseRevision]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="noload"
    )


class TestCaseRevision(Base):
    """用例不可变版本快照（创建/编辑/导入/回滚）。"""

    __tablename__ = "test_case_revision"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_test_case_revision_version"),
        Index("ix_revision_case_created", "case_id", "created_at"),
        MYSQL_TABLE_OPTS,  # type: ignore[arg-type]
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False, default="UPDATE")
    operator: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    summary: Mapped[str | None] = mapped_column(String(512), comment="变更摘要")
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="完整用例配置快照")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    case: Mapped[TestCase] = relationship(back_populates="revisions")
