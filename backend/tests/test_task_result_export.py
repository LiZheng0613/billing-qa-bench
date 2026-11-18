"""批量任务结果 Excel 导出单元测试（不依赖数据库）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import StatusDistribution, TaskMetrics
from billing_qa.services.io.execution_excel import (
    MAX_CELL_LENGTH,
    build_task_results_xlsx,
)


def _task() -> BatchTask:
    started = datetime(2026, 1, 2, 10, 0, 0)
    return BatchTask(
        id=42,
        code="TASK-TEST-001",
        name="批量回归验收",
        description="验证导出能力",
        status="COMPLETED",
        control_state="RUNNING",
        environment_id=1,
        case_ids=[1, 2],
        total_cases=2,
        finished_cases=2,
        pass_count=1,
        fail_count=1,
        error_count=0,
        max_turns_count=0,
        concurrency=2,
        qps_limit=5.0,
        retry_on_env_error=True,
        max_env_retries=2,
        retry_backoff_seconds=3.0,
        enable_self_check=True,
        repeat_rounds=3,
        round_index=2,
        schedule_type="IMMEDIATE",
        schedule_active=False,
        schedule_source_id=None,
        created_by="admin",
        created_at=started,
        started_at=started,
        finished_at=started + timedelta(minutes=3),
        summary={"pass_rate": 50.0},
        error_message=None,
    )


def _metrics() -> TaskMetrics:
    distribution = StatusDistribution(PASS=1, FAIL=1)
    return TaskMetrics(
        task_id=42,
        status="COMPLETED",
        control_state="RUNNING",
        total_cases=2,
        finished_cases=2,
        progress=100.0,
        status_distribution=distribution,
        pass_rate=50.0,
        avg_ttft_ms=120.5,
        avg_latency_ms=900.0,
        p95_latency_ms=1500.0,
        avg_turns=2.0,
        simulator_tokens=300,
        judge_tokens=120,
        total_tokens=420,
        env_retry_count=0,
        eta_seconds=None,
        elapsed_seconds=180.0,
        throughput_per_minute=0.67,
    )


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "execution_id": 1001,
        "trace_uid": "trace-1001",
        "case_id": 1,
        "case_code": "DEMO-001",
        "case_name": "账单查询",
        "case_type": "单轮",
        "priority": "P0",
        "case_status": "启用",
        "tags": "计费,账单",
        "owner": "tester",
        "dataset_version": "v1",
        "snapshot_id": "SNAP-1",
        "as_of": "2026-01",
        "grading_mode": "答案契约",
        "input_type": "清晰",
        "risk_level": "低",
        "review_status": "无需审核",
        "expected_answer": "合计 100 元",
        "description": "账单问数",
        "query": "我这个月账单多少",
        "user_goal": "获取账单",
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
        "simulator_template_version": 3,
        "judge_template_version": 4,
        "grader_version": "v2",
        "environment_id": 1,
        "environment_name": "QA 环境",
        "started_at": "2026-01-02 10:00:00",
        "finished_at": "2026-01-02 10:00:01",
        "error_message": None,
        "judge_notes": "答案与事实一致",
        "final_answer": "您本月合计 100 元。",
        "rule_result": {"passed": True},
        "judge_result": {"pass": True, "notes": ["答案与事实一致"]},
        "answer_grading": {"passed": True},
        "fact_results": [{"fact": "amount", "passed": True}],
        "diagnostics": {"tool_calls": 2},
        "judges": [{"model_name": "judge-a", "pass_flag": True}],
        "turns": [{"turn": 1, "query": "我这个月账单多少"}],
        "case_snapshot": {"code": "DEMO-001", "expected_answer": "合计 100 元"},
    }
    row.update(overrides)
    return row


def test_build_task_results_workbook() -> None:
    content = build_task_results_xlsx(
        _task(),
        _metrics(),
        [_row(), _row(execution_id=1002, case_id=2, case_code="DEMO-002", status="失败", status_code="FAIL")],
        environment_name="QA 环境",
        scope_label="用例最新（每用例只保留最新一次尝试）",
        exported_by="admin",
        exported_at=datetime(2026, 1, 2, 11, 0, 0),
    )

    workbook = load_workbook(BytesIO(content))
    assert workbook.sheetnames == ["执行概览", "用例结果"]

    overview = workbook["执行概览"]
    overview_map = {
        overview.cell(row=row, column=1).value: overview.cell(row=row, column=2).value
        for row in range(1, overview.max_row + 1)
    }
    assert overview_map["任务编号"] == "TASK-TEST-001"
    assert overview_map["任务名称"] == "批量回归验收"
    assert overview_map["通过率(%)"] == 50.0
    assert overview_map["导出范围"] == "用例最新（每用例只保留最新一次尝试）"
    assert overview_map["明细行数"] == 2
    assert overview_map["失败追问自检"] == "是"
    assert overview_map["连续轮次总数"] == 3
    assert overview_map["当前轮次"] == 2

    results = workbook["用例结果"]
    headers = [cell.value for cell in results[1]]
    assert "用例编号" in headers
    assert "最终答案" in headers
    assert "完整轮次轨迹(JSON)" in headers
    assert "完整用例快照(JSON)" in headers
    assert results.max_row == 3
    assert results.cell(row=2, column=headers.index("用例编号") + 1).value == "DEMO-001"
    assert results.cell(row=3, column=headers.index("状态码") + 1).value == "FAIL"


def test_self_check_fields_exported() -> None:
    row = _row(
        self_check_status="已追问",
        self_check_status_code="DONE",
        self_check_trigger="LLM 裁决不通过",
        self_check_skip_reason=None,
        self_check_conclusion="出错位置：金额与底层数据不一致；修正方式：回查取数结果",
        self_check_session_id="sess-9dbf4c787df8",
        self_check_template="SELF_CHECK_DEFAULT@v1",
        self_check_latency_ms=62.5,
        self_check_performed_at="2026-01-02 10:00:02",
        self_check_error=None,
        self_check_notes="原对话未回填会话值；已按新会话处理",
        self_check_prompt="## Role\n你是智能客服系统…",
        self_check_request={"method": "POST", "sessionid": "sess-9dbf4c787df8"},
        self_check_response={"answer": "【自检结论】…", "intent": "SELF_CHECK"},
    )
    content = build_task_results_xlsx(_task(), _metrics(), [row])
    workbook = load_workbook(BytesIO(content))
    results = workbook["用例结果"]
    headers = [cell.value for cell in results[1]]

    def value(name: str) -> object:
        return results.cell(row=2, column=headers.index(name) + 1).value

    assert value("追问自检状态") == "已追问"
    assert value("追问自检触发原因") == "LLM 裁决不通过"
    assert value("追问自检结论") == row["self_check_conclusion"]
    assert value("追问自检会话") == "sess-9dbf4c787df8"
    assert value("追问自检模板") == "SELF_CHECK_DEFAULT@v1"
    assert value("追问自检时延(ms)") == 62.5
    assert value("追问自检时间") == "2026-01-02 10:00:02"
    assert value("追问自检备注") == "原对话未回填会话值；已按新会话处理"
    assert value("追问自检 Prompt(渲染后)") == "## Role\n你是智能客服系统…"
    assert '"SELF_CHECK"' in str(value("追问自检响应(JSON)"))
    assert "sessionid" in str(value("追问自检请求(JSON)"))

    overview = workbook["执行概览"]
    overview_map = {
        overview.cell(row=r, column=1).value: overview.cell(row=r, column=2).value
        for r in range(1, overview.max_row + 1)
    }
    assert overview_map["追问自检已追问数"] == 1
    assert overview_map["追问自检未触发数"] == 0
    assert overview_map["追问自检失败数"] == 0


def test_build_task_results_workbook_without_rows() -> None:
    content = build_task_results_xlsx(
        _task(),
        _metrics(),
        [],
        exported_by="admin",
        exported_at=datetime(2026, 1, 2, 11, 0, 0),
    )
    workbook = load_workbook(BytesIO(content))
    results = workbook["用例结果"]
    assert results.max_row == 1
    assert results.cell(row=1, column=1).value == "序号"


def test_long_json_is_truncated_to_excel_limit() -> None:
    content = build_task_results_xlsx(
        _task(),
        _metrics(),
        [_row(final_answer="长" * 40000, case_snapshot={"payload": "大" * 40000})],
        exported_by="admin",
    )
    workbook = load_workbook(BytesIO(content))
    results = workbook["用例结果"]
    headers = [cell.value for cell in results[1]]
    answer = results.cell(row=2, column=headers.index("最终答案") + 1).value
    snapshot = results.cell(row=2, column=headers.index("完整用例快照(JSON)") + 1).value
    assert len(answer) <= MAX_CELL_LENGTH
    assert answer.endswith("…(已截断)")
    assert len(snapshot) <= MAX_CELL_LENGTH
    assert snapshot.endswith("…(已截断)")


def test_formula_like_text_is_stored_as_string() -> None:
    content = build_task_results_xlsx(
        _task(),
        _metrics(),
        [_row(final_answer="=1+1", case_name="@SUM(A1)")],
        exported_by="admin",
    )
    workbook = load_workbook(BytesIO(content))
    results = workbook["用例结果"]
    headers = [cell.value for cell in results[1]]
    answer = results.cell(row=2, column=headers.index("最终答案") + 1).value
    case_name = results.cell(row=2, column=headers.index("用例名称") + 1).value
    assert answer == "'=1+1"
    assert case_name == "'@SUM(A1)"
