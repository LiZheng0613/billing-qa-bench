"""Prompt 资产中心：双轨模板 + 版本管理。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from billing_qa.db.base import Base, TimestampMixin, now


class PromptTemplate(Base, TimestampMixin):
    """模板主体，指向当前最新版本。"""

    __tablename__ = "prompt_template"
    __table_args__ = (
        Index("ix_prompt_template_code", "code", unique=True),
        Index("ix_prompt_template_type", "template_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="模板编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模板名称")
    template_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="USER_SIMULATOR / LLM_JUDGE"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="模板说明")
    current_version: Mapped[int] = mapped_column(Integer, default=1, comment="当前最新版本号")
    ref_count: Mapped[int] = mapped_column(Integer, default=0, comment="被用例引用计数")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否内置模板")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))

    versions: Mapped[list[PromptTemplateVersion]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PromptTemplateVersion.version.desc()",
    )


class PromptTemplateVersion(Base):
    """不可变版本内容。"""

    __tablename__ = "prompt_template_version"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_prompt_template_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("prompt_template.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="模板正文")
    variables: Mapped[list | None] = mapped_column(JSON, comment="声明的变量列表")
    changelog: Mapped[str | None] = mapped_column(String(512), comment="版本变更说明")
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    template: Mapped[PromptTemplate] = relationship(back_populates="versions")
