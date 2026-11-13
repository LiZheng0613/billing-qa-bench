"""LLM 模型注册（模拟器 / 裁判）。"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import MYSQL_TABLE_OPTS, Base, TimestampMixin


class ModelConfig(Base, TimestampMixin):
    """OpenAI 兼容协议模型配置。"""

    __tablename__ = "model_config"
    __table_args__ = (
        Index("ix_model_config_code", "code", unique=True),
        Index("ix_model_config_role", "role"),
        MYSQL_TABLE_OPTS,  # type: ignore[arg-type]
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="模型编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="展示名称")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="JUDGE", comment="SIMULATOR/JUDGE")
    provider: Mapped[str] = mapped_column(String(64), default="openai-compatible")
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, comment="OpenAI 兼容 base_url")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, comment="API Key（加密）")
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
