"""环境与被测接口配置。"""

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

from billing_qa.db.base import Base, TimestampMixin


class Environment(Base, TimestampMixin):
    """被测问账系统环境实例。"""

    __tablename__ = "environment"
    __table_args__ = (
        Index("ix_environment_code", "code", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="环境编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="环境名称")
    description: Mapped[str | None] = mapped_column(Text, comment="环境说明")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, comment="被测系统基础地址")
    health_path: Mapped[str] = mapped_column(String(256), default="/health", comment="探活路径")
    api_method: Mapped[str] = mapped_column(String(16), default="POST", comment="对话接口方法")
    api_path: Mapped[str] = mapped_column(String(256), default="/api/chat", comment="对话接口路径")
    headers_template: Mapped[dict | None] = mapped_column(JSON, comment="自定义请求头模板")
    body_template: Mapped[dict | None] = mapped_column(JSON, comment="请求体模板，支持变量占位符")
    is_stream: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否 SSE 流式")
    extractors: Mapped[dict | None] = mapped_column(JSON, comment="JSONPath 白盒提取映射")
    pre_hook: Mapped[dict | None] = mapped_column(JSON, comment="前置接口钩子配置")
    basic_auth: Mapped[dict | None] = mapped_column(
        JSON, comment="Basic 鉴权配置（username + password_encrypted）"
    )
    session_mode: Mapped[str] = mapped_column(
        String(24),
        default="SERVER_RETURNED",
        comment="会话绑定方式：SERVER_RETURNED / CLIENT_GENERATED",
    )
    timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0, comment="接口超时")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True, comment="校验 TLS")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    last_health_status: Mapped[str | None] = mapped_column(String(16), comment="最近探活状态")
    last_health_latency_ms: Mapped[float | None] = mapped_column(Float, comment="最近探活耗时")
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近探活时间")
    last_health_message: Mapped[str | None] = mapped_column(Text, comment="最近探活详情")

    variables: Mapped[list[EnvironmentVariable]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EnvironmentVariable.id",
    )


class EnvironmentVariable(Base, TimestampMixin):
    """环境级全局变量池（敏感值加密存储）。"""

    __tablename__ = "environment_variable"
    __table_args__ = (
        UniqueConstraint("environment_id", "key", name="uq_env_variable_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment_id: Mapped[int] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False, comment="变量名")
    value_encrypted: Mapped[str | None] = mapped_column(Text, comment="变量值（加密）")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否敏感")
    description: Mapped[str | None] = mapped_column(String(256), comment="说明")

    environment: Mapped[Environment] = relationship(back_populates="variables")
