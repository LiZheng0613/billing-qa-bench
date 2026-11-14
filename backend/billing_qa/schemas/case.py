"""测试用例与变更审计 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from billing_qa.models.enums import CaseType, JudgeStrategy, TemplateStrategy
from billing_qa.schemas.common import ORMModel


class NumericExpectation(BaseModel):
    label: str = Field(default="数值")
    value: float
    tolerance: float = Field(default=0.0, ge=0)


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
        return self


class CaseCreate(CaseBase):
    pass


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    tags: list[str] | None = None
    environment_id: int | None = None
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
    change_summary: str | None = Field(default=None, max_length=512)


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


class ImportResult(BaseModel):
    total: int
    created: int
    updated: int
    failed: int
    errors: list[ImportRowError]
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
