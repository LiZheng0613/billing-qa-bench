"""用例 Excel 导入导出与最终用例字段对齐。"""

from __future__ import annotations

import json
from io import BytesIO

from openpyxl import Workbook, load_workbook

from billing_qa.services.io.excel import EXCEL_COLUMNS, export_cases_xlsx, parse_excel


def _export_row() -> dict[str, object]:
    return {
        "code": "EXCEL-ALIGN-001",
        "name": "Excel 字段对齐",
        "description": "覆盖最终实现新增字段",
        "case_type": "SINGLE",
        "status": "DRAFT",
        "priority": "P0",
        "tags": ["导入导出", "字段对齐"],
        "environment_code": "QA-MOCK",
        "request_variables": {"userid": "13800138000", "segment": 7},
        "query": "本期账单是多少？",
        "expected_answer": "账单合计 100 元",
        "fail_fast": True,
        "max_turns": 6,
        "pass_threshold": 70.5,
        "grading_mode": "ANSWER_CONTRACT",
        "input_type": "CLEAR",
        "as_of": "2026-10",
        "snapshot_id": "SNAP-2026-10",
        "answer_envelope": {"must": [{"id": "amount", "kind": "scalar", "value": 100}]},
        "response_contract": {"allowed": ["clarify", "partial"]},
        "plan_spec": {"required_capabilities": ["bill_query"]},
        "prompt_overrides": {"judge": "只关注金额", "simulator": "保持简洁"},
        "judge_model_codes": ["judge-a", "judge-b"],
        "judge_strategy": "VETO",
        "risk_level": "HIGH",
        "owner": "qa-team",
        "review_status": "PENDING",
        "dataset_version": "v2",
    }


def _cell_value(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def test_excel_columns_cover_final_case_fields() -> None:
    content = export_cases_xlsx([_export_row()])
    workbook = load_workbook(BytesIO(content))
    sheet = workbook.active
    headers = [cell.value for cell in sheet[1]]
    labels = [label for _key, label in EXCEL_COLUMNS]
    assert headers == labels

    for label in (
        "用例描述",
        "用例状态(ACTIVE/DISABLED/DRAFT)",
        "请求变量(JSON)",
        "Prompt 覆写(JSON)",
        "裁判模型编码(逗号分隔)",
    ):
        assert label in headers

    row = {
        header: sheet.cell(row=2, column=index + 1).value
        for index, header in enumerate(headers)
    }
    assert row["用例状态(ACTIVE/DISABLED/DRAFT)"] == "DRAFT"
    assert row["请求变量(JSON)"] == json.dumps(
        {"userid": "13800138000", "segment": 7}, ensure_ascii=False
    )
    assert row["裁判模型编码(逗号分隔)"] == json.dumps(
        ["judge-a", "judge-b"], ensure_ascii=False
    )


def test_parse_excel_reads_final_structured_fields() -> None:
    row = _export_row()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([label for _key, label in EXCEL_COLUMNS])
    sheet.append([_cell_value(row.get(key)) for key, _label in EXCEL_COLUMNS])
    buffer = BytesIO()
    workbook.save(buffer)

    records = parse_excel(buffer.getvalue())
    assert len(records) == 1
    parsed = records[0]
    assert parsed["status"] == "DRAFT"
    assert parsed["description"] == "覆盖最终实现新增字段"
    assert parsed["request_variables"] == {"userid": "13800138000", "segment": 7}
    assert parsed["prompt_overrides"] == {"judge": "只关注金额", "simulator": "保持简洁"}
    assert parsed["judge_model_codes"] == ["judge-a", "judge-b"]
    assert parsed["fail_fast"] is True
    assert parsed["max_turns"] == 6
    assert parsed["pass_threshold"] == 70.5
    assert parsed["answer_envelope"]["must"][0]["id"] == "amount"
