"""Prompt 资产中心 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from billing_qa.models.enums import TemplateType
from billing_qa.schemas.common import ORMModel


class PromptVersionCreate(BaseModel):
    content: str = Field(min_length=10)
    changelog: str | None = Field(default=None, max_length=512)


class PromptCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=128)
    template_type: TemplateType
    description: str | None = None
    content: str = Field(min_length=10)
    changelog: str | None = "初始版本"


class PromptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    is_active: bool | None = None


class PromptVersionOut(ORMModel):
    id: int
    version: int
    content: str
    variables: list[str] | None
    changelog: str | None
    created_by: str | None
    created_at: datetime


class PromptOut(ORMModel):
    id: int
    code: str
    name: str
    template_type: str
    description: str | None
    current_version: int
    ref_count: int
    is_builtin: bool
    is_active: bool
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    versions: list[PromptVersionOut] = Field(default_factory=list)


class PromptListItem(ORMModel):
    id: int
    code: str
    name: str
    template_type: str
    description: str | None
    current_version: int
    ref_count: int
    is_builtin: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PromptUsageCase(BaseModel):
    id: int
    code: str
    name: str
    strategy: str
    pinned_version: int | None = None


class PromptUsage(BaseModel):
    template_id: int
    ref_count: int
    cases: list[PromptUsageCase]
    can_delete: bool
    delete_blockers: list[str]


class SandboxRequest(BaseModel):
    mode: Literal["SIMULATOR", "JUDGE"]
    execution_id: int | None = Field(default=None, description="历史执行快照 id")
    variables: dict[str, Any] | None = Field(default=None, description="额外/覆盖变量")
    model_id: int | None = Field(default=None, description="指定模型；缺省用角色默认模型")
    content_override: str | None = Field(default=None, description="临时覆写模板正文")
    temperature: float | None = Field(default=None, ge=0, le=2)


class SandboxResponse(BaseModel):
    rendered_prompt: str
    output: dict[str, Any] | None
    raw_content: str
    missing_variables: list[str]
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    model_name: str
