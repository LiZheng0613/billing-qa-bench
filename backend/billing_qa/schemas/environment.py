"""环境与被测接口 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from billing_qa.schemas.common import ORMModel


class EnvVariableIn(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str | None = None
    is_secret: bool = False
    description: str | None = None

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError("变量名只能包含字母、数字与下划线")
        return value


class EnvVariableOut(BaseModel):
    id: int
    key: str
    value_masked: str
    is_secret: bool
    description: str | None = None
    updated_at: datetime | None = None


class PreHookConfig(BaseModel):
    """前置鉴权接口配置。"""

    enabled: bool = False
    method: str = "POST"
    path: str = "/api/auth/token"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict | None = None
    token_jsonpath: str = "$.data.token"
    token_var: str = "auth_token"
    cache_seconds: int = 300


DEFAULT_BODY_TEMPLATE: dict = {
    "query": "{{query}}",
    "session_id": "{{session_id}}",
    "history": "{{history}}",
}

DEFAULT_EXTRACTORS: dict = {
    "final_answer": "$.data.answer",
    "session_id": "$.data.session_id",
    "intent": "$.data.intent",
    "generated_sql": "$.data.generated_sql",
    "api_params": "$.data.api_params",
    "raw_db_data": "$.data.raw_db_data",
}


class EnvironmentBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    base_url: str = Field(min_length=1, max_length=512)
    health_path: str = "/health"
    api_method: str = "POST"
    api_path: str = "/api/chat"
    headers_template: dict[str, str] | None = None
    body_template: dict | None = Field(default_factory=lambda: dict(DEFAULT_BODY_TEMPLATE))
    is_stream: bool = False
    extractors: dict | None = Field(default_factory=lambda: dict(DEFAULT_EXTRACTORS))
    pre_hook: PreHookConfig | None = None
    session_mode: Literal["SERVER_RETURNED", "CLIENT_GENERATED"] = "SERVER_RETURNED"
    timeout_seconds: float = 60.0
    verify_ssl: bool = True
    is_active: bool = True

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return value.rstrip("/")

    @field_validator("api_method")
    @classmethod
    def _check_method(cls, value: str) -> str:
        method = value.upper()
        if method not in {"GET", "POST", "PUT", "PATCH"}:
            raise ValueError("仅支持 GET/POST/PUT/PATCH")
        return method


class EnvironmentCreate(EnvironmentBase):
    variables: list[EnvVariableIn] = Field(default_factory=list)


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    base_url: str | None = None
    health_path: str | None = None
    api_method: str | None = None
    api_path: str | None = None
    headers_template: dict[str, str] | None = None
    body_template: dict | None = None
    is_stream: bool | None = None
    extractors: dict | None = None
    pre_hook: PreHookConfig | None = None
    session_mode: Literal["SERVER_RETURNED", "CLIENT_GENERATED"] | None = None
    timeout_seconds: float | None = None
    verify_ssl: bool | None = None
    is_active: bool | None = None


class EnvironmentOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None
    base_url: str
    health_path: str
    api_method: str
    api_path: str
    headers_template: dict | None
    body_template: dict | None
    is_stream: bool
    extractors: dict | None
    pre_hook: dict | None
    session_mode: str
    timeout_seconds: float
    verify_ssl: bool
    is_active: bool
    last_health_status: str | None
    last_health_latency_ms: float | None
    last_health_at: datetime | None
    last_health_message: str | None
    created_at: datetime
    updated_at: datetime
    variables: list[EnvVariableOut] = Field(default_factory=list)


class HealthCheckResult(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    message: str
    checked_at: datetime

    model_config = {"from_attributes": True}


class EnvironmentUsage(BaseModel):
    environment_id: int
    case_count: int
    task_count: int


class VariableBatchUpdate(BaseModel):
    variables: list[EnvVariableIn]

    @model_validator(mode="after")
    def _unique_keys(self) -> VariableBatchUpdate:
        keys = [item.key for item in self.variables]
        if len(keys) != len(set(keys)):
            raise ValueError("变量名重复")
        return self


class EnvPreviewRequest(BaseModel):
    """请求报文预览：选一条示例用例，带入其请求变量与会话绑定配置。"""

    case_id: int = Field(ge=1, description="示例用例 id")


class EnvPreviewResponse(BaseModel):
    """渲染后的请求报文与占位符来源标注（只读，不落库）。"""

    headers: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    origins: dict[str, str] = Field(default_factory=dict)
    session_mode_resolved: str
    session_id: str | None = None
    query: str | None = None
    warnings: list[str] = Field(default_factory=list)
