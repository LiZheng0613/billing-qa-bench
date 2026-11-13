"""任务指标聚合单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from billing_qa.services.engine.metrics import (
    TERMINAL_STATUSES,
    _percentile,
    build_distribution,
)


def _record(status: str, latency: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        latency_ms=latency,
        ttft_ms=latency,
        turns_used=1,
        simulator_tokens=0,
        judge_tokens=0,
    )


def test_percentile() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile([], 0.95) == 0.0
    assert _percentile([42.0], 0.95) == 42.0
    assert _percentile(values, 0.5) == 30.0
    assert _percentile(values, 0.95) == 48.0


def test_build_distribution_counts_pending() -> None:
    records = [
        _record("PASS"),
        _record("PASS"),
        _record("FAIL"),
        _record("MAX_TURNS_EXCEEDED"),
    ]
    distribution = build_distribution(records, total_cases=6)
    assert distribution.PASS == 2
    assert distribution.FAIL == 1
    assert distribution.MAX_TURNS_EXCEEDED == 1
    assert distribution.PENDING == 2


def test_build_distribution_ignores_unknown_status() -> None:
    distribution = build_distribution([_record("RUNNING"), _record("UNKNOWN")], total_cases=2)
    assert distribution.RUNNING == 1
    assert distribution.PENDING == 0


def test_terminal_statuses_cover_all_final_states() -> None:
    assert {"PASS", "FAIL", "ERROR", "MAX_TURNS_EXCEEDED", "REPETITION_ABORT", "ABORTED"} == TERMINAL_STATUSES
