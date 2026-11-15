"""LLM 模型注册（模拟器 / 裁判）。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import Base, TimestampMixin
from billing_qa.models.enums import ModelConnectionType


class ModelConfig(Base, TimestampMixin):
    """模型配置，支持 OpenAI 兼容直连与 OpenCode Serve 两种接入方式。"""

    __tablename__ = "model_config"
    __table_args__ = (
        Index("ix_model_config_code", "code", unique=True),
        Index("ix_model_config_role", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="模型编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="展示名称")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="JUDGE", comment="SIMULATOR/JUDGE")
    connection_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ModelConnectionType.OPENAI_COMPATIBLE.value,
        comment="OPENAI_COMPATIBLE / OPENCODE_SERVE",
    )
    provider: Mapped[str] = mapped_column(String(64), default="openai-compatible")
    base_url: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="OpenAI 兼容 base_url；OpenCode 模式存 serve 根地址"
    )
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, comment="API Key（加密）")
    serve_url: Mapped[str | None] = mapped_column(
        String(512), comment="OpenCode Serve 地址，例如 http://127.0.0.1"
    )
    serve_port: Mapped[int | None] = mapped_column(
        Integer, default=4096, comment="OpenCode Serve 端口"
    )
    serve_username: Mapped[str | None] = mapped_column(
        String(128), comment="OpenCode Serve Basic Auth 用户名"
    )
    serve_password_encrypted: Mapped[str | None] = mapped_column(
        Text, comment="OpenCode Serve Basic Auth 密码（加密）"
    )
    opencode_agent: Mapped[str | None] = mapped_column(String(128), comment="OpenCode agent ID")
    opencode_model: Mapped[str | None] = mapped_column(
        String(128), comment="OpenCode 模型，格式 provider/model"
    )
    opencode_directory: Mapped[str | None] = mapped_column(
        String(512), comment="OpenCode session location.directory"
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="模型名")
    # 裁判默认 0 消除波动，模拟器默认 0.7 保持多样性；NULL 表示按角色取默认值
    temperature: Mapped[float | None] = mapped_column(Float, comment="采样温度")
    top_p: Mapped[float | None] = mapped_column(Float, comment="top_p")
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048, comment="最大输出 token")
    timeout_seconds: Mapped[float] = mapped_column(Float, default=60.0, comment="单次请求超时")
    rpm_limit: Mapped[int] = mapped_column(Integer, default=60, comment="每分钟请求上限")
    max_retries: Mapped[int] = mapped_column(Integer, default=3, comment="失败重试次数")
    response_format_json: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="强制 JSON Structured Output"
    )
    extra_params: Mapped[dict | None] = mapped_column(JSON, comment="透传额外参数")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def effective_temperature(self) -> float:
        if self.temperature is not None:
            return self.temperature
        return 0.7 if self.role == "SIMULATOR" else 0.0

    @property
    def is_opencode(self) -> bool:
        return self.connection_type == ModelConnectionType.OPENCODE_SERVE.value
