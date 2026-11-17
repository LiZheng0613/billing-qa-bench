"""测试用例与变更审计 Schema。"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from billing_qa.models.enums import (
    CaseSessionMode,
    CaseType,
    FactKind,
    GradingMode,
    InputType,
    JudgeStrategy,
    TemplateStrategy,
)
from billing_qa.schemas.common import ORMModel


def _fact_id() -> str:
    return f"f{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------- #
# 用例级请求变量校验
# ---------------------------------------------------------------------- #
_REQUEST_VARIABLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 运行时由执行器注入，用例自定义同名变量会被无条件覆盖，因此直接拒绝以免静默失效
_RESERVED_REQUEST_VARIABLES = {"query", "history", "history_text", "turn"}
_REQUEST_VARIABLES_LIMIT = 16 * 1024


def validate_request_variables(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """校验用例级请求变量：命名合法、避开运行时保留字、体积可控。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("请求变量必须是对象（key-value 映射）")
    for key in value:
        if not isinstance(key, str) or not _REQUEST_VARIABLE_KEY.match(key):
            raise ValueError(f"请求变量名不合法: {key!r}（需匹配字母/数字/下划线且不以数字开头）")
        if key in _RESERVED_REQUEST_VARIABLES:
            raise ValueError(
                f"请求变量名 {key} 由执行器运行时注入，请勿自定义（可用运行时变量清单见帮助）"
            )
    serialized = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    if len(serialized) > _REQUEST_VARIABLES_LIMIT:
        raise ValueError("请求变量体积超过 16KB，请精简取值")
    return value


class NumericExpectation(BaseModel):
    label: str = Field(default="数值")
    value: float
    tolerance: float = Field(default=0.0, ge=0)
    aliases: list[str] = Field(
        default_factory=list, description="业务名称别名，支持中英文（如 total_amount）"
    )
    require_name: bool = Field(
        default=False, description="为 true 时，答案必须出现名称且与数值同句绑定"
    )


class RuleConfig(BaseModel):
    """规则硬校验配置。"""

    model_config = ConfigDict(extra="allow")

    amounts: list[NumericExpectation] = Field(default_factory=list)
    counts: list[NumericExpectation] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    must_contain: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)


class TurnItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int | None = None
    query: str = Field(min_length=1)
    expected_answer: str | None = None
    expected_rules: RuleConfig | None = None
    notes: str | None = None


# ---------------------------------------------------------------------- #
# 答案契约判分（P0）
# ---------------------------------------------------------------------- #
FactVerificationStatus = Literal[
    "PENDING", "VERIFIED", "CONFLICT", "NO_EVIDENCE", "WAIVED", "STALE"
]


