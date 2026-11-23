"""单用例历史执行导出（趋势报表）单元测试（不依赖数据库）。"""

from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO

from openpyxl import load_workbook

from billing_qa.models.test_case import TestCase as CaseModel
from billing_qa.services.io.case_history_report import (
    CASE_HISTORY_COLUMNS,
    CASE_HISTORY_JSONL_SCHEMA_NAME,
    CASE_HISTORY_JSONL_SCHEMA_VERSION,
    build_case_history_jsonl,
    build_case_history_xlsx,
    summarize_case_history,
)


def _case() -> CaseModel:
    return CaseModel(
        id=7,
        code="DEMO-HIST-001",
        name="账单月度汇总",
        case_type="SINGLE",
        status="ACTIVE",
        priority="P1",
        tags=["计费", "账单"],
        current_version=3,
        owner="tester",
        dataset_version="v1",
        grading_mode="JUDGE",
        risk_level="LOW",
        review_status="APPROVED",
        expected_answer="合计 100 元",
    )


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "execution_id": 1001,
        "trace_uid": "trace-1001",
        "case_id": 7,
        "case_version": 3,
        "case_code": "DEMO-HIST-001",
        "case_name": "账单月度汇总",
        "case_type": "单轮",
        "priority": "P1",
        "case_status": "启用",
        "tags": "计费,账单",
        "owner": "tester",
        "task_id": 42,
        "task_name": "批量回归验收",
        "source": "批量执行",
        "status": "通过",
        "status_code": "PASS",
        "pass_flag": "是",
        "total_score": 95.0,
        "termination_status": "目标达成",
        "attempt_no": 1,
        "attempt_count": 1,
        "error_kind": None,
        "root_cause": None,
        "lucky_pass": "否",
        "turns_used": 2,
        "max_turns": 5,
        "latency_ms": 850.0,
        "ttft_ms": 110.0,
        "simulator_tokens": 200,
        "judge_tokens": 80,
        "total_tokens": 280,
        "grader_version": "v2",
        "environment_id": 1,
        "environment_name": "QA 环境",
        "started_at": "2026-01-02 10:00:00",
        "finished_at": "2026-01-02 10:00:01",
        "error_message": None,
        "judge_notes": "答案与事实一致",
        "final_answer": "您本月合计 100 元。",
        "expected_answer": "合计 100 元",
        "self_check_status": "未开启",
        "rule_result": {"passed": True},
        "judge_result": {"pass": True, "notes": ["答案与事实一致"]},
        "answer_grading": {"passed": True},
        "fact_results": [{"fact": "amount", "passed": True}],
        "diagnostics": {"tool_calls": 2},
        "judges": [{"model_name": "judge-a", "pass_flag": True}],
        "turns": [{"turn": 1, "query": "本月账单多少"}],
        "case_snapshot": {"code": "DEMO-HIST-001", "expected_answer": "合计 100 元"},
        # enrich_result_rows 的派生字段（收集阶段已补充）
        "judge_conclusion": "答案与事实一致",
        "error_tags": None,
        "fact_hit": "1/1",
        "fact_miss": None,
        "rule_failures": None,
        "diag_summary": "工具调用 2 次",
        "sql_excerpt": "SELECT SUM(amount) ...",
        "last_qa": "Q: 本月账单多少 A: 您本月合计 100 元。",
        "fact_missed": False,
        "rule_failed": False,
        "suggest_action": None,
    }
    row.update(overrides)
    return row


def _history_rows() -> list[dict[str, object]]:
    """时间升序的 4 次历史执行：两次通过、一次失败、一次无判定。"""
    return [
        _row(
            execution_id=1001,
            trace_uid="trace-1001",
            started_at="2026-01-02 10:00:00",
            case_version=2,
            pass_flag="是",
            total_score=90.0,
        ),
        _row(
            execution_id=1002,
            trace_uid="trace-1002",
            started_at="2026-01-03 10:00:00",
            case_version=2,
            pass_flag="否",
            status="失败",
            status_code="FAIL",
            total_score=40.0,
            error_kind="模型异常",
            root_cause="取数错误",
            fact_miss="amount 未命中",
            fact_missed=True,
            suggest_action="核对取数口径",
        ),
        _row(
            execution_id=1003,
            trace_uid="trace-1003",
            started_at="2026-01-04 10:00:00",
            case_version=3,
            pass_flag=None,
            status="执行中",
            status_code="RUNNING",
            total_score=None,
            latency_ms=None,
            task_name=None,
            task_id=None,
        ),
        _row(
            execution_id=1004,
            trace_uid="trace-1004",
            started_at="2026-01-05 10:00:00",
            case_version=3,
            pass_flag="是",
            total_score=95.0,
        ),
    ]


