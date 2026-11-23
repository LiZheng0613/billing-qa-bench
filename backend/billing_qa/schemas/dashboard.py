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


class ExecutionEnvironmentMetric(BaseModel):
    """单个执行环境在对比窗口内的正确率 / 稳定性 / 效率画像。"""

    environment_id: int | None
    code: str
    name: str
    is_active: bool
    last_health_status: str | None
    last_health_latency_ms: float | None
    last_health_at: datetime | None
    last_executed_at: datetime | None

    total: int
    measured: int
    passed: int
    failed: int
    pass_rate: float | None
    previous_pass_rate: float | None

    # 按用例去重后的覆盖度：避免同一用例反复跑把样本量刷高。
    case_total: int
    case_passed: int
    case_pass_rate: float | None
    # 同一用例在窗口内既通过又失败，说明结果不稳定（flaky）。
    flaky_cases: int
    flaky_rate: float | None
    # 环境/执行异常（status=ERROR）单独计，不和普通判负混在一起。
    error_total: int
    error_rate: float | None

    avg_score: float | None
    avg_turns: float | None
    p95_ms: float | None
    avg_ms: float | None
    avg_ttft_ms: float | None
    tokens: int
    # 每日通过率的标准差（pp），只有 >=2 个有效日才有值。
    volatility: float | None

    daily: list[ExecutionDailyPoint]
    failure_reasons: list[ExecutionFailureReason]


class ExecutionEnvironmentSummary(BaseModel):
    """按执行环境横向对比：同一时间窗、同一口径，便于判断优化思路优劣。"""

    days: int
    since: datetime
    environments: list[ExecutionEnvironmentMetric]
