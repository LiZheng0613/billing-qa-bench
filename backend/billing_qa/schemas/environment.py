"""环境与被测接口 Schema。"""

from __future__ import annotations

import re
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
    # 明文值仅在调用方拥有 environment:secret_read 权限时返回，否则为 None。
    value_plain: str | None = None
    is_secret: bool
    description: str | None = None
    updated_at: datetime | None = None


class PreHookConfig(BaseModel):
    """前置接口配置：执行一次，提取变量供主请求与后续轮次复用。"""

    enabled: bool = False
    method: str = "POST"
    path: str = "/api/auth/token"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict | None = None
    # 多字段提取：变量名 -> JSONPath。配置后优先于下面的 token_jsonpath / token_var
    extract: dict[str, str] | None = None
    token_jsonpath: str = "$.data.token"
    token_var: str = "auth_token"
    cache_seconds: int = 300

    @field_validator("extract")
    @classmethod
    def _check_extract(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for key, path in value.items():
            if not pattern.match(str(key)):
                raise ValueError(f"前置接口提取变量名不合法: {key!r}")
            if not str(path or "").strip():
                raise ValueError(f"前置接口提取变量 {key} 缺少 JSONPath")
        return value


class BasicAuthIn(BaseModel):
    """环境级 Basic 鉴权：执行时生成 Authorization: Basic <base64(账号:密码)>。"""

    username: str = Field(min_length=1, max_length=256)
    password: str | None = Field(default=None, max_length=512)


class BasicAuthOut(BaseModel):
    username: str
    password_set: bool = False
    # 明文密码仅在调用方拥有 environment:secret_read 权限时返回。
    password: str | None = None


_CERT_PEM_MARKER = "-----BEGIN CERTIFICATE-----"
_KEY_PEM_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN ENCRYPTED PRIVATE KEY-----",
)


class MtlsAuthIn(BaseModel):
    """环境级 mTLS 双向证书：客户端证书 + 私钥，可选私钥口令与自定义 CA。

    留空表示沿用已保存的内容；无 environment:secret_read 权限时接口不回显敏感 PEM，
    有权限的账号编辑时可见明文原值。
    """

    client_cert_pem: str | None = Field(default=None, max_length=65536)
    client_key_pem: str | None = Field(default=None, max_length=65536)
    key_password: str | None = Field(default=None, max_length=512)
    ca_cert_pem: str | None = Field(default=None, max_length=65536)

    @field_validator("client_cert_pem", "ca_cert_pem")
    @classmethod
    def _check_cert_pem(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if text and _CERT_PEM_MARKER not in text:
            raise ValueError("证书内容需为 PEM 格式（包含 -----BEGIN CERTIFICATE-----）")
        return text or None

    @field_validator("client_key_pem")
    @classmethod
    def _check_key_pem(cls, value: str | None) -> str | None:
        text = (value or "").strip()
        if text and not any(marker in text for marker in _KEY_PEM_MARKERS):
            raise ValueError("私钥内容需为 PEM 格式（-----BEGIN ... PRIVATE KEY-----）")
        return text or None


class MtlsAuthOut(BaseModel):
    enabled: bool = False
    cert_set: bool = False
    key_set: bool = False
    key_password_set: bool = False
    ca_set: bool = False
    # 明文 PEM / 口令仅在调用方拥有 environment:secret_read 权限时返回。
    client_cert_pem: str | None = None
    client_key_pem: str | None = None
    key_password: str | None = None
    ca_cert_pem: str | None = None


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
    basic_auth: BasicAuthIn | None = None
    mtls: MtlsAuthIn | None = None
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
    basic_auth: BasicAuthIn | None = None
    mtls: MtlsAuthIn | None = None
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
    basic_auth: BasicAuthOut | None = None
    mtls: MtlsAuthOut | None = None
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
