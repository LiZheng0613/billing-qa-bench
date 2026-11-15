"""模型注册 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from billing_qa.models.enums import ModelConnectionType, ModelRole
from billing_qa.schemas.common import ORMModel


def normalize_http_url(value: str | None) -> str | None:
    """校验并规范化 http(s) 地址；空值返回 None。"""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not text.startswith(("http://", "https://")):
        raise ValueError("地址必须以 http:// 或 https:// 开头")
    return text.rstrip("/")


class ModelConfigBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    role: ModelRole
    connection_type: ModelConnectionType = ModelConnectionType.OPENAI_COMPATIBLE
    provider: str = "openai-compatible"
    base_url: str | None = Field(default=None, max_length=512)
    serve_url: str | None = Field(default=None, max_length=512)
    serve_port: int | None = Field(default=4096, ge=1, le=65535)
    serve_username: str | None = Field(default=None, max_length=128)
    opencode_agent: str | None = Field(default=None, max_length=128)
    opencode_model: str | None = Field(default=None, max_length=128)
    opencode_directory: str | None = Field(default=None, max_length=512)
    model_name: str | None = Field(default=None, max_length=128)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int = Field(default=2048, ge=1, le=131072)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    rpm_limit: int = Field(default=60, ge=1, le=6000)
    max_retries: int = Field(default=3, ge=0, le=10)
    response_format_json: bool = True
    extra_params: dict | None = None
    is_active: bool = True

    @field_validator("base_url", "serve_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return normalize_http_url(value)

    @model_validator(mode="after")
    def _check_connection(self) -> ModelConfigBase:
        if self.connection_type == ModelConnectionType.OPENCODE_SERVE:
            if not self.serve_url:
                raise ValueError("OpenCode Serve 接入需填写 serve_url")
            if self.serve_port is None:
                raise ValueError("OpenCode Serve 接入需填写 serve_port")
        else:
            if not self.base_url:
                raise ValueError("OpenAI 兼容接入需填写 base_url")
            if not self.model_name:
                raise ValueError("OpenAI 兼容接入需填写 model_name")
        return self


class ModelConfigCreate(ModelConfigBase):
    api_key: str | None = None
    serve_password: str | None = None


class ModelConfigUpdate(BaseModel):
    name: str | None = None
    role: ModelRole | None = None
    connection_type: ModelConnectionType | None = None
    provider: str | None = None
    base_url: str | None = None
    serve_url: str | None = None
    serve_port: int | None = Field(default=None, ge=1, le=65535)
    serve_username: str | None = None
    serve_password: str | None = None
    opencode_agent: str | None = None
    opencode_model: str | None = None
    opencode_directory: str | None = None
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

    @field_validator("base_url", "serve_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return normalize_http_url(value)


class ModelConfigOut(ORMModel):
    id: int
    code: str
    name: str
    role: str
    connection_type: str
    provider: str
    base_url: str
    model_name: str
    serve_url: str | None = None
    serve_port: int | None = None
    serve_username: str | None = None
    serve_password_masked: str = ""
    opencode_agent: str | None = None
    opencode_model: str | None = None
    opencode_directory: str | None = None
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
    connection_type: str | None = None
    latency_ms: float | None = None
    content: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    message: str


class OpenCodeCatalogRequest(BaseModel):
    """拉取 OpenCode 可用模型/Agent 的入参。

    ``model_id`` 用于编辑已有模型时复用已保存的密码：前端留空密码也能刷新列表。
    """

    serve_url: str | None = Field(default=None, max_length=512)
    # None 表示沿用模型已保存的端口；独立调用时 endpoint 回退到 4096。
    serve_port: int | None = Field(default=None, ge=1, le=65535)
    serve_username: str | None = Field(default=None, max_length=128)
    serve_password: str | None = None
    opencode_directory: str | None = Field(default=None, max_length=512)
    model_id: int | None = None
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)

    @field_validator("serve_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return normalize_http_url(value)


class OpenCodeCatalogModelOut(BaseModel):
    value: str
    provider_id: str
    model_id: str
    name: str
    status: str | None = None
    enabled: bool = True
    context_limit: int | None = None
    output_limit: int | None = None


class OpenCodeCatalogAgentOut(BaseModel):
    id: str
    name: str
    mode: str | None = None
    hidden: bool = False


class OpenCodeCatalogOut(BaseModel):
    serve_url: str
    models: list[OpenCodeCatalogModelOut]
    agents: list[OpenCodeCatalogAgentOut]

