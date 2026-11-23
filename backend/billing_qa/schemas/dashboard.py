"""测试大盘聚合视图：一次请求拿到首屏需要的执行统计。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ExecutionWindowSummary(BaseModel):
    """时间窗内的执行口径（已排除沙盒试跑）。"""

    hours: int
    total: int
    measured: int
    passed: int
    failed: int
    pass_rate: float | None
    previous_pass_rate: float | None
    previous_measured: int


class ExecutionLatencySummary(BaseModel):
    p95_ms: float | None
    avg_ms: float | None
    avg_ttft_ms: float | None


class ExecutionTokenSummary(BaseModel):
    simulator: int
    judge: int


class ExecutionDailyPoint(BaseModel):
    date: str
    total: int
    measured: int
    passed: int
    pass_rate: float | None


class ExecutionSourceSummary(BaseModel):
    source: str
    total: int
    measured: int
    passed: int
    pass_rate: float | None


class ExecutionFailureReason(BaseModel):
    status: str
    count: int


class ExecutionFailedCase(BaseModel):
    case_id: int
    case_code: str | None
    case_name: str | None
    failures: int
    last_failed_at: datetime | None
    last_status: str | None


class ExecutionSummary(BaseModel):
    window: ExecutionWindowSummary
    latency: ExecutionLatencySummary
    tokens: ExecutionTokenSummary
    daily: list[ExecutionDailyPoint]
    by_source: list[ExecutionSourceSummary]
    failure_reasons: list[ExecutionFailureReason]
    top_failed_cases: list[ExecutionFailedCase]