def test_summarize_case_history() -> None:
    stats = summarize_case_history(_history_rows())

    assert stats["total"] == 4
    assert stats["scored_count"] == 3
    assert stats["no_verdict_count"] == 1
    assert stats["pass_count"] == 2
    assert stats["fail_count"] == 1
    assert stats["pass_rate"] == 66.67
    assert stats["avg_score"] == 75.0
    assert stats["first_started_at"] == "2026-01-02 10:00:00"
    assert stats["last_started_at"] == "2026-01-05 10:00:00"
    assert stats["span_days"] == "3.0 天"
    assert stats["version_distribution"] == "2×2、3×2"
    # 近窗口（默认 20 > 行数）覆盖全部行：有通过也有失败 → Flaky。
    assert stats["near_sequence"] == "✓✗－✓"
    assert stats["flaky"] is True
    assert "Flaky" in stats["stability"]


def test_build_case_history_workbook() -> None:
    content = build_case_history_xlsx(
        _case(),
        _history_rows(),
        exported_by="admin",
        exported_at=datetime(2026, 1, 5, 12, 0, 0),
        include_sandbox=False,
        base_url="http://localhost:5173",
    )

    workbook = load_workbook(BytesIO(content))
    assert workbook.sheetnames == ["执行概览", "历史执行明细", "口径说明"]

    overview = workbook["执行概览"]
    overview_map = {
        overview.cell(row=row, column=1).value: overview.cell(row=row, column=2).value
        for row in range(1, overview.max_row + 1)
    }
    assert overview_map["用例编号"] == "DEMO-HIST-001"
    assert overview_map["当前版本"] == "v3"
    assert overview_map["总执行次数"] == 4
    assert overview_map["通过率（有判定口径）"] == "66.67%"
    assert overview_map["结果序列（按时间先后）"] == "✓✗－✓"
    assert "Flaky" in overview_map["稳定性"]
    assert overview_map["数据口径"] == "全部历史执行（不含草稿试跑）"

    detail = workbook["历史执行明细"]
    headers = [cell.value for cell in detail[1]]
    assert [label for _key, label, _width in CASE_HISTORY_COLUMNS] == headers
    assert detail.max_row == 5  # 表头 + 4 行
    # 时间升序：序号即第 N 次。
    assert detail.cell(row=2, column=1).value == 1
    assert detail.cell(row=5, column=1).value == 4
    assert detail.cell(row=3, column=headers.index("总分") + 1).value == 40.0
    # 执行号 / Trace UID 渲染为平台超链接。
    exec_cell = detail.cell(row=2, column=headers.index("执行号") + 1)
    assert exec_cell.hyperlink.target == "http://localhost:5173/trace?execution=1001"
    trace_cell = detail.cell(row=2, column=headers.index("Trace UID") + 1)
    assert trace_cell.hyperlink.target == "http://localhost:5173/trace?trace_uid=trace-1001"
    # 期望答案取自各次执行的快照。
    assert (
        detail.cell(row=2, column=headers.index("期望答案(执行时)") + 1).value
        == "合计 100 元"
    )

    notes = workbook["口径说明"]
    notes_map = {
        notes.cell(row=row, column=1).value: notes.cell(row=row, column=2).value
        for row in range(1, notes.max_row + 1)
    }
    assert "JSONL 不截断" in notes_map


def test_build_case_history_jsonl() -> None:
    long_answer = "长" * 40000
    rows = _history_rows()
    rows.append(
        _row(
            execution_id=1005,
            trace_uid="trace-1005",
            started_at="2026-01-06 10:00:00",
            final_answer=long_answer,
            turns=[{"turn": 1, "final_answer": long_answer}],
        )
    )
    content = build_case_history_jsonl(
        _case(),
        rows,
        include_sandbox=True,
        exported_by="admin",
        exported_at=datetime(2026, 1, 6, 12, 0, 0),
    )
    lines = content.decode("utf-8").strip().split("\n")
    assert len(lines) == 1 + len(rows)

    meta = json.loads(lines[0])
    assert meta["kind"] == "meta"
    assert meta["schema"] == CASE_HISTORY_JSONL_SCHEMA_NAME
    assert meta["schema_version"] == CASE_HISTORY_JSONL_SCHEMA_VERSION
    assert meta["case_code"] == "DEMO-HIST-001"
    assert meta["case_current_version"] == 3
    assert meta["include_sandbox"] is True
    assert meta["row_count"] == len(rows)
    assert meta["stats"]["total"] == len(rows)
    assert meta["stats"]["pass_rate"] == 75.0

    for index, line in enumerate(lines[1:], start=1):
        record = json.loads(line)
        assert record["kind"] == "row"
        assert record["seq"] == index
    last = json.loads(lines[-1])
    # JSONL 永不截断：超长答案与轮次轨迹完整保留。
    assert len(last["final_answer"]) == 40000
    assert last["turns"][0]["final_answer"] == long_answer
    assert last["suggest_action"] is None or isinstance(last["suggest_action"], str)
