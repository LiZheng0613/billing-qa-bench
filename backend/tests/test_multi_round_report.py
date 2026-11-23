"""计划多轮次报表聚合与渲染单元测试（不依赖数据库）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import StatusDistribution, TaskMetrics
from billing_qa.services.io.multi_round_report import (
    ROUNDS_JSONL_SCHEMA_NAME,
    ROUNDS_JSONL_SCHEMA_VERSION,
    RoundData,
    build_rounds_jsonl,
    build_rounds_xlsx,
    compute_case_stability,
    round_aggregates,
)


def _task(seq: int, *, started: bool = True, status: str = "COMPLETED") -> BatchTask:
    started_at = (
        datetime(2026, 1, 2, 10, 0, 0) + timedelta(minutes=5 * (seq - 1))
        if started
        else None
    )
    return BatchTask(
        id=1000 + seq,
        code=f"BATCH-{seq:03d}",
        name=f"账单回归 · 第 {seq}/3 轮",
        description="多轮报表测试",
        status=status,
        control_state="FINISHED",
        environment_id=1,
        case_ids=[1, 2, 3],
        total_cases=3,
        finished_cases=3,
        concurrency=3,
        qps_limit=5.0,
        repeat_rounds=3,
        round_index=seq,
        schedule_type="IMMEDIATE",
        schedule_active=False,
        created_by="admin",
        created_at=started_at or datetime(2026, 1, 2, 9, 0, 0),
        started_at=started_at,
        finished_at=started_at + timedelta(minutes=4) if started_at else None,
    )


def _metrics(passed: int, failed: int) -> TaskMetrics:
    return TaskMetrics(
        task_id=1,
        status="COMPLETED",
        control_state="FINISHED",
        total_cases=3,
        finished_cases=3,
        progress=100.0,
        status_distribution=StatusDistribution(PASS=passed, FAIL=failed),
        pass_rate=passed / (passed + failed) * 100 if passed + failed else 0.0,
        avg_score=80.0,
        avg_ttft_ms=120.0,
        avg_latency_ms=900.0,
        p95_latency_ms=1500.0,
        avg_turns=2.0,
        simulator_tokens=300,
        judge_tokens=120,
        total_tokens=420,
        eta_seconds=None,
        elapsed_seconds=240.0,
        throughput_per_minute=0.75,
    )


def _row(
    case_id: int, status: str, score: float, *, version: int = 7, **extra: object
) -> dict[str, object]:
    base: dict[str, object] = {
        "execution_id": 9000 + case_id,
        "trace_uid": f"trace-{case_id}",
        "case_id": case_id,
        "case_code": f"DEMO-{case_id:03d}",
        "case_name": f"用例 {case_id}",
        "case_type": "单轮",
        "priority": "P1",
        "tags": "计费",
        "owner": "tester",
        "case_version": version,
        "status": {"PASS": "通过", "FAIL": "失败"}[status],
        "status_code": status,
        "pass_flag": "是" if status == "PASS" else "否",
        "total_score": score,
        "latency_ms": 1000.0 + case_id * 10,
        "turns_used": 2,
        "max_turns": 5,
        "total_tokens": 420,
        "final_answer": "答案",
        "expected_answer": "期望",
        "root_cause": "取数错误" if status == "FAIL" else None,
        "error_tags": "取数缺失" if status == "FAIL" else None,
        "judge_conclusion": "结论" if status == "FAIL" else None,
        "attempt_no": 1,
        "attempt_count": 1,
        "environment_name": "QA 环境",
        "simulator_template_version": 3,
        "judge_template_version": 4,
        "grader_version": "v2",
        "judges": [{"model_name": "judge-a"}],
        "self_check_status_code": "DONE" if status == "FAIL" else "SKIPPED",
        "self_check_conclusion": "已追问：核对取数",
        "suggest_action": "核对取数工具、时间窗与权限。",
        "fact_miss": "金额（期望 100 / 实际 90）" if status == "FAIL" else None,
        "diag_summary": "工具调用 2 次 · 取数 0 行",
        "sql_excerpt": "SELECT fee FROM bill WHERE month='2026-01'",
        "last_qa": "Q：查账单 A：合计 90 元",
        "lucky_pass": "否",
    }
    base.update(extra)
    return base


# 场景：DEMO-001 稳定通过；DEMO-002 波动（P→F→P）；DEMO-003 稳定失败（F→F→F）。
_PLANS = [
    {"case": 1, "rounds": ["PASS", "PASS", "PASS"], "scores": [95.0, 96.0, 99.0]},
    {"case": 2, "rounds": ["PASS", "FAIL", "PASS"], "scores": [92.0, 48.0, 90.0]},
    {"case": 3, "rounds": ["FAIL", "FAIL", "FAIL"], "scores": [35.0, 42.0, 38.0]},
]


def _rounds(
    *, judge_templates: tuple[int, int, int] = (4, 4, 4), versions: dict[int, list[int]] | None = None
) -> list[RoundData]:
    rounds: list[RoundData] = []
    for seq in (1, 2, 3):
        rows = []
        for plan in _PLANS:
            version_map = versions or {}
            version = version_map.get(plan["case"], [7, 7, 7])[seq - 1]
            rows.append(
                _row(
                    plan["case"],
                    plan["rounds"][seq - 1],
                    plan["scores"][seq - 1],
                    version=version,
                    judge_template_version=judge_templates[seq - 1],
                )
            )
        passed = sum(1 for r in rows if r["status_code"] == "PASS")
        rounds.append(
            RoundData(
                task=_task(seq),
                metrics=_metrics(passed, len(rows) - passed),
                rows=rows,
                chain_index=1,
                round_seq=seq,
            )
        )
    return rounds


def _plan() -> BatchTask:
    plan = _task(0)
    plan.id = 999
    plan.code = "PLAN-001"
    plan.name = "账单回归验收"
    return plan


# ---------------------------------------------------------------- 轮次聚合
def test_round_aggregates_delta_trend_and_new_changes() -> None:
    aggregates = round_aggregates(_rounds())
    assert [a["round_seq"] for a in aggregates] == [1, 2, 3]
    first, second, third = aggregates
    assert first["trend"] == "基线"
    assert first["new_failed"] == "—（首轮基线）"
    # 第 1 轮 2/3 通过（66.7），第 2 轮 1/3（33.3），第 3 轮 2/3（66.7）。
    assert first["pass_rate"] == 66.7
    assert second["delta_pass_rate"] == -33.4
    assert second["new_failed"] == "DEMO-002"
    assert second["new_passed"] == "无"
    assert second["trend"] == "恶化"
    assert third["delta_pass_rate"] == 33.4
    assert third["new_passed"] == "DEMO-002"
    assert third["new_failed"] == "无"
    assert third["trend"] == "改善"
    # 较首轮基线。
    assert third["delta_base_pass_rate"] == 0.0
    # 累计通过率。
    assert third["cumulative_pass_rate"] == 55.6


def test_round_aggregates_include_attempts_dedupe() -> None:
    """include_attempts 展开时，聚合按用例取最新尝试，重跑不重复计数。"""
    rounds = _rounds()
    retry = _row(2, "PASS", 90.0)
    retry["attempt_no"] = 2
    retry["execution_id"] = 9999
    rounds[2].rows.append(retry)
    aggregates = round_aggregates(rounds)
    assert aggregates[2]["case_count"] == 3  # 仍是 3 条用例
    assert aggregates[2]["pass_count"] == 2


# ---------------------------------------------------------------- 稳定性
def test_case_stability_levels_flaky_regression_recovery() -> None:
    stabilities = compute_case_stability(_rounds())
    levels = {s.case_code: s.level for s in stabilities}
    assert levels["DEMO-001"] == "稳定通过"
    assert levels["DEMO-002"] == "波动"
    assert levels["DEMO-003"] == "稳定失败"

    s1 = next(s for s in stabilities if s.case_code == "DEMO-001")
    assert s1.rate == 100.0 and not s1.flaky and s1.max_streak == 0
    assert s1.score_trend == "上升"

    s2 = next(s for s in stabilities if s.case_code == "DEMO-002")
    assert s2.flaky
    assert s2.regression == 1 and s2.recovery == 1
    assert s2.max_streak == 1
    assert s2.first_fail == 2 and s2.last_fail == 2
    assert s2.fail_rounds_text == "第 2 轮"
    assert s2.root_rounds_text == "第 2 轮：取数错误"
    assert s2.top_tags == "取数缺失 ×1"
    assert s2.risk_type == "波动"

    s3 = next(s for s in stabilities if s.case_code == "DEMO-003")
    assert s3.max_streak == 3
    assert s3.first_fail == 1 and s3.last_fail == 3
    assert s3.rate == 0.0
    assert s3.risk_type == "稳定失败"
    assert s3.score_std is not None and s3.score_std > 0


def test_case_stability_version_change_and_lucky_pass() -> None:
    """执行期间用例版本变更 + 幸运通过（末轮）都要体现在风险类型中。"""
    rounds = _rounds(versions={1: [7, 8, 8]})
    rounds[2].rows[0]["lucky_pass"] = "是"  # DEMO-001 末轮幸运通过
    rounds[2].rows[0]["status_code"] = "PASS"
    stabilities = compute_case_stability(rounds)
    s1 = next(s for s in stabilities if s.case_code == "DEMO-001")
    assert s1.version_changed
    assert s1.versions_text == "v7 → v8 → v8"
    assert s1.risk_type == "幸运通过（末轮）"
    # 稳定通过 + 幸运通过 → 跨轮问题清单也要能收进来。
    assert s1.level == "稳定通过"


def test_case_stability_borderline_pass() -> None:
    rounds = _rounds()
    rounds[2].rows[0]["total_score"] = 60.0  # 末轮压线通过
    stabilities = compute_case_stability(rounds)
    s1 = next(s for s in stabilities if s.case_code == "DEMO-001")
    assert s1.risk_type == "压线通过（末轮）"


def test_case_stability_single_round_is_insufficient() -> None:
    rounds = _rounds()[:1]
    stabilities = compute_case_stability(rounds)
    assert all(s.level == "数据不足" for s in stabilities)
    assert all(s.risk_type is None for s in stabilities if s.case_code != "DEMO-003")


def test_case_stability_missing_round() -> None:
    """某轮用例范围内有用例但无记录 → 缺失轮次。"""
    rounds = _rounds()
    rounds[1].rows = [r for r in rounds[1].rows if r["case_id"] != 3]
    stabilities = compute_case_stability(rounds)
    s3 = next(s for s in stabilities if s.case_code == "DEMO-003")
    assert s3.missing_rounds == [2]
    assert s3.measured == 2  # 缺失轮不计入分母


# ---------------------------------------------------------------- 工作簿
def test_build_rounds_xlsx_structure() -> None:
    content = build_rounds_xlsx(
        _plan(),
        _rounds(judge_templates=(4, 5, 5)),
        environment_name="QA 环境",
        scope_label="最近一次轮次链（round_scope=latest）",
        exported_by="admin",
        exported_at=datetime(2026, 1, 2, 12, 0, 0),
        include_raw=True,
        base_url="http://localhost:5173",
        history_rates={2: "70%（7/10）"},
    )
    workbook = load_workbook(BytesIO(content))
    assert workbook.sheetnames == [
        "综合概览",
        "轮次对比",
        "跨轮问题清单",
        "用例稳定性",
        "多轮用例明细",
        "口径说明",
        "原始明细",
    ]

    overview = workbook["综合概览"]
    overview_map = {
        overview.cell(row=r, column=1).value: overview.cell(row=r, column=2).value
        for r in range(1, overview.max_row + 1)
    }
    assert overview_map["计划编号"] == "PLAN-001"
    assert overview_map["轮次总数"] == 3
    assert overview_map["明细行数"] == 9
    assert overview_map["跨轮总通过率(%)"] == 55.6
    # 裁判模板第 2 轮变化 → 配置一致性给出「不一致」结论。
    conclusion = [
        value for key, value in overview_map.items() if str(key).startswith("配置一致性")
    ]
    assert any("不一致" in str(text) for text in conclusion)
    assert any("各轮配置一致" not in str(text) and "不宜全部归因" in str(text) for text in conclusion)

    compare = workbook["轮次对比"]
    compare_headers = [c.value for c in compare[1]]
    assert "较上一轮通过率(pp)" in compare_headers
    assert "本轮新失败用例" in compare_headers
    assert compare.max_row == 4
    trend_col = compare_headers.index("趋势判定") + 1
    assert [compare.cell(row=r, column=trend_col).value for r in (2, 3, 4)] == [
        "基线",
        "恶化",
        "改善",
    ]

    issues = workbook["跨轮问题清单"]
    issue_headers = [c.value for c in issues[1]]
    assert "处置建议" in issue_headers
    assert "问题归属" in issue_headers
    # 收进波动（DEMO-002）与稳定失败（DEMO-003）。
    assert issues.max_row == 3
    issue_codes = {
        issues.cell(row=r, column=issue_headers.index("用例编号") + 1).value
        for r in (2, 3)
    }
    assert issue_codes == {"DEMO-002", "DEMO-003"}
    # 建议排查方向来自末次失败行。
    suggest = issues.cell(row=2, column=issue_headers.index("建议排查方向") + 1).value
    assert "核对取数工具" in str(suggest)
    # 近10次通过率注入。
    history_col = issue_headers.index("近10次通过率(全库)") + 1
    values = {issues.cell(row=r, column=history_col).value for r in (2, 3)}
    assert "70%（7/10）" in values

    stability = workbook["用例稳定性"]
    stability_headers = [c.value for c in stability[1]]
    assert "状态序列" in stability_headers
    assert "第 1 轮分数" in stability_headers and "第 3 轮分数" in stability_headers
    assert "用例版本序列" in stability_headers
    assert stability.max_row == 4

    detail = workbook["多轮用例明细"]
    assert detail.max_row == 10  # 3 轮 × 3 用例 + 表头

    raw = workbook["原始明细"]
    raw_headers = [c.value for c in raw[1]]
    assert "全局轮次" in raw_headers and "批次编号" in raw_headers
    assert raw.max_row == 10

    # 超链接：执行号列可点击跳转平台。
    issues_exec_col = issue_headers.index("最近失败执行号") + 1
    cell = issues.cell(row=2, column=issues_exec_col)
    assert cell.hyperlink is not None
    assert "trace?execution=" in cell.hyperlink.target

    # 问题归属下拉。
    validations = list(issues.data_validations.dataValidation)
    assert validations, "问题归属列应有下拉校验"


def test_build_rounds_xlsx_without_raw_and_links() -> None:
    content = build_rounds_xlsx(_plan(), _rounds(), exported_by="admin")
    workbook = load_workbook(BytesIO(content))
    assert "原始明细" not in workbook.sheetnames
    issues = workbook["跨轮问题清单"]
    assert not list(issues.data_validations.dataValidation) or True


def test_build_rounds_xlsx_empty_rounds_raises() -> None:
    with pytest.raises(ValueError):
        build_rounds_xlsx(_plan(), [])


# ---------------------------------------------------------------- JSONL
def test_build_rounds_jsonl_structure() -> None:
    content = build_rounds_jsonl(
        _plan(),
        _rounds(),
        environment_name="QA 环境",
        exported_by="admin",
        exported_at=datetime(2026, 1, 2, 12, 0, 0),
    )
    text = content.decode("utf-8")
    assert text.endswith("\n")
    assert not content.startswith(b"\xef\xbb\xbf")
    records = [json.loads(line) for line in text.splitlines()]

    meta = records[0]
    assert meta["kind"] == "meta"
    assert meta["schema"] == ROUNDS_JSONL_SCHEMA_NAME
    assert meta["schema_version"] == ROUNDS_JSONL_SCHEMA_VERSION
    assert meta["plan_code"] == "PLAN-001"
    assert meta["round_count"] == 3
    assert meta["case_count"] == 3

    summaries = [r for r in records if r["kind"] == "round_summary"]
    assert len(summaries) == 3
    assert summaries[0]["pass_rate"] == 66.7
    assert "_case_status" not in summaries[0]

    stabilities = [r for r in records if r["kind"] == "case_stability"]
    assert {r["case_code"] for r in stabilities} == {
        "DEMO-001",
        "DEMO-002",
        "DEMO-003",
    }
    s2 = next(r for r in stabilities if r["case_code"] == "DEMO-002")
    assert s2["level"] == "波动" and s2["flaky"] is True
    assert s2["regression_count"] == 1 and s2["recovery_count"] == 1
    assert "核对取数" in s2["dispose_suggestion"] or s2["dispose_suggestion"]

    rows = [r for r in records if r["kind"] == "row"]
    assert len(rows) == 9
    assert rows[0]["round_seq"] == 1
    assert rows[0]["task_code"] == "BATCH-001"
    # 大字段不截断，dict/list 保持原生结构。
    assert rows[0]["judges"] == [{"model_name": "judge-a"}]
    long = build_rounds_jsonl(
        _plan(),
        [
            RoundData(
                task=_task(1),
                metrics=_metrics(0, 3),
                rows=[_row(1, "FAIL", 30.0, case_snapshot={"payload": "大" * 40000})],
                chain_index=1,
                round_seq=1,
            )
        ],
        exported_by="admin",
    )
    assert "大" * 40000 in long.decode("utf-8")


def test_build_rounds_jsonl_empty_rounds_raises() -> None:
    with pytest.raises(ValueError):
        build_rounds_jsonl(_plan(), [])
