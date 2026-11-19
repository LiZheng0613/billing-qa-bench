"""批量任务结果 Excel / JSONL 导出单元测试（不依赖数据库）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import StatusDistribution, TaskMetrics
from billing_qa.services.io.execution_excel import (
    MAX_CELL_LENGTH,
    TASK_RESULT_COLUMNS,
    build_task_results_xlsx,
)
from billing_qa.services.io.execution_jsonl import (
    JSONL_SCHEMA_NAME,
    JSONL_SCHEMA_VERSION,
    build_task_results_jsonl,
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


def _parse_jsonl(content: bytes) -> list[dict[str, object]]:
    text = content.decode("utf-8")
    assert text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


def test_build_task_results_jsonl_meta_and_rows() -> None:
    content = build_task_results_jsonl(
        _task(),
        _metrics(),
        [_row(), _row(execution_id=1002, case_id=2, case_code="DEMO-002")],
        environment_name="QA 环境",
        scope_label="用例最新（每用例只保留最新一次尝试）",
        exported_by="admin",
        exported_at=datetime(2026, 1, 2, 11, 0, 0),
    )
    records = _parse_jsonl(content)

    meta = records[0]
    assert meta["kind"] == "meta"
    assert meta["schema"] == JSONL_SCHEMA_NAME
    assert meta["schema_version"] == JSONL_SCHEMA_VERSION
    assert meta["task_code"] == "TASK-TEST-001"
    assert meta["environment_name"] == "QA 环境"
    assert meta["row_count"] == 2
    assert meta["exported_by"] == "admin"

    rows = records[1:]
    assert [row["seq"] for row in rows] == [1, 2]
    assert rows[0]["execution_id"] == 1001
    assert rows[1]["execution_id"] == 1002
    # 行字段与 Excel 列完全对齐，方便逐列对照。
    column_keys = {key for key, _label, _width in TASK_RESULT_COLUMNS}
    assert column_keys <= set(rows[0].keys())
    # dict/list 保持原生嵌套结构，不序列化成字符串。
    assert rows[0]["turns"] == [{"turn": 1, "query": "我这个月账单多少"}]
    assert rows[0]["judge_result"]["pass"] is True
    assert rows[0]["case_snapshot"]["code"] == "DEMO-001"


def test_build_task_results_jsonl_keeps_extra_row_fields() -> None:
    row = _row(self_check_status_code="DONE")
    content = build_task_results_jsonl(_task(), _metrics(), [row])
    records = _parse_jsonl(content)
    # 不在 Excel 列清单内的字段也完整保留。
    assert records[1]["self_check_status_code"] == "DONE"


def test_build_task_results_jsonl_not_truncated() -> None:
    # 同样的超长输入：Excel 会截断到 32000，JSONL 必须一字不差地保留。
    long_answer = "长" * 40000
    long_payload = {"payload": "大" * 40000}
    excel_content = build_task_results_xlsx(
        _task(),
        _metrics(),
        [_row(final_answer=long_answer, case_snapshot=long_payload)],
        exported_by="admin",
    )
    jsonl_content = build_task_results_jsonl(
        _task(),
        _metrics(),
        [_row(final_answer=long_answer, case_snapshot=long_payload)],
        exported_by="admin",
    )

    workbook = load_workbook(BytesIO(excel_content))
    results = workbook["用例结果"]
    headers = [cell.value for cell in results[1]]
    excel_answer = results.cell(row=2, column=headers.index("最终答案") + 1).value
    assert len(str(excel_answer)) <= MAX_CELL_LENGTH

    row = _parse_jsonl(jsonl_content)[1]
    assert row["final_answer"] == long_answer
    assert row["case_snapshot"] == long_payload


def test_build_task_results_jsonl_without_rows() -> None:
    content = build_task_results_jsonl(
        _task(),
        _metrics(),
        [],
        exported_by="admin",
        exported_at=datetime(2026, 1, 2, 11, 0, 0),
    )
    records = _parse_jsonl(content)
    assert len(records) == 1
    assert records[0]["kind"] == "meta"
    assert records[0]["row_count"] == 0


def test_build_task_results_jsonl_no_mojibake() -> None:
    """导出的 JSONL 必须是明文 UTF-8 中文，无转义、无 BOM、可无损往返。"""
    content = build_task_results_jsonl(
        _task(),
        _metrics(),
        [_row(final_answer="您本月合计 100 元。", case_name="账单查询-中文用例")],
        exported_by="管理员",
    )

    # 1) 原始字节里就是真 UTF-8 中文，而不是 \uXXXX 转义（防止编辑器里看到一堆转义）。
    assert "您本月合计 100 元。".encode("utf-8") in content
    assert "账单查询-中文用例".encode("utf-8") in content
    assert "\\u" not in content.decode("utf-8")

    # 2) 无 BOM：jq / pandas 等按行解析的工具不会被首行字节顺序标记破坏。
    assert not content.startswith(b"\xef\xbb\xbf")

    # 3) 整体可无损 UTF-8 解码，且逐行往返后内容一致（防止半截字符/坏字节）。
    text = content.decode("utf-8")
    records = [json.loads(line) for line in text.splitlines()]
    row = records[1]
    assert row["final_answer"] == "您本月合计 100 元。"
    assert row["case_name"] == "账单查询-中文用例"
    assert records[0]["exported_by"] == "管理员"