class FactAtom(BaseModel):
    """可机械判定的最小事实单元（业务语言表达）。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=_fact_id, min_length=1, max_length=64)
    kind: FactKind = FactKind.SCALAR
    label: str | None = Field(default=None, max_length=200)
    value: float | str | bool | None = None
    values: list[str] = Field(default_factory=list)
    unit: str | None = Field(default=None, max_length=16)
    tolerance: float = Field(default=0.0, ge=0)
    time_scope: str | None = None
    matcher: str | None = None
    evidence_required: bool = True
    derived_from: list[str] = Field(default_factory=list)
    formula: str | None = None
    keywords: list[str] = Field(default_factory=list, description="boolean/text 类事实的语义线索")
    aliases: list[str] = Field(
        default_factory=list,
        description="业务名称别名，支持中英文 key（如 total_amount / 账单合计）",
    )
    require_name: bool = Field(
        default=False, description="为 true 时，答案必须出现名称且与数值同句绑定"
    )

    # ---- 取证与验证（存于 JSON，不单独建表）----
    verification_status: FactVerificationStatus = Field(
        default="PENDING", description="PENDING/VERIFIED/CONFLICT/NO_EVIDENCE/WAIVED/STALE"
    )
    verified_by: str | None = Field(default=None, max_length=64)
    verified_at: str | None = Field(default=None, max_length=40)
    verification_note: str | None = Field(default=None, max_length=512)
    evidence_ref: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _validate_value(self) -> FactAtom:
        if self.kind in {FactKind.SET, FactKind.TOPK} and not self.values:
            raise ValueError(f"集合类事实 {self.id} 必须提供 values")
        if self.kind in {FactKind.SCALAR, FactKind.DATE} and self.value in (None, ""):
            raise ValueError(f"事实 {self.id} 必须提供 value")
        if self.kind == FactKind.SCALAR and isinstance(self.value, str):
            raise ValueError(f"标量事实 {self.id} 的 value 必须是数值")
        return self


class AnswerEnvelope(BaseModel):
    """答案接受包络：Must 必答 / May 允许扩展 / MustNot 禁止出现。"""

    must: list[FactAtom] = Field(default_factory=list)
    may: list[FactAtom] = Field(default_factory=list)
    must_not: list[FactAtom] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    tolerances: dict[str, float] = Field(
        default_factory=lambda: {"money": 0.01, "percent": 0.1}
    )
    extras_policy: Literal["strict", "lenient"] = "lenient"
    clarity_rules: dict[str, Any] = Field(
        default_factory=lambda: {"conclusion_first": True, "max_ratio": 3.0}
    )

    @model_validator(mode="after")
    def _no_conflict(self) -> AnswerEnvelope:
        must_ids = {item.id for item in self.must}
        may_ids = {item.id for item in self.may}
        banned = {item.id for item in self.must_not}
        overlap = (must_ids | may_ids) & banned
        if overlap:
            raise ValueError(f"事实不能同时出现在 Must/May 与 MustNot: {sorted(overlap)}")
        return self


class PlanSpec(BaseModel):
    """取数计划：只做诊断能力覆盖，不参与正确性判定。"""

    required_capabilities: list[str] = Field(default_factory=list)
    alternatives: dict[str, list[str]] = Field(default_factory=dict)
    max_tool_calls: int | None = Field(default=None, ge=1)
    allow_parallel: bool = True


class ClarifySpec(BaseModel):
    must_ask_slot: list[str] = Field(default_factory=list)
    must_not_assert: list[str] = Field(default_factory=list)


class AssumptionScenario(BaseModel):
    assumption: str = Field(min_length=1)
    must: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _must_required(self) -> AssumptionScenario:
        if not self.must:
            raise ValueError("每个假设场景必须给出对应的 Must 事实")
        return self


class AssumptionSpec(BaseModel):
    must_state_assumption: bool = True
    scenarios: list[AssumptionScenario] = Field(default_factory=list)


class PartialSpec(BaseModel):
    must_disclose: bool = True
    required_facts: list[str] = Field(default_factory=list)


class RefuseSpec(BaseModel):
    must_reason: bool = True


class ResponseContract(BaseModel):
    """输入不清晰时的「正确行为」契约。"""

    allowed: list[Literal["clarify", "answer_with_assumption", "partial", "refuse"]] = (
        Field(default_factory=list)
    )
    clarify: ClarifySpec | None = None
    answer_with_assumption: AssumptionSpec | None = None
    partial: PartialSpec | None = None
    refuse: RefuseSpec | None = None

    @model_validator(mode="after")
    def _at_least_two(self) -> ResponseContract:
        if len(self.allowed) < 2:
            raise ValueError("歧义/非清晰用例至少需要两种可接受行为")
        for name in self.allowed:
            if getattr(self, name) is None:
                raise ValueError(f"允许行为 {name} 缺少对应配置")
        assumption = self.answer_with_assumption
        if assumption is not None and not assumption.scenarios:
            raise ValueError("answer_with_assumption 至少需要一个假设场景")
        return self


class HiddenContextItem(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    release_hint: str | None = Field(default=None, description="触发释放的语义提示")


class CaseBase(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    case_type: CaseType
    status: str = "ACTIVE"
    priority: str = "P1"
    tags: list[str] = Field(default_factory=list)
    environment_id: int | None = None

    # ---- 请求变量与会话绑定 ----
    request_variables: dict[str, Any] = Field(
        default_factory=dict,
        description="渲染进请求头/请求体模板的用例级变量，值支持任意 JSON 类型",
    )
    session_mode: CaseSessionMode = Field(
        default=CaseSessionMode.INHERIT,
        description="会话绑定方式：INHERIT 跟随环境；FIXED 由 request_variables.session_id 表达",
    )

    query: str | None = None
    expected_answer: str | None = None
    expected_rules: RuleConfig | None = None

    turns: list[TurnItem] | None = None
    session_expected: dict[str, Any] | None = None
    fail_fast: bool = False

    user_goal: str | None = None
    initial_query: str | None = None
    persona_description: str | None = None
    hidden_context: list[HiddenContextItem] | None = None
    max_turns: int = Field(default=5, ge=1, le=20)
    terminate_conditions: dict[str, Any] | None = None

    simulator_template_id: int | None = None
    simulator_template_strategy: TemplateStrategy = TemplateStrategy.LATEST
    simulator_template_version: int | None = None
    judge_template_id: int | None = None
    judge_template_strategy: TemplateStrategy = TemplateStrategy.LATEST
    judge_template_version: int | None = None
    prompt_overrides: dict[str, str] | None = None

    simulator_model_id: int | None = None
    judge_model_ids: list[int] = Field(default_factory=list)
    judge_strategy: JudgeStrategy = JudgeStrategy.AVG
    pass_threshold: float | None = Field(default=None, ge=0, le=100)

    # ---- 答案契约判分 ----
    grading_mode: GradingMode = GradingMode.LEGACY
    input_type: InputType = InputType.CLEAR
    as_of: str | None = None
    snapshot_id: str | None = None
    answer_envelope: AnswerEnvelope | None = None
    response_contract: ResponseContract | None = None
    plan_spec: PlanSpec | None = None
    risk_level: Literal["LOW", "HIGH"] = "LOW"
    owner: str | None = None
    review_status: str = "NOT_REQUIRED"
    dataset_version: str | None = "v1"
    dedupe_hash: str | None = None

    @field_validator("request_variables")
    @classmethod
    def _check_request_variables(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        return validate_request_variables(value) or {}

    @model_validator(mode="after")
    def _validate_by_type(self) -> CaseBase:
        if self.case_type == CaseType.SINGLE:
            if not self.query:
                raise ValueError("单轮用例必须填写 query")
        elif self.case_type == CaseType.STATIC_MULTI:
            if not self.turns:
                raise ValueError("静态多轮用例必须填写至少一轮 turns")
            for idx, turn in enumerate(self.turns, start=1):
                turn.index = idx
        elif self.case_type == CaseType.DYNAMIC_MULTI:
            if not self.user_goal:
                raise ValueError("动态多轮用例必须填写 user_goal")
            if self.max_turns < 1:
                raise ValueError("max_turns 至少为 1")
        for strategy, version, label in (
            (self.simulator_template_strategy, self.simulator_template_version, "模拟器模板"),
            (self.judge_template_strategy, self.judge_template_version, "裁判模板"),
        ):
            if strategy == TemplateStrategy.PINNED and not version:
                raise ValueError(f"{label}选择 PINNED 策略时必须指定版本号")
        self._validate_answer_contract()
        return self

    def _validate_answer_contract(self) -> None:
        if self.grading_mode != GradingMode.ANSWER_CONTRACT:
            return
        if self.answer_envelope is None or not self.answer_envelope.must:
            raise ValueError("契约模式必须至少填写一条 Must 事实")
        if not self.as_of:
            raise ValueError("契约模式必须冻结 as_of（账期/日期）")
        if self.input_type != InputType.CLEAR:
            if self.response_contract is None:
                raise ValueError("非清晰输入必须提供响应契约")
            if self.risk_level == "HIGH" and self.review_status == "NOT_REQUIRED":
                self.review_status = "PENDING"
        if self.risk_level == "HIGH" and self.review_status == "NOT_REQUIRED":
            self.review_status = "PENDING"


class CaseCreate(CaseBase):
    pass


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    tags: list[str] | None = None
    environment_id: int | None = None
    request_variables: dict[str, Any] | None = None
    session_mode: CaseSessionMode | None = None
    query: str | None = None
    expected_answer: str | None = None
    expected_rules: RuleConfig | None = None
    turns: list[TurnItem] | None = None
    session_expected: dict[str, Any] | None = None
    fail_fast: bool | None = None
    user_goal: str | None = None
    initial_query: str | None = None
    persona_description: str | None = None
    hidden_context: list[HiddenContextItem] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=20)
    terminate_conditions: dict[str, Any] | None = None
    simulator_template_id: int | None = None
    simulator_template_strategy: TemplateStrategy | None = None
    simulator_template_version: int | None = None
    judge_template_id: int | None = None
    judge_template_strategy: TemplateStrategy | None = None
    judge_template_version: int | None = None
    prompt_overrides: dict[str, str] | None = None
    simulator_model_id: int | None = None
    judge_model_ids: list[int] | None = None
    judge_strategy: JudgeStrategy | None = None
    pass_threshold: float | None = Field(default=None, ge=0, le=100)
    grading_mode: GradingMode | None = None
    input_type: InputType | None = None
    as_of: str | None = None
    snapshot_id: str | None = None
    answer_envelope: AnswerEnvelope | None = None
    response_contract: ResponseContract | None = None
    plan_spec: PlanSpec | None = None
    risk_level: Literal["LOW", "HIGH"] | None = None
    owner: str | None = None
    review_status: str | None = None
    dataset_version: str | None = None
    dedupe_hash: str | None = None
    change_summary: str | None = Field(default=None, max_length=512)

    @field_validator("request_variables")
    @classmethod
    def _check_request_variables(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_request_variables(value)


class FactVerifyRequest(BaseModel):
    """单条事实的取证结论。"""

    verification_status: FactVerificationStatus = "VERIFIED"
    note: str | None = Field(default=None, max_length=512)
    evidence_ref: str | None = Field(default=None, max_length=256)


class CaseReviewRequest(BaseModel):
    review_status: Literal["PENDING", "APPROVED", "REJECTED"]
    note: str | None = Field(default=None, max_length=512)


class CaseFreezeRequest(BaseModel):
    dataset_version: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=512)


class FactAtomView(FactAtom):
    section: Literal["must", "may", "must_not"]


class CaseOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None
    case_type: str
    status: str
    priority: str
    tags: list | None
    environment_id: int | None
    request_variables: dict | None = None
    session_mode: str | None = None
    query: str | None
    expected_answer: str | None
    expected_rules: dict | None
    turns: list | None
    session_expected: dict | None
    fail_fast: bool
    user_goal: str | None
    initial_query: str | None
    persona_description: str | None
    hidden_context: list | None
    max_turns: int
    terminate_conditions: dict | None
    simulator_template_id: int | None
    simulator_template_strategy: str
    simulator_template_version: int | None
    judge_template_id: int | None
    judge_template_strategy: str
    judge_template_version: int | None
    prompt_overrides: dict | None
    simulator_model_id: int | None
    judge_model_ids: list | None
    judge_strategy: str
    pass_threshold: float | None
    current_version: int
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    grading_mode: str | None = None
    input_type: str | None = None
    as_of: str | None = None
    snapshot_id: str | None = None
    answer_envelope: dict | None = None
    response_contract: dict | None = None
    plan_spec: dict | None = None
    risk_level: str | None = None
    owner: str | None = None
    review_status: str | None = None
    reviewed_by: str | None = None
    dataset_version: str | None = None
    dedupe_hash: str | None = None
    simulator_template_code: str | None = None
    judge_template_code: str | None = None
    environment_name: str | None = None


class RevisionOut(ORMModel):
    id: int
    case_id: int
    version: int
    change_type: str
    operator: str
    summary: str | None
    created_at: datetime
    snapshot: dict | None = None


class DiffChange(BaseModel):
    path: str
    label: str
    old: Any = None
    new: Any = None
    change_type: str


class RevisionDiff(BaseModel):
    case_id: int
    from_version: int
    to_version: int
    changes: list[DiffChange]
    total: int


class RollbackRequest(BaseModel):
    version: int = Field(ge=1)
    summary: str | None = None


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportItemResult(BaseModel):
    """逐条导入结果，便于前端展示与人工核对。"""

    row: int
    code: str | None = None
    action: Literal["created", "updated", "skipped", "failed"]
    message: str | None = None


class ImportResult(BaseModel):
    total: int
    created: int
    updated: int
    skipped: int = 0
    failed: int
    dry_run: bool = False
    on_conflict: Literal["upsert", "skip", "error"] = "upsert"
    errors: list[ImportRowError]
    items: list[ImportItemResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    case_ids: list[int]


class CaseExecutionItem(BaseModel):
    id: int
    task_id: int | None
    task_name: str | None = None
    case_id: int | None = None
    case_code: str | None = None
    case_name: str | None = None
    source: str
    status: str
    attempt_no: int = 1
    attempt_count: int = 1
    error_kind: str | None = None
    pass_flag: bool | None
    total_score: float | None
    case_version: int
    turns_used: int
    latency_ms: float | None
    ttft_ms: float | None
    started_at: datetime
    finished_at: datetime | None


class TrendPoint(BaseModel):
    execution_id: int
    started_at: datetime
    total_score: float | None
    latency_ms: float | None
    status: str
    pass_flag: bool | None
    turns_used: int


class CaseTrend(BaseModel):
    case_id: int
    points: list[TrendPoint]
    pass_rate: float
    avg_score: float
    avg_latency_ms: float
    flaky: bool


class RunCompareSide(BaseModel):
    execution_id: int
    case_version: int
    status: str
    total_score: float | None
    pass_flag: bool | None
    started_at: datetime
    final_answer: str | None
    generated_sql: str | None
    raw_db_data: Any = None
    judge_reason: str | None
    turns: list[dict[str, Any]]


class RunCompare(BaseModel):
    case_id: int
    left: RunCompareSide
    right: RunCompareSide
    diffs: list[DiffChange]


class CaseDebugRequest(BaseModel):
    environment_id: int | None = None
    case_snapshot_override: dict[str, Any] | None = None
