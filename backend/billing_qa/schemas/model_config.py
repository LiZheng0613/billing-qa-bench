"""模型注册 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from billing_qa.models.enums import ModelRole
from billing_qa.schemas.common import ORMModel


class ModelConfigBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    role: ModelRole
    provider: str = "openai-compatible"
    base_url: str = Field(min_length=1, max_length=512)
    model_name: str = Field(min_length=1, max_length=128)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int = Field(default=2048, ge=1, le=131072)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    rpm_limit: int = Field(default=60, ge=1, le=6000)
    max_retries: int = Field(default=3, ge=0, le=10)
    response_format_json: bool = True
    extra_params: dict | None = None
    is_active: bool = True

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return value.rstrip("/")


class ModelConfigCreate(ModelConfigBase):
    api_key: str | None = None


class ModelConfigUpdate(BaseModel):
    name: str | None = None
    role: ModelRole | None = None
    provider: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    timeout_seconds: float | None = None
    rpm_limit: int | None = None
    max_retries: int | None = None
    response_format_json: bool | None = None
    extra_params: dict | None = None
    is_active: bool | None = None


class ModelConfigOut(ORMModel):
    id: int
    code: str
    name: str
    role: str
    provider: str
    base_url: str
    model_name: str
    api_key_masked: str = ""
    temperature: float | None
    effective_temperature: float
    top_p: float | None
    max_tokens: int
    timeout_seconds: float
    rpm_limit: int
    max_retries: int
    response_format_json: bool
    extra_params: dict | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ModelTestResult(BaseModel):
    ok: bool
    latency_ms: float | None = None
    content: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    message: str
