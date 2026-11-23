"""计划多轮次执行报表：纯聚合 + 工作簿 / JSONL 渲染。

数据由调用方（API 层）按轮次收集好传入（每轮一份 ``RoundData``），
本模块只做指标聚合与渲染，不连数据库，便于单元测试。

Excel 工作表（默认 6 页）：
- 综合概览：计划信息、配置一致性、跨轮结论、稳定性分布、失败画像；
- 轮次对比：每执行轮次一行，含环比 / 较首轮 / 趋势判定 / 新失败与新通过用例；
- 跨轮问题清单：跨轮风险用例（稳定失败 / 波动 / 幸运通过 / 压线通过），
  含各轮根因、末次失败全套素材、处置建议、人工跟进列与平台超链接；
- 用例稳定性：每用例一行，含状态 / 分数 / 耗序列、Flaky、回归 / 恢复、
  分数统计、用例版本变更；
- 多轮用例明细：轮次 × 用例长表（与单批次「用例结果」核心字段对齐）；
- 口径说明。

可选（include_raw=True）：原始明细（轮次 × 用例 × 完整 70 列字段）。

JSONL 导出（kind 区分）：meta / round_summary / case_stability / row，
row 行不截断，完整保留轮次轨迹等大字段。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskMetrics
from billing_qa.services.io.execution_excel import (
    ISSUE_SEVERITY,
    OWNERSHIP_OPTIONS,
    PRIORITY_ORDER,
    ROOT_CAUSE_LABELS,
    TASK_RESULT_COLUMNS,
    apply_dropdown,
    apply_hyperlinks,
    bool_text,
    distribution_text,
    exec_url_builder,
    first_value,
    format_datetime,
    judge_model_name,
    schedule_type_label,
    status_label,
    trace_url_builder,
    write_overview,
    write_table,
)

ROUNDS_JSONL_SCHEMA_NAME = "billing-qa/task-rounds"
ROUNDS_JSONL_SCHEMA_VERSION = 1

# 已产生终态结果的状态（计入通过率分母）。
TERMINAL_STATUSES = {
    "PASS",
    "FAIL",
    "ERROR",
    "MAX_TURNS_EXCEEDED",
    "REPETITION_ABORT",
    "ABORTED",
}

# 用例稳定性页的「第 k 轮状态 / 分数 / 耗时」动态列上限；超过则只保留汇总列。
MAX_DYNAMIC_ROUND_COLUMNS = 20

# Excel 工作表行数上限保护（1,048,576 减去表头与余量）。
EXCEL_MAX_ROWS = 1_048_000

# 近 N 次通过率窗口默认值。
DEFAULT_HISTORY_COUNT = 10


@dataclass
class RoundData:
    """一个执行轮次（一个批次）的数据快照。"""

    task: BatchTask
    metrics: TaskMetrics
    rows: list[dict[str, Any]]
    # 第几次触发产生的轮次链（同一计划周期触发会形成多条链）。
    chain_index: int = 1
    # 全局轮次序号（按链与链内顺序编号，从 1 开始）。
    round_seq: int = 0

    @property
    def chain_label(self) -> str:
        return f"第 {self.chain_index} 次触发"

    @property
    def case_ids(self) -> set[int]:
        return {row["case_id"] for row in self.rows if row.get("case_id") is not None}

    @property
    def task_case_ids(self) -> set[int]:
        """该轮任务的用例范围（用例在范围内但无记录 → 缺失轮次）。"""
        return {int(case_id) for case_id in (self.task.case_ids or [])}


# ---------------------------------------------------------------- 基础工具
def _latest_row_per_case(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """一轮内按用例取最新一次尝试（include_attempts 展开时用于去重）。"""
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        if case_id is None:
            continue
        current = result.get(case_id)
        if current is None or (row.get("attempt_no") or 0) >= (current.get("attempt_no") or 0):
            result[case_id] = row
    return result


def case_maps(rounds: list[RoundData]) -> dict[int, dict[int, dict[str, Any]]]:
    """{全局轮次: {case_id: 最新尝试行}}。"""
    return {rd.round_seq: _latest_row_per_case(rd.rows) for rd in rounds}


def _round_score(row: dict[str, Any]) -> float | None:
    value = row.get("total_score")
    return value if isinstance(value, (int, float)) else None


def _round_latency(row: dict[str, Any]) -> float | None:
    value = row.get("latency_ms")
    return value if isinstance(value, (int, float)) else None


# ---------------------------------------------------------------- 轮次聚合
def round_aggregates(rounds: list[RoundData]) -> list[dict[str, Any]]:
    """每个执行轮次一行的聚合指标（轮次对比页 + JSONL round_summary）。"""
    maps = case_maps(rounds)
    code_of = {
        row.get("case_id"): str(row.get("case_code") or row.get("case_id"))
        for rd in rounds
        for row in rd.rows
    }
    aggregates: list[dict[str, Any]] = []
    cumulative_pass = 0
    cumulative_measured = 0
    previous: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    for rd in rounds:
        rows = list(maps[rd.round_seq].values())
        dist = rd.metrics.status_distribution
        measured = sum(
            getattr(dist, key)
            for key in (
                "PASS",
                "FAIL",
                "ERROR",
                "MAX_TURNS_EXCEEDED",
                "REPETITION_ABORT",
                "ABORTED",
            )
        )
        pass_count = dist.PASS
        pass_rate = pass_count / measured * 100 if measured else None
        scores = [score for score in (_round_score(r) for r in rows) if score is not None]
        latencies = [lat for lat in (_round_latency(r) for r in rows) if lat is not None]
        self_check_count = sum(
            1 for r in rows if r.get("self_check_status_code") == "DONE"
        )
        unfinished = dist.RUNNING + dist.PENDING
        aggregate: dict[str, Any] = {
            "round_seq": rd.round_seq,
            "chain_index": rd.chain_index,
            "chain_label": rd.chain_label,
            "round_index": rd.task.round_index or rd.round_seq,
            "task_id": rd.task.id,
            "code": rd.task.code,
            "name": rd.task.name,
            "status": status_label(rd.task.status) or rd.task.status,
            "sim_template": first_value(rd.rows, "simulator_template_version"),
            "judge_template": first_value(rd.rows, "judge_template_version"),
            "grader_version": first_value(rd.rows, "grader_version"),
            "judge_model": judge_model_name(rd.rows),
            "environment_name": first_value(rd.rows, "environment_name"),
            "case_count": len(rows),
            "started_at": format_datetime(rd.task.started_at),
            "finished_at": format_datetime(rd.task.finished_at),
            "elapsed_seconds": rd.metrics.elapsed_seconds,
            "pass_count": pass_count,
            "fail_count": dist.FAIL,
            "error_count": dist.ERROR,
            "max_turns_count": dist.MAX_TURNS_EXCEEDED,
            "repetition_count": dist.REPETITION_ABORT,
            "aborted_count": dist.ABORTED,
            "unfinished_count": unfinished,
            "pass_rate": round(pass_rate, 1) if pass_rate is not None else None,
            "avg_score": round(statistics.mean(scores), 2) if scores else None,
            "avg_turns": round(rd.metrics.avg_turns, 2),
            "avg_latency_ms": round(rd.metrics.avg_latency_ms) if rd.metrics.avg_latency_ms else None,
            "p95_latency_ms": rd.metrics.p95_latency_ms,
            "avg_ttft_ms": round(rd.metrics.avg_ttft_ms) if rd.metrics.avg_ttft_ms else None,
            "total_tokens": rd.metrics.total_tokens,
            "retry_count": rd.metrics.retry_count,
            "self_check_count": self_check_count,
            "measured": measured,
            "_case_status": {
                case_id: str(row.get("status_code") or "")
                for case_id, row in maps[rd.round_seq].items()
            },
        }
        cumulative_pass += pass_count
        cumulative_measured += measured
        aggregate["cumulative_pass_rate"] = (
            round(cumulative_pass / cumulative_measured * 100, 1)
            if cumulative_measured
            else None
        )
        if previous is None:
            aggregate["delta_pass_rate"] = None
            aggregate["delta_avg_score"] = None
            aggregate["delta_latency_pct"] = None
            aggregate["delta_base_pass_rate"] = None
            aggregate["delta_base_avg_score"] = None
            aggregate["trend"] = "基线"
            aggregate["new_failed"] = "—（首轮基线）"
            aggregate["new_passed"] = "—（首轮基线）"
        else:
            aggregate["delta_pass_rate"] = _delta(
                aggregate.get("pass_rate"), previous.get("pass_rate")
            )
            aggregate["delta_avg_score"] = _delta(
                aggregate.get("avg_score"), previous.get("avg_score")
            )
            aggregate["delta_latency_pct"] = _pct_delta(
                aggregate.get("avg_latency_ms"), previous.get("avg_latency_ms")
            )
            aggregate["trend"] = _trend_label(
                aggregate["delta_pass_rate"], aggregate["delta_avg_score"]
            )
            new_failed, new_passed = _status_changes_with_codes(
                previous["_case_status"], aggregate["_case_status"], code_of
            )
            aggregate["new_failed"] = "、".join(new_failed) or "无"
            aggregate["new_passed"] = "、".join(new_passed) or "无"
            aggregate["delta_base_pass_rate"] = _delta(
                aggregate.get("pass_rate"), baseline.get("pass_rate")
            )
            aggregate["delta_base_avg_score"] = _delta(
                aggregate.get("avg_score"), baseline.get("avg_score")
            )
        aggregates.append(aggregate)
        if baseline is None:
            baseline = aggregate
        previous = aggregate
    return aggregates


def _delta(current: float | None, base: float | None) -> float | None:
    if current is None or base is None:
        return None
    return round(current - base, 2)


def _pct_delta(current: float | None, base: float | None) -> float | None:
    if not current or not base:
        return None
    return round((current - base) / base * 100, 1)


def _trend_label(delta_rate: float | None, delta_score: float | None) -> str:
    if delta_rate is None and delta_score is None:
        return "持平"
    if (delta_rate or 0) > 0 or (delta_rate == 0 and (delta_score or 0) > 0):
        return "改善"
    if (delta_rate or 0) < 0 or (delta_rate == 0 and (delta_score or 0) < 0):
        return "恶化"
    return "持平"


def _status_changes_with_codes(
    previous: dict[int, str],
    current: dict[int, str],
    code_of: dict[int, str],
) -> tuple[list[str], list[str]]:
    new_failed: list[str] = []
    new_passed: list[str] = []
    for case_id, status in current.items():
        prev = previous.get(case_id)
        if prev is None:
            continue
        if prev == "PASS" and status != "PASS":
            new_failed.append(code_of.get(case_id, str(case_id)))
        elif prev != "PASS" and status == "PASS":
            new_passed.append(code_of.get(case_id, str(case_id)))
    return new_failed, new_passed


# ---------------------------------------------------------------- 用例稳定性
@dataclass
class CaseStability:
    """单个用例跨轮稳定性指标（用例稳定性页 + JSONL case_stability）。"""

    case_id: int
    case_code: str
    case_name: str
    case_type: str | None = None
    priority: str | None = None
    tags: str | None = None
    owner: str | None = None
    # 每轮的行数据（无记录 / 未纳入为 None）。
    per_round: list[dict[str, Any] | None] = field(default_factory=list)
    # 全部轮次的全局序号（与 per_round 一一对应）。
    round_seqs: list[int] = field(default_factory=list)
    present_rounds: list[int] = field(default_factory=list)
    missing_rounds: list[int] = field(default_factory=list)
    excluded_rounds: list[int] = field(default_factory=list)
    versions: list[Any] = field(default_factory=list)
    measured: int = 0
    passed: int = 0
    rate: float | None = None
    level: str = "数据不足"
    flaky: bool = False
    regression: int = 0
    recovery: int = 0
    first_fail: int | None = None
    last_fail: int | None = None
    max_streak: int = 0
    avg_score: float | None = None
    max_score: float | None = None
    min_score: float | None = None
    score_range: float | None = None
    score_std: float | None = None
    score_cv: float | None = None
    score_trend: str = "—"
    avg_latency: float | None = None
    max_latency: float | None = None
    avg_turns: float | None = None
    avg_tokens: float | None = None
    roots: list[str] = field(default_factory=list)
    top_tags: str = "—"
    risk_type: str | None = None
    last_failure: dict[str, Any] | None = None
    last_failure_round: int | None = None

    @property
    def version_changed(self) -> bool:
        return len({v for v in self.versions if v is not None}) > 1

    @property
    def versions_text(self) -> str:
        parts = [f"v{v}" if v is not None else "—" for v in self.versions]
        return " → ".join(parts)

    @property
    def sequence(self) -> str:
        labels = []
        for row in self.per_round:
            if row is None:
                labels.append("未测")
                continue
            code = str(row.get("status_code") or "")
            labels.append(status_label(code) or code or "未测")
        return " → ".join(labels)

    @property
    def fail_rounds_text(self) -> str:
        rounds = [
            f"第 {seq} 轮"
            for seq, row in zip(self.round_seqs, self.per_round)
            if row is not None and row.get("status_code") != "PASS"
        ]
        return "、".join(rounds) or "—"

    @property
    def root_rounds_text(self) -> str:
        parts = []
        for seq, row in zip(self.round_seqs, self.per_round):
            if row is None or row.get("status_code") == "PASS":
                continue
            root = row.get("root_cause") or "未收敛"
            parts.append(f"第 {seq} 轮：{root}")
        return "；".join(parts) or "—"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_code": self.case_code,
            "case_name": self.case_name,
            "measured_rounds": self.measured,
            "passed_rounds": self.passed,
            "pass_rate": round(self.rate, 1) if self.rate is not None else None,
            "status_sequence": self.sequence,
            "level": self.level,
            "flaky": self.flaky,
            "regression_count": self.regression,
            "recovery_count": self.recovery,
            "first_fail_round": self.first_fail,
            "last_fail_round": self.last_fail,
            "max_fail_streak": self.max_streak,
            "avg_score": round(self.avg_score, 2) if self.avg_score is not None else None,
            "score_range": self.score_range,
            "risk_type": self.risk_type,
            "versions": self.versions_text,
            "version_changed": self.version_changed,
        }


def compute_case_stability(
    rounds: list[RoundData],
    *,
    borderline_score: float = 70.0,
) -> list[CaseStability]:
    """逐用例跨轮稳定性：状态序列 / 分级 / Flaky / 回归恢复 / 分数统计 / 版本变更。"""
    maps = case_maps(rounds)
    sequences = [rd.round_seq for rd in rounds]
    case_ids: set[int] = set()
    for mapping in maps.values():
        case_ids.update(mapping.keys())
    for rd in rounds:
        case_ids.update(rd.case_ids)

    stabilities: list[CaseStability] = []
    for case_id in sorted(case_ids):
        per_round: list[dict[str, Any] | None] = []
        present: list[int] = []
        meta: dict[str, Any] | None = None
        for seq in sequences:
            rd = rounds[sequences.index(seq)]
            row = maps[seq].get(case_id)
            if row is not None:
                per_round.append(row)
                present.append(seq)
                meta = row
            elif case_id in (rd.task_case_ids or rd.case_ids):
                per_round.append(None)  # 该轮范围内有用例但无记录 → 缺失
            else:
                per_round.append(None)  # 未纳入该轮
        if meta is None:
            continue
        stability = CaseStability(
            case_id=case_id,
            case_code=str(meta.get("case_code") or case_id),
            case_name=str(meta.get("case_name") or ""),
            case_type=meta.get("case_type"),
            priority=meta.get("priority"),
            tags=meta.get("tags"),
            owner=meta.get("owner"),
            per_round=per_round,
            round_seqs=sequences,
            present_rounds=present,
        )
        stability.missing_rounds = [
            seq
            for seq, row, rd in zip(sequences, per_round, rounds)
            if row is None and case_id in (rd.task_case_ids or rd.case_ids)
        ]
        stability.excluded_rounds = [
            seq
            for seq, row, rd in zip(sequences, per_round, rounds)
            if row is None and case_id not in (rd.task_case_ids or rd.case_ids)
        ]
        stability.versions = [
            (row.get("case_version") if row is not None else None) for row in per_round
        ]

        measured_rows = [
            row
            for row in per_round
            if row is not None and str(row.get("status_code") or "") in TERMINAL_STATUSES
        ]
        stability.measured = len(measured_rows)
        stability.passed = sum(
            1 for row in measured_rows if row.get("status_code") == "PASS"
        )
        if stability.measured:
            stability.rate = stability.passed / stability.measured * 100
        if stability.measured >= 2:
            if stability.rate == 100:
                stability.level = "稳定通过"
            elif stability.rate == 0:
                stability.level = "稳定失败"
            else:
                stability.level = "波动"
        else:
            stability.level = "数据不足"
        stability.flaky = 0 < stability.passed < stability.measured

        # 回归 / 恢复 / 连续失败 / 首末失败（按「全部轮次 × 行数据」顺序）。
        entries = list(zip(sequences, per_round))
        measured_pairs = [
            (seq, row)
            for seq, row in entries
            if row is not None and str(row.get("status_code") or "") in TERMINAL_STATUSES
        ]
        streak = 0
        for index, (seq, row) in enumerate(measured_pairs, start=1):
            if row.get("status_code") == "PASS":
                if index > 1 and measured_pairs[index - 2][1].get("status_code") != "PASS":
                    stability.recovery += 1
                streak = 0
            else:
                if index > 1 and measured_pairs[index - 2][1].get("status_code") == "PASS":
                    stability.regression += 1
                streak += 1
                stability.max_streak = max(stability.max_streak, streak)
                if stability.first_fail is None:
                    stability.first_fail = seq
                stability.last_fail = seq

        scores = [score for score in (_round_score(r) for r in measured_rows) if score is not None]
        if scores:
            stability.avg_score = statistics.mean(scores)
            stability.max_score = max(scores)
            stability.min_score = min(scores)
            stability.score_range = round(max(scores) - min(scores), 1)
            if len(scores) > 1:
                stability.score_std = statistics.pstdev(scores)
                if stability.avg_score:
                    stability.score_cv = stability.score_std / stability.avg_score * 100
            if len(scores) >= 2:
                diff = scores[-1] - scores[0]
                stability.score_trend = (
                    "上升" if diff > 2 else ("下降" if diff < -2 else "持平")
                )
        latencies = [lat for lat in (_round_latency(r) for r in measured_rows) if lat is not None]
        if latencies:
            stability.avg_latency = statistics.mean(latencies)
            stability.max_latency = max(latencies)
        turns = [r.get("turns_used") for r in measured_rows if r.get("turns_used") is not None]
        if turns:
            stability.avg_turns = round(statistics.mean(turns), 2)
        tokens = [r.get("total_tokens") for r in measured_rows if r.get("total_tokens") is not None]
        if tokens:
            stability.avg_tokens = round(statistics.mean(tokens))

        roots: list[str] = []
        tag_counter: dict[str, int] = {}
        for row in measured_rows:
            if row.get("status_code") == "PASS":
                continue
            if row.get("root_cause"):
                roots.append(str(row["root_cause"]))
            for tag in str(row.get("error_tags") or "").split("、"):
                text = tag.strip()
                if text:
                    tag_counter[text] = tag_counter.get(text, 0) + 1
        stability.roots = roots
        stability.top_tags = (
            "、".join(
                f"{tag} ×{count}"
                for tag, count in sorted(tag_counter.items(), key=lambda kv: -kv[1])[:3]
            )
            or "—"
        )

        # 末次失败行 + 风险类型（含幸运通过 / 压线通过）。
        for seq, row in zip(reversed(sequences), reversed(per_round)):
            if row is not None and row.get("status_code") != "PASS":
                stability.last_failure = row
                stability.last_failure_round = seq
                break
        stability.risk_type = _risk_type(stability, borderline_score)
        stabilities.append(stability)
    stabilities.sort(
        key=lambda item: (
            PRIORITY_ORDER.get(str(item.priority), 9),
            str(item.case_code),
        )
    )
    return stabilities


def _risk_type(stability: CaseStability, borderline_score: float) -> str | None:
    parts: list[str] = []
    if stability.level in ("稳定失败", "波动"):
        parts.append(stability.level)
    latest = stability.per_round[-1] if stability.per_round else None
    if latest is not None and latest.get("status_code") == "PASS":
        if latest.get("lucky_pass") == "是":
            parts.append("幸运通过（末轮）")
        score = _round_score(latest)
        if score is not None and score < borderline_score:
            parts.append("压线通过（末轮）")
    return " + ".join(parts) if parts else None


def dispose_suggestion(stability: CaseStability) -> str:
    """跨轮处置建议（确定性规则）。"""
    if stability.risk_type and stability.risk_type.startswith("稳定失败"):
        return (
            f"连续 {stability.measured} 轮失败：确认根因后提单跟踪系统缺陷，"
            "用例侧补充约束（must_not / 事实 Must）后复跑。"
        )
    if stability.regression > 0:
        return (
            "回归关注：近期由通过转为失败，优先复跑并对比失败轮的取数与配置差异"
            "（结合轮次对比页的配置一致性）。"
        )
    if stability.level == "波动":
        if stability.recovery > 0:
            return "波动用例：失败后已恢复，确认为一次性问题（如环境抖动）后保留观察。"
        return "波动用例：复跑确认是否稳定复现，并结合失败轮建议排查方向处理。"
    if stability.risk_type and "幸运通过" in stability.risk_type:
        return "末轮幸运通过（答案对但无取数证据）：补充数值 / 事实 Must 约束后复跑。"
    if stability.risk_type and "压线通过" in stability.risk_type:
        return "末轮压线通过：核对判分口径后复跑，观察分数是否稳定。"
    if stability.level == "数据不足":
        return "轮次数据不足：补跑后再评估稳定性。"
    return "结果稳定，纳入回归基线。"


# ---------------------------------------------------------------- 配置一致性
def _consistency_text(label: str, values: list[Any]) -> str:
    distinct: list[Any] = []
    for value in values:
        if value not in (None, "") and value not in distinct:
            distinct.append(value)
    if not distinct:
        return f"{label}：无数据"
    if len(distinct) == 1:
        return f"{label}：各轮一致（{distinct[0]}）"
    transitions: list[str] = []
    for previous, current in zip(values, values[1:]):
        if previous not in (None, "") and current not in (None, "") and previous != current:
            transitions.append(f"{previous} → {current}")
    if transitions:
        return f"{label}：不一致（{'，'.join(dict.fromkeys(transitions))}）"
    return f"{label}：不一致（{'，'.join(str(v) for v in distinct)}）"


def config_consistency(rounds: list[RoundData]) -> list[tuple[str, str]]:
    """轮次间模板 / 判分器 / 模型 / 环境 / 用例集 / 用例版本一致性。"""
    if not rounds:
        return []
    sim_values = [first_value(rd.rows, "simulator_template_version") for rd in rounds]
    judge_values = [first_value(rd.rows, "judge_template_version") for rd in rounds]
    grader_values = [first_value(rd.rows, "grader_version") for rd in rounds]
    model_values = [judge_model_name(rd.rows) for rd in rounds]
    env_values = [first_value(rd.rows, "environment_name") for rd in rounds]
    case_sets = [(rd.task_case_ids or rd.case_ids) for rd in rounds]
    lines = [
        _consistency_text("模拟器模板", sim_values),
        _consistency_text("裁判模板", judge_values),
        _consistency_text("判分器版本", grader_values),
        _consistency_text("裁判模型", model_values),
        _consistency_text("执行环境", env_values),
    ]
    same_case_set = all(cs == case_sets[0] for cs in case_sets)
    lines.append(
        ("用例集：各轮一致" if same_case_set else "用例集：不一致（各轮用例范围不同）")
        + f"（{len(case_sets[0])} 条）"
    )
    changed_versions = 0
    for rd in rounds:
        versions = {row.get("case_version") for row in rd.rows}
        if len(versions) > 1:
            changed_versions += 1
    lines.append(
        f"轮内用例版本：{changed_versions} 轮存在执行期间用例变更"
        if changed_versions
        else "轮内用例版本：各轮执行期间无用例变更"
    )
    consistent = all(
        len({v for v in values if v not in (None, "")}) <= 1
        for values in (sim_values, judge_values, grader_values, model_values, env_values)
    ) and same_case_set and not changed_versions
    lines.append(
        "结论：各轮配置一致，稳定性结论可信。"
        if consistent
        else "结论：存在配置差异（见上），跨轮波动需结合变更分析，不宜全部归因于被测系统。"
    )
    return [(f"配置一致性 {index + 1}", text) for index, text in enumerate(lines)]


# ---------------------------------------------------------------- 列定义
ROUND_COMPARE_COLUMNS: list[tuple[str, int]] = [
    ("全局轮次", 8), ("轮次链", 12), ("链内轮次", 10), ("批次编号", 21), ("批次 ID", 9),
    ("批次名称", 24), ("执行状态", 10), ("模拟器模板", 10), ("裁判模板", 10),
    ("判分器版本", 13), ("裁判模型", 12), ("执行环境", 14), ("开始时间", 17),
    ("结束时间", 17), ("耗时(秒)", 9), ("用例数", 9), ("通过数", 8), ("失败数", 8),
    ("异常数", 8), ("轮次超限数", 9), ("复读熔断数", 9), ("未完成数", 9),
    ("通过率(%)", 10), ("平均分", 9), ("平均对话轮次", 11), ("平均耗时(ms)", 11),
    ("P95 耗时(ms)", 11), ("平均 TTFT(ms)", 11), ("总 Tokens", 11), ("自动重试数", 10),
    ("追问自检数", 10), ("较上一轮通过率(pp)", 12), ("较上一轮平均分", 11),
    ("较上一轮耗时(%)", 12), ("较首轮通过率(pp)", 12), ("较首轮平均分", 11),
    ("趋势判定", 10), ("累计通过率(%)", 11), ("本轮新失败用例", 30), ("本轮新通过用例", 30),
]

CROSS_ISSUE_COLUMNS: list[tuple[str, int]] = [
    ("序号", 6), ("优先级", 8), ("用例编号", 18), ("用例名称", 22), ("用例类型", 10),
    ("业务域标签", 14), ("用例负责人", 10), ("风险类型", 16), ("失败轮次", 14),
    ("已测/通过轮次", 11), ("通过率(%)", 10), ("各轮失败根因", 34), ("高频错误标签", 22),
    ("末次失败轮次", 10), ("末次失败裁判结论", 40), ("末次失败期望答案", 36),
    ("末次失败实际答案", 36), ("未命中事实", 28), ("末次轨迹诊断摘要", 32),
    ("关键 SQL/接口摘录", 34), ("末轮问答摘录", 36), ("追问自检结论", 32),
    (f"近{DEFAULT_HISTORY_COUNT}次通过率(全库)", 13), ("建议排查方向", 40),
    ("处置建议", 42), ("最近失败执行号", 11), ("最近失败 Trace UID", 20),
    ("处理人（跟进）", 12), ("处理结论", 24), ("问题归属", 11), ("缺陷单号", 13),
]

STABILITY_BASE_COLUMNS: list[tuple[str, int]] = [
    ("用例 ID", 8), ("用例编号", 18), ("用例名称", 22), ("用例类型", 10),
    ("优先级", 8), ("业务域标签", 14), ("用例负责人", 10), ("出现轮次", 9),
    ("缺失轮次", 10), ("已测轮次", 9), ("通过轮次", 9), ("通过率(%)", 10),
    ("状态序列", 24),
]

STABILITY_TAIL_COLUMNS: list[tuple[str, int]] = [
    ("稳定性分级", 11), ("Flaky", 8), ("回归次数", 9), ("恢复次数", 9),
    ("最长连续失败", 11), ("首次失败轮次", 10), ("最近失败轮次", 10),
    ("平均分", 9), ("最高分", 9), ("最低分", 9), ("分数极差", 9),
    ("分数标准差", 10), ("变异系数(%)", 10), ("分数趋势", 9),
    ("用例版本序列", 14), ("执行期间用例变更", 12), ("平均耗时(ms)", 11),
    ("最大耗时(ms)", 11), ("平均对话轮次", 11), ("平均 Tokens", 11),
    ("主要失败根因", 12), ("高频错误标签", 22), ("处置建议", 42),
    ("最近失败执行号", 11), ("最近失败 Trace UID", 20),
]

DETAIL_COLUMNS: list[tuple[str, int]] = [
    ("全局轮次", 8), ("轮次链", 12), ("批次编号", 21), ("用例编号", 18),
    ("用例名称", 22), ("用例类型", 10), ("优先级", 8), ("执行状态", 10),
    ("是否通过", 9), ("总分", 8), ("失败根因", 12), ("裁判标签", 16),
    ("裁判备注", 36), ("已用轮次", 9), ("耗时(ms)", 10), ("总 Tokens", 10),
    ("最终答案", 40), ("追问自检结论", 30), ("建议排查方向", 36),
    ("执行号", 9), ("Trace UID", 20),
]


def _triple(columns: list[tuple[str, int]]) -> list[tuple[str, str, int]]:
    """(表头, 宽度) → (键, 表头, 宽度)，与 write_table 的三元组列定义对齐。"""
    return [(label, label, width) for label, width in columns]


def stability_columns(round_count: int) -> list[tuple[str, str, int]]:
    """稳定性页列 = 基础 + 每轮动态（状态 / 分数 / 耗时）+ 尾部汇总。"""
    columns = list(STABILITY_BASE_COLUMNS)
    dynamic = min(round_count, MAX_DYNAMIC_ROUND_COLUMNS)
    for index in range(1, dynamic + 1):
        columns.append((f"第 {index} 轮状态", 10))
    for index in range(1, dynamic + 1):
        columns.append((f"第 {index} 轮分数", 10))
    for index in range(1, dynamic + 1):
        columns.append((f"第 {index} 轮耗时(ms)", 11))
    columns.extend(STABILITY_TAIL_COLUMNS)
    return _triple(columns)


# ---------------------------------------------------------------- 行构建
def round_compare_rows(aggregates: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for aggregate in aggregates:
        rows.append(
            [
                aggregate["round_seq"],
                aggregate["chain_label"],
                aggregate["round_index"],
                aggregate["code"],
                aggregate["task_id"],
                aggregate["name"],
                aggregate["status"],
                aggregate["sim_template"],
                aggregate["judge_template"],
                aggregate["grader_version"],
                aggregate["judge_model"],
                aggregate["environment_name"],
                aggregate["started_at"],
                aggregate["finished_at"],
                aggregate["elapsed_seconds"],
                aggregate["case_count"],
                aggregate["pass_count"],
                aggregate["fail_count"],
                aggregate["error_count"],
                aggregate["max_turns_count"],
                aggregate["repetition_count"],
                aggregate["unfinished_count"],
                aggregate["pass_rate"],
                aggregate["avg_score"],
                aggregate["avg_turns"],
                aggregate["avg_latency_ms"],
                aggregate["p95_latency_ms"],
                aggregate["avg_ttft_ms"],
                aggregate["total_tokens"],
                aggregate["retry_count"],
                aggregate["self_check_count"],
                aggregate["delta_pass_rate"],
                aggregate["delta_avg_score"],
                aggregate["delta_latency_pct"],
                aggregate["delta_base_pass_rate"],
                aggregate["delta_base_avg_score"],
                aggregate["trend"],
                aggregate["cumulative_pass_rate"],
                aggregate["new_failed"],
                aggregate["new_passed"],
            ]
        )
    return rows


def cross_issue_rows(
    stabilities: list[CaseStability],
    *,
    history_rates: dict[int, str] | None = None,
) -> list[list[Any]]:
    history = history_rates or {}
    rows: list[list[Any]] = []
    candidates = [
        stability
        for stability in stabilities
        if stability.risk_type or stability.level in ("稳定失败", "波动")
    ]
    candidates.sort(
        key=lambda item: (
            ISSUE_SEVERITY.get("FAIL", 4),
            PRIORITY_ORDER.get(str(item.priority), 9),
            0 if item.level == "稳定失败" else 1,
            str(item.case_code),
        )
    )
    for stability in candidates:
        failure = stability.last_failure or {}
        rows.append(
            [
                len(rows) + 1,
                stability.priority,
                stability.case_code,
                stability.case_name,
                stability.case_type,
                stability.tags,
                stability.owner,
                stability.risk_type or stability.level,
                stability.fail_rounds_text,
                f"{stability.measured}/{stability.passed}",
                round(stability.rate, 1) if stability.rate is not None else None,
                stability.root_rounds_text,
                stability.top_tags,
                (
                    f"第 {stability.last_failure_round} 轮"
                    if stability.last_failure_round
                    else "—"
                ),
                failure.get("judge_conclusion"),
                failure.get("expected_answer"),
                failure.get("final_answer") or "（无有效回复）",
                failure.get("fact_miss"),
                failure.get("diag_summary"),
                failure.get("sql_excerpt"),
                failure.get("last_qa"),
                failure.get("self_check_conclusion"),
                history.get(stability.case_id),
                failure.get("suggest_action"),
                dispose_suggestion(stability),
                failure.get("execution_id"),
                failure.get("trace_uid"),
                None,
                None,
                None,
                None,
            ]
        )
    return rows


def stability_rows(
    stabilities: list[CaseStability],
    *,
    round_count: int,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for stability in stabilities:
        row: list[Any] = [
            stability.case_id,
            stability.case_code,
            stability.case_name,
            stability.case_type,
            stability.priority,
            stability.tags,
            stability.owner,
            f"{len(stability.present_rounds)}/{round_count}",
            (
                "、".join(f"第 {seq}" for seq in stability.missing_rounds) or "无"
                if stability.missing_rounds
                else "无"
            ),
            stability.measured,
            stability.passed,
            round(stability.rate, 1) if stability.rate is not None else None,
            stability.sequence,
        ]
        dynamic = min(round_count, MAX_DYNAMIC_ROUND_COLUMNS)
        for index in range(dynamic):
            item = stability.per_round[index] if index < len(stability.per_round) else None
            row.append(item.get("status") if item else None)
        for index in range(dynamic):
            item = stability.per_round[index] if index < len(stability.per_round) else None
            row.append(_round_score(item) if item else None)
        for index in range(dynamic):
            item = stability.per_round[index] if index < len(stability.per_round) else None
            row.append(_round_latency(item) if item else None)
        failure = stability.last_failure
        row.extend(
            [
                stability.level,
                "是" if stability.flaky else "否",
                stability.regression or None,
                stability.recovery or None,
                stability.max_streak or None,
                stability.first_fail,
                stability.last_fail,
                round(stability.avg_score, 2) if stability.avg_score is not None else None,
                round(stability.max_score, 1) if stability.max_score is not None else None,
                round(stability.min_score, 1) if stability.min_score is not None else None,
                stability.score_range,
                round(stability.score_std, 2) if stability.score_std is not None else None,
                round(stability.score_cv, 1) if stability.score_cv is not None else None,
                stability.score_trend,
                stability.versions_text,
                "是" if stability.version_changed else "否",
                round(stability.avg_latency) if stability.avg_latency else None,
                stability.max_latency,
                stability.avg_turns,
                stability.avg_tokens,
                "、".join(sorted(set(stability.roots))) or "—",
                stability.top_tags,
                dispose_suggestion(stability),
                failure.get("execution_id") if failure else None,
                failure.get("trace_uid") if failure else None,
            ]
        )
        rows.append(row)
    return rows


def detail_rows(rounds: list[RoundData]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for rd in rounds:
        latest = _latest_row_per_case(rd.rows)
        ordered = sorted(latest.values(), key=lambda row: str(row.get("case_code") or ""))
        for row in ordered:
            rows.append(
                [
                    rd.round_seq,
                    rd.chain_label,
                    rd.task.code,
                    row.get("case_code"),
                    row.get("case_name"),
                    row.get("case_type"),
                    row.get("priority"),
                    row.get("status"),
                    row.get("pass_flag"),
                    row.get("total_score"),
                    row.get("root_cause"),
                    row.get("error_tags"),
                    row.get("judge_conclusion"),
                    row.get("turns_used"),
                    row.get("latency_ms"),
                    row.get("total_tokens"),
                    row.get("final_answer"),
                    row.get("self_check_conclusion"),
                    row.get("suggest_action"),
                    row.get("execution_id"),
                    row.get("trace_uid"),
                ]
            )
    return rows


def raw_rows(rounds: list[RoundData]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    sequence = 0
    for rd in rounds:
        for row in rd.rows:
            sequence += 1
            values = [
                sequence if key == "seq" else row.get(key)
                for key, _label, _width in TASK_RESULT_COLUMNS
            ]
            rows.append([rd.round_seq, rd.task.code] + values)
    return rows


# ---------------------------------------------------------------- 概览
def plan_overview_sections(
    plan: BatchTask,
    rounds: list[RoundData],
    aggregates: list[dict[str, Any]],
    stabilities: list[CaseStability],
    *,
    environment_name: str | None,
    scope_label: str,
    exported_by: str | None,
    exported_at: datetime,
    include_raw: bool,
    history_rates: dict[int, str] | None,
) -> list[tuple[str, list[tuple[str, Any]]]]:
    total_rounds = len(rounds)
    total_rows = sum(len(rd.rows) for rd in rounds)
    measured_total = sum(aggregate["measured"] for aggregate in aggregates)
    passed_total = sum(aggregate["pass_count"] for aggregate in aggregates)
    overall_rate = (
        round(passed_total / measured_total * 100, 1) if measured_total else None
    )
    first = aggregates[0] if aggregates else None
    last = aggregates[-1] if aggregates else first

    level_counts: dict[str, int] = {}
    for stability in stabilities:
        level_counts[stability.level] = level_counts.get(stability.level, 0) + 1
    flaky = [s.case_code for s in stabilities if s.flaky]
    regressions = [s.case_code for s in stabilities if s.regression]
    recoveries = [s.case_code for s in stabilities if s.recovery]
    never_pass = [
        s.case_code for s in stabilities if s.measured and s.passed == 0
    ]

    all_rows = [row for rd in rounds for row in rd.rows]
    scores = [score for score in (_round_score(r) for r in all_rows) if score is not None]
    band_low = sum(1 for s in scores if s < 60)
    band_mid = sum(1 for s in scores if 60 <= s < 80)
    band_high = sum(1 for s in scores if s >= 80)
    self_check_done = sum(
        1 for r in all_rows if r.get("self_check_status_code") == "DONE"
    )
    completed_rounds = sum(
        1 for rd in rounds if rd.task.status == "COMPLETED"
    )
    in_progress = [a for a in aggregates if a["unfinished_count"]]

    sections: list[tuple[str, list[tuple[str, Any]]]] = [
        (
            "计划信息",
            [
                ("计划 ID", plan.id),
                ("计划编号", plan.code),
                ("计划名称", plan.name),
                ("任务描述", plan.description),
                ("执行方式", schedule_type_label(plan.schedule_type)),
                ("连续轮次总数", plan.repeat_rounds),
                ("创建人", plan.created_by),
                ("创建时间", format_datetime(plan.created_at)),
                ("执行环境", environment_name),
                ("用例范围", f"{len(stabilities)} 条（并集）"),
            ],
        ),
        (
            "配置一致性（稳定性结论的前提）",
            [(label, value) for label, value in config_consistency(rounds)],
        ),
        (
            "导出范围",
            [
                ("轮次范围", scope_label),
                (
                    "包含轮次",
                    " · ".join(
                        f'第 {a["round_seq"]} 轮 {a["code"]}' for a in aggregates
                    )
                    or "—",
                ),
                (
                    "时间范围",
                    f'{first["started_at"] or "—"} ~ {last["finished_at"] or "—"}'
                    if first
                    else "—",
                ),
                ("尝试口径", "每轮每用例取最新一次尝试（失败重跑不重复计数）"),
                ("明细行数", total_rows),
                (
                    "原始明细",
                    "已包含" if include_raw else "未包含（导出时勾选「包含原始明细」可附加）",
                ),
                ("导出人", exported_by),
                ("导出时间", format_datetime(exported_at)),
            ],
        ),
        (
            "轮次完成度",
            [
                ("轮次总数", total_rounds),
                ("已完成轮次", completed_rounds),
                ("执行中 / 未完成轮次", f"{len(in_progress)} 条" if in_progress else "无"),
                (
                    "数据完整性",
                    "全部轮次已完成，无缺失用例"
                    if not in_progress and not any(
                        s.missing_rounds for s in stabilities
                    )
                    else "存在进行中轮次或缺失用例，跨轮结论仅供参考",
                ),
            ],
        ),
        (
            "跨轮结论",
            [
                ("总执行次数（轮次 × 用例）", measured_total or total_rows),
                ("总通过次数", passed_total),
                ("跨轮总通过率(%)", overall_rate),
                (
                    "首轮 → 末轮通过率(%)",
                    f'{first["pass_rate"]} → {last["pass_rate"]}'
                    f'（{(last["pass_rate"] or 0) - (first["pass_rate"] or 0):+.1f}pp）'
                    if first and last and first is not last
                    else "仅一轮，无对比",
                ),
                (
                    "首轮 → 末轮平均分",
                    f'{first["avg_score"]} → {last["avg_score"]}'
                    if first and last and first is not last
                    else "仅一轮，无对比",
                ),
                (
                    "最需关注用例",
                    "、".join(never_pass[:5]) or "无",
                ),
            ],
        ),
        (
            "稳定性分布",
            [
                ("稳定通过", level_counts.get("稳定通过", 0)),
                ("波动", level_counts.get("波动", 0)),
                ("稳定失败", level_counts.get("稳定失败", 0)),
                ("数据不足", level_counts.get("数据不足", 0)),
                ("Flaky 用例", f"{len(flaky)} 条：{'、'.join(flaky) or '无'}"),
                ("回归用例（先过后败）", f"{len(regressions)} 条：{'、'.join(regressions) or '无'}"),
                ("恢复用例（先败后过）", f"{len(recoveries)} 条：{'、'.join(recoveries) or '无'}"),
                ("从未通过", f"{len(never_pass)} 条：{'、'.join(never_pass) or '无'}"),
            ],
        ),
        (
            "失败画像（跨轮汇总）",
            [
                ("失败根因分布", distribution_text(all_rows, "root_cause", ROOT_CAUSE_LABELS)),
                (
                    "错误标签 Top3",
                    _top_tags_text(all_rows),
                ),
                (
                    "分数带分布",
                    f"<60 分 {band_low} 次 · 60~80 分 {band_mid} 次 · ≥80 分 {band_high} 次"
                    f"（另 {total_rows - len(scores)} 次无分数）",
                ),
                (
                    "追问自检统计",
                    f"已追问 {self_check_done} 次 · 未触发 {total_rows - self_check_done} 次",
                ),
                (
                    "近10次通过率(全库) 覆盖",
                    f"{len(history_rates or {})} / {len(stabilities)} 条用例",
                ),
            ],
        ),
    ]
    return sections


def _top_tags_text(rows: list[dict[str, Any]]) -> str:
    counter: dict[str, int] = {}
    for row in rows:
        for tag in str(row.get("error_tags") or "").split("、"):
            text = tag.strip()
            if text:
                counter[text] = counter.get(text, 0) + 1
    top = sorted(counter.items(), key=lambda kv: -kv[1])[:3]
    return "、".join(f"{tag} ×{count}" for tag, count in top) or "—"


CALIBRATION_SECTIONS: list[tuple[str, list[tuple[str, Any]]]] = [
    (
        "术语与口径",
        [
            ("执行轮次 vs 对话轮次", "执行轮次 = 计划中的第 k/N 批次；对话轮次 = 单用例会话内的交互轮数。"),
            ("通过率分母", "只统计已产生终态结果（通过/失败/异常/轮次超限/复读熔断/已终止）的执行；执行中/等待中不计入分母。"),
            ("尝试口径", "每执行轮次、每用例取最新一次尝试，失败自动重跑不重复计数。"),
            ("用例通过率", "通过轮次数 / 已测轮次数（已测 = 已产生终态结果的轮次）。"),
            ("稳定性分级", "已测 ≥2 轮：100% 稳定通过、0% 稳定失败、其余波动；已测 <2 轮：数据不足。"),
            ("Flaky", "既有通过轮次又有非通过轮次的用例（含环境异常导致的波动）。"),
            ("回归 / 恢复", "相邻轮次通过 → 非通过计 1 次回归；非通过 → 通过计 1 次恢复。"),
            ("缺失轮次 / 未纳入轮次", "缺失 = 该轮用例范围内有用例但无执行记录；未纳入 = 该轮任务范围不包含该用例。"),
            ("分数标准差 / 变异系数", "总体标准差；变异系数 = 标准差 / 平均分 × 100%，平均分为 0 时留空。"),
            ("较上一轮 / 较首轮", "Δ 为当前轮减上一轮（或首轮）差值；通过率单位百分点(pp)；耗时为相对变化百分比。"),
            ("趋势判定", "通过率上升、或持平但平均分上升记「改善」；反之记「恶化」；其余「持平」；首轮「基线」。"),
            ("本轮新失败 / 新通过", "相对上一轮：通过 → 非通过为新失败；非通过 → 通过为新通过。"),
            ("风险类型", "稳定失败 / 波动 / 幸运通过（答案对但无取数证据）/ 压线通过（通过但总分低于阈值）。"),
            ("配置一致性", "轮次间的模板 / 判分器 / 模型 / 环境 / 用例集比对；任一不一致时，稳定性结论需结合变更分析。"),
            ("长文本截断", "关键页超长文本截断并标注，完整内容见原始明细页或 JSONL 导出。"),
        ],
    ),
    (
        "操作提示",
        [
            ("超链接", "执行号 / Trace UID 可点击跳转平台执行详情页。"),
            ("人工跟进列", "处理人 / 处理结论 / 问题归属（下拉）/ 缺陷单号，留空供人工填写，回填后可作为处理台账。"),
            ("筛选", "明细页首行已启用筛选，可按状态 / 根因 / 负责人快速切片。"),
        ],
    ),
]


# ---------------------------------------------------------------- 工作簿 / JSONL
def build_rounds_xlsx(
    plan: BatchTask,
    rounds: list[RoundData],
    *,
    environment_name: str | None = None,
    scope_label: str = "最近一次轮次链",
    exported_by: str | None = None,
    exported_at: datetime | None = None,
    include_raw: bool = False,
    base_url: str = "",
    borderline_score: float = 70.0,
    history_rates: dict[int, str] | None = None,
) -> bytes:
    """生成计划多轮次报表工作簿，返回 xlsx 字节。"""
    if not rounds:
        raise ValueError("rounds 不能为空")
    exported_at = exported_at or datetime.now()
    aggregates = round_aggregates(rounds)
    stabilities = compute_case_stability(rounds, borderline_score=borderline_score)

    workbook = Workbook()
    overview = workbook.active
    overview.title = "综合概览"
    write_overview(
        overview,
        plan_overview_sections(
            plan,
            rounds,
            aggregates,
            stabilities,
            environment_name=environment_name,
            scope_label=scope_label,
            exported_by=exported_by,
            exported_at=exported_at,
            include_raw=include_raw,
            history_rates=history_rates,
        ),
    )

    compare_sheet = workbook.create_sheet("轮次对比")
    compare_rows = round_compare_rows(aggregates)
    compare_columns = _triple(ROUND_COMPARE_COLUMNS)
    write_table(compare_sheet, compare_columns, compare_rows, color_headers=("趋势判定",))

    issue_sheet = workbook.create_sheet("跨轮问题清单")
    issues = cross_issue_rows(stabilities, history_rates=history_rates)
    issue_columns = _triple(CROSS_ISSUE_COLUMNS)
    write_table(issue_sheet, issue_columns, issues)
    if base_url:
        apply_hyperlinks(
            issue_sheet, issue_columns, len(issues), "最近失败执行号", exec_url_builder(base_url)
        )
        apply_hyperlinks(
            issue_sheet, issue_columns, len(issues), "最近失败 Trace UID", trace_url_builder(base_url)
        )
    apply_dropdown(issue_sheet, issue_columns, len(issues), "问题归属", OWNERSHIP_OPTIONS)

    stability_sheet = workbook.create_sheet("用例稳定性")
    columns = stability_columns(len(rounds))
    rows = stability_rows(stabilities, round_count=len(rounds))
    dynamic = min(len(rounds), MAX_DYNAMIC_ROUND_COLUMNS)
    color_headers = ("稳定性分级", "执行期间用例变更") + tuple(
        f"第 {index} 轮状态" for index in range(1, dynamic + 1)
    )
    write_table(
        stability_sheet,
        columns,
        rows,
        color_headers=color_headers,
        score_headers=("平均分",),
    )
    if base_url:
        apply_hyperlinks(
            stability_sheet, columns, len(rows), "最近失败执行号", exec_url_builder(base_url)
        )
        apply_hyperlinks(
            stability_sheet, columns, len(rows), "最近失败 Trace UID", trace_url_builder(base_url)
        )

    detail_sheet = workbook.create_sheet("多轮用例明细")
    detail = detail_rows(rounds)
    detail_columns = _triple(DETAIL_COLUMNS)
    write_table(detail_sheet, detail_columns, detail)
    if base_url:
        apply_hyperlinks(detail_sheet, detail_columns, len(detail), "执行号", exec_url_builder(base_url))
        apply_hyperlinks(detail_sheet, detail_columns, len(detail), "Trace UID", trace_url_builder(base_url))

    write_overview(workbook.create_sheet("口径说明"), CALIBRATION_SECTIONS)

    if include_raw:
        raw_sheet = workbook.create_sheet("原始明细")
        raw = raw_rows(rounds)
        raw_columns = [
            ("全局轮次", "全局轮次", 8),
            ("批次编号", "批次编号", 21),
        ] + list(TASK_RESULT_COLUMNS)
        write_table(
            raw_sheet, raw_columns, raw, color_headers=("执行状态", "是否通过")
        )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_rounds_jsonl(
    plan: BatchTask,
    rounds: list[RoundData],
    *,
    environment_name: str | None = None,
    scope_label: str = "最近一次轮次链",
    exported_by: str | None = None,
    exported_at: datetime | None = None,
    borderline_score: float = 70.0,
    history_rates: dict[int, str] | None = None,
) -> bytes:
    """生成计划多轮次 JSONL：meta / round_summary / case_stability / row。"""
    if not rounds:
        raise ValueError("rounds 不能为空")
    exported_at = exported_at or datetime.now()
    aggregates = round_aggregates(rounds)
    stabilities = compute_case_stability(rounds, borderline_score=borderline_score)

    meta: dict[str, Any] = {
        "kind": "meta",
        "schema": ROUNDS_JSONL_SCHEMA_NAME,
        "schema_version": ROUNDS_JSONL_SCHEMA_VERSION,
        "plan_id": plan.id,
        "plan_code": plan.code,
        "plan_name": plan.name,
        "environment_name": environment_name,
        "scope_label": scope_label,
        "round_count": len(rounds),
        "case_count": len(stabilities),
        "exported_by": exported_by,
        "exported_at": exported_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    lines = [_dump_line(meta)]
    for aggregate in aggregates:
        payload = {key: value for key, value in aggregate.items() if key != "_case_status"}
        payload["kind"] = "round_summary"
        lines.append(_dump_line(payload))
    for stability in stabilities:
        payload = stability.to_dict()
        payload["kind"] = "case_stability"
        payload["dispose_suggestion"] = dispose_suggestion(stability)
        lines.append(_dump_line(payload))
    for rd in rounds:
        for index, row in enumerate(rd.rows, start=1):
            record: dict[str, Any] = {
                "kind": "row",
                "seq": index,
                "round_seq": rd.round_seq,
                "chain_index": rd.chain_index,
                "round_index": rd.task.round_index,
                "task_id": rd.task.id,
                "task_code": rd.task.code,
            }
            for key, _label, _width in TASK_RESULT_COLUMNS:
                if key == "seq":
                    continue
                record[key] = row.get(key)
            for key, value in row.items():
                if key not in record:
                    record[key] = value
            lines.append(_dump_line(record))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _dump_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)
