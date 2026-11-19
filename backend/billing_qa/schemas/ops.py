"""运营与取数资产相关 Schema（候选池 / 判分器自检 / 口径 / 数据源 / 能力）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from billing_qa.schemas.common import ORMModel


# ---------------------------------------------------------------------- #
# 候选池
# ---------------------------------------------------------------------- #
class CandidateCreate(BaseModel):
    source: Literal["PRODUCTION", "TRACE", "LLM", "MANUAL"] = "MANUAL"
    raw_question: str = Field(min_length=1)
    context: dict[str, Any] | None = None
    trace_uid: str | None = Field(default=None, max_length=36)
    environment_id: int | None = None
    draft: dict[str, Any] | None = None
    dedupe_hash: str | None = Field(default=None, max_length=64)
    note: str | None = None


class CandidateUpdate(BaseModel):
    status: Literal["NEW", "TRIAGED", "PROMOTED", "REJECTED"] | None = None
    note: str | None = None
    draft: dict[str, Any] | None = None


class CandidatePromoteRequest(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    case: dict[str, Any] | None = Field(default=None, description="覆盖候选草稿的用例字段")


class CandidateOut(ORMModel):
    id: int
    source: str
    raw_question: str
    context: dict[str, Any] | None
    trace_uid: str | None
    environment_id: int | None
    draft: dict[str, Any] | None
    status: str
    dedupe_hash: str | None
    note: str | None
    reviewed_by: str | None
    promoted_case_id: int | None
    created_at: datetime
    updated_at: datetime


class CandidatePromoteOut(BaseModel):
    candidate_id: int
    case_id: int
    case_code: str


# ---------------------------------------------------------------------- #
# 判分器自检
# ---------------------------------------------------------------------- #
class GraderEvalRunRequest(BaseModel):
    snapshot: dict[str, Any] | None = None
    correct_answer: str | None = None
    dataset_version: str | None = Field(default=None, max_length=32)


class GraderEvalOut(ORMModel):
    id: int
    dataset_version: str | None
    grader_version: str
    mutant_kind: str
    total: int
    detected: int
    false_kills: int
    run_at: datetime
    details: dict[str, Any] | None


# ---------------------------------------------------------------------- #
# 业务口径
# ---------------------------------------------------------------------- #
class CaliberCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    definition: str | None = None
    canonical_source: Literal["BUSINESS_UI", "BILLING_SYSTEM", "CRM", "DATA_WAREHOUSE"] = (
        "BUSINESS_UI"
    )
    owner: str | None = Field(default=None, max_length=64)


class CaliberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    definition: str | None = None
    canonical_source: str | None = None
    owner: str | None = None


class CaliberOut(ORMModel):
    id: int
    key: str
    name: str
    definition: str | None
    canonical_source: str
    owner: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------- #
# 数据源
# ---------------------------------------------------------------------- #
class DataSourceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["CYPHER_TOOL", "SQL_DB", "HTTP_API"] = "CYPHER_TOOL"
    endpoint_url: str | None = Field(default=None, max_length=512)
    description: str | None = None
    auth_config: dict[str, Any] | None = None
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    max_rows: int = Field(default=200, ge=1, le=10000)
    tool_version: str | None = Field(default=None, max_length=32)
    is_active: bool = True


class DataSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal["CYPHER_TOOL", "SQL_DB", "HTTP_API"] | None = None
    endpoint_url: str | None = Field(default=None, max_length=512)
    description: str | None = None
    auth_config: dict[str, Any] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)
    max_rows: int | None = Field(default=None, ge=1, le=10000)
    tool_version: str | None = Field(default=None, max_length=32)
    is_active: bool | None = None


class DataSourceOut(ORMModel):
    id: int
    code: str
    name: str
    kind: str
    endpoint_url: str | None
    description: str | None
    has_auth: bool = False
    # 鉴权配置仅在调用方拥有 capability:write 权限时返回明文。
    auth_config: dict[str, Any] | None = None
    timeout_seconds: float
    max_rows: int
    tool_version: str | None
    is_active: bool
    last_probe_status: str | None
    last_probe_latency_ms: float | None
    last_probe_at: datetime | None
    last_probe_message: str | None
    created_at: datetime
    updated_at: datetime


class ProbeResult(BaseModel):
    ok: bool
    status: str
    latency_ms: float | None = None
    message: str
    meta: dict[str, Any] | None = None


# ---------------------------------------------------------------------- #
# 数据能力
# ---------------------------------------------------------------------- #
class CapabilityCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["CYPHER", "SQL", "API"] = "CYPHER"
    description: str | None = None
    source_id: int | None = None
    query_text: str | None = None
    params_schema: list[dict[str, Any]] | None = None
    param_binding: dict[str, str] | None = None
    intent_patterns: list[str] | None = None
    lookup_queries: dict[str, str] | None = None


class CapabilityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal["CYPHER", "SQL", "API"] | None = None
    description: str | None = None
    source_id: int | None = None
    query_text: str | None = None
    params_schema: list[dict[str, Any]] | None = None
    param_binding: dict[str, str] | None = None
    intent_patterns: list[str] | None = None
    lookup_queries: dict[str, str] | None = None
    status: Literal["DRAFT", "CERTIFIED", "DEPRECATED"] | None = None
    is_active: bool | None = None


class CapabilityOut(ORMModel):
    id: int
    key: str
    name: str
    kind: str
    description: str | None
    source_id: int | None
    query_text: str | None
    params_schema: list[dict[str, Any]] | None
    param_binding: dict[str, Any] | None
    intent_patterns: list[str] | None
    lookup_queries: dict[str, Any] | None
    version: int
    status: str
    cert_result: dict[str, Any] | None
    certified_at: datetime | None
    certified_by: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CapabilityExecuteRequest(BaseModel):
    case_id: int
    params: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    fact_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    max_rows: int | None = Field(default=None, ge=1, le=10000)
    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


class GoldQueryOut(ORMModel):
    id: int
    case_id: int
    capability_id: int | None
    capability_version: int | None
    origin: str
    fact_ids: list[str] | None
    template_params: dict[str, Any] | None
    query_text: str | None
    columns: list[str] | None
    rows: list[dict[str, Any]] | None
    row_count: int
    truncated: bool
    executor_version: str | None
    executed_at: datetime
    is_active: bool


class CapabilityExecuteResult(BaseModel):
    capability_id: int
    capability_key: str
    execution: dict[str, Any]
    gold_query: GoldQueryOut


# ---------------------------------------------------------------------- #
# 事实级统计
# ---------------------------------------------------------------------- #
class FactStatItem(BaseModel):
    fact_id: str
    label: str | None
    total: int
    passed: int
    pass_rate: float
    last_run_at: datetime | None = None


class FactStatsOverview(BaseModel):
    execution_total: int
    passed: int
    pass_rate: float
    lucky_pass: int
    avg_score: float
    fact_total: int
    fact_passed: int
    fact_pass_rate: float
    top_failed: list[FactStatItem]


class DatasetStatItem(BaseModel):
    dataset_version: str | None
    grading_mode: str
    review_status: str
    total: int
