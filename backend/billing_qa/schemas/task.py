"""任务与执行 Schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from billing_qa.core.schedule_time import (
    normalize_local,
    normalize_weekdays,
    parse_time_text,
)
from billing_qa.models.enums import TaskScheduleType
from billing_qa.schemas.case import CaseExecutionItem
from billing_qa.schemas.common import ORMModel


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    environment_id: int | None = None
    # SELECTED：按 case_ids 执行；ALL_ACTIVE：忽略 case_ids，跨分页执行用例库全部启用用例
    case_scope: Literal["SELECTED", "ALL_ACTIVE"] = "SELECTED"
    case_ids: list[int] = Field(default_factory=list)
    concurrency: int = Field(default=3, ge=1, le=20)
    qps_limit: float = Field(default=5.0, gt=0, le=100)
    retry_on_env_error: bool = True
    max_env_retries: int = Field(default=2, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=3.0, ge=0, le=60)
    # 执行方式：立即 / 指定时间一次 / 每天 / 每周
    schedule_type: TaskScheduleType = TaskScheduleType.IMMEDIATE
    scheduled_at: datetime | None = Field(default=None, description="指定时间执行的时间点")
    schedule_time: str | None = Field(default=None, max_length=5, description="周期时刻 HH:MM")
    schedule_weekdays: list[int] = Field(
        default_factory=list, description="每周执行的星期，1=周一 … 7=周日"
    )
    auto_start: bool = True

    @model_validator(mode="after")
    def _validate_schedule(self) -> TaskCreate:
        if self.schedule_type == TaskScheduleType.SCHEDULED:
            moment = normalize_local(self.scheduled_at)
            if moment is None:
                raise ValueError("指定时间执行必须填写执行时间")
            if moment <= datetime.now():
                raise ValueError("指定执行时间必须晚于当前时间")
            self.scheduled_at = moment
            self.schedule_time = None
            self.schedule_weekdays = []
        elif self.schedule_type in {TaskScheduleType.DAILY, TaskScheduleType.WEEKLY}:
            if not self.schedule_time:
                raise ValueError("周期执行必须填写执行时刻")
            try:
                parse_time_text(self.schedule_time)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            self.scheduled_at = None
            if self.schedule_type == TaskScheduleType.WEEKLY:
                try:
                    self.schedule_weekdays = normalize_weekdays(self.schedule_weekdays)
                except ValueError as exc:
                    raise ValueError(str(exc)) from exc
            else:
                self.schedule_weekdays = []
        else:
            self.scheduled_at = None
            self.schedule_time = None
            self.schedule_weekdays = []
        return self


class TaskScheduleUpdate(BaseModel):
    """调整定时计划：执行方式、时间点、星期与启停。"""

    schedule_type: TaskScheduleType | None = None
    scheduled_at: datetime | None = None
    schedule_time: str | None = Field(default=None, max_length=5)
    schedule_weekdays: list[int] | None = None
    active: bool | None = None


class TaskOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None
    status: str
    control_state: str
    environment_id: int | None
    case_ids: list
    total_cases: int
    finished_cases: int
    pass_count: int
    fail_count: int
    error_count: int
    max_turns_count: int
    concurrency: int
    qps_limit: float
    retry_on_env_error: bool
    max_env_retries: int
    retry_backoff_seconds: float
    schedule_type: str
    scheduled_at: datetime | None
    schedule_time: str | None
    schedule_weekdays: list | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    schedule_active: bool
    schedule_source_id: int | None
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: dict | None
    error_message: str | None
    environment_name: str | None = None


class StatusDistribution(BaseModel):
    PASS: int = 0
    FAIL: int = 0
    ERROR: int = 0
    MAX_TURNS_EXCEEDED: int = 0
    REPETITION_ABORT: int = 0
    ABORTED: int = 0
    RUNNING: int = 0
    PENDING: int = 0


class TaskMetrics(BaseModel):
    task_id: int
    status: str
    control_state: str
    total_cases: int
    finished_cases: int
    progress: float
    status_distribution: StatusDistribution
    pass_rate: float
    avg_ttft_ms: float
    avg_latency_ms: float
    p95_latency_ms: float
    avg_turns: float
    simulator_tokens: int
    judge_tokens: int
    total_tokens: int
    env_retry_count: int = 0
    eta_seconds: float | None
    elapsed_seconds: float
    throughput_per_minute: float


class TaskProgressEvent(BaseModel):
    type: str = "progress"
    task_id: int
    status: str
    control_state: str
    progress: float
    finished_cases: int
    total_cases: int
    pass_count: int
    fail_count: int
    error_count: int
    max_turns_count: int
    metrics: TaskMetrics | None = None
    execution: CaseExecutionItem | None = None
    message: str | None = None
    timestamp: datetime


class TurnTraceOut(ORMModel):
    id: int
    turn_index: int
    query: str
    input_source: str
    simulator_thought: str | None
    simulator_status: str | None
    hidden_context_released: list | None
    rendered_prompt: str | None
    request_payload: dict | None
    response_raw: dict | None
    final_answer: str | None
    session_id: str | None
    intent: str | None
    generated_sql: str | None
    api_params: dict | None
    raw_db_data: dict | None
    ttft_ms: float | None
    latency_ms: float | None
    rule_check: dict | None
    simulator_prompt_tokens: int
    simulator_completion_tokens: int
    created_at: datetime


class JudgeResultOut(ORMModel):
    id: int
    model_config_id: int | None
    model_name: str
    pass_flag: bool
    total_score: float
    dimension_scores: dict | None
    error_tags: list | None
    reason: str | None
    raw_response: dict | None
    judge_prompt: str | None
    prompt_tokens: int
    completion_tokens: int
    created_at: datetime


class ExecutionListItem(BaseModel):
    id: int
    task_id: int | None
    task_name: str | None = None
    case_id: int
    case_code: str | None = None
    case_name: str | None = None
    case_version: int
    source: str
    status: str
    attempt_no: int = 1
    attempt_count: int = 1
    error_kind: str | None = None
    pass_flag: bool | None
    total_score: float | None
    termination_status: str | None
    turns_used: int
    latency_ms: float | None
    ttft_ms: float | None
    simulator_tokens: int
    judge_tokens: int
    started_at: datetime
    finished_at: datetime | None


class ExecutionDetail(ExecutionListItem):
    environment_id: int | None
    max_turns: int | None
    simulator_template_version: int | None
    judge_template_version: int | None
    rule_result: dict | None
    judge_result: dict | None
    error_message: str | None
    case_snapshot: dict
    trace_uid: str
    turns: list[TurnTraceOut]
    judges: list[JudgeResultOut]
    raw_request_response: list[dict[str, Any]] = Field(default_factory=list)
