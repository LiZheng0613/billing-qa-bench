"""测试用例 Excel / JSON 批量导入导出。"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from billing_qa.core.exceptions import ValidationError

EXCEL_COLUMNS: list[tuple[str, str]] = [
    ("code", "用例编号"),
    ("name", "用例名称"),
    ("description", "用例描述"),
    ("case_type", "用例类型(SINGLE/STATIC_MULTI/DYNAMIC_MULTI)"),
    ("status", "用例状态(ACTIVE/DISABLED/DRAFT)"),
    ("priority", "优先级"),
    ("tags", "业务域标签(逗号分隔)"),
    ("environment_code", "环境编码"),
    ("request_variables", "请求变量(JSON)"),
    ("query", "固定输入"),
    ("expected_answer", "预期口径/数值"),
    ("expected_rules", "规则校验配置(JSON)"),
    ("turns", "多轮话术(JSON)"),
    ("session_expected", "会话级预期(JSON)"),
    ("fail_fast", "单轮失败中断(TRUE/FALSE)"),
    ("user_goal", "测试目标"),
    ("initial_query", "首轮破冰"),
    ("persona_description", "用户画像"),
    ("hidden_context", "隐藏信息(JSON)"),
    ("max_turns", "最大轮次"),
    ("terminate_conditions", "终止条件(JSON)"),
    ("simulator_template_code", "模拟器模板编码"),
    ("simulator_template_strategy", "模拟器策略(LATEST/PINNED)"),
    ("simulator_template_version", "模拟器版本"),
    ("judge_template_code", "裁判模板编码"),
    ("judge_template_strategy", "裁判策略(LATEST/PINNED)"),
    ("judge_template_version", "裁判版本"),
    ("prompt_overrides", "Prompt 覆写(JSON)"),
    ("judge_model_codes", "裁判模型编码(逗号分隔)"),
    ("judge_strategy", "裁决策略(AVG/VETO)"),
    ("pass_threshold", "通过分数线"),
    ("grading_mode", "判分方式(LEGACY/ANSWER_CONTRACT)"),
    ("input_type", "输入形态"),
    ("as_of", "冻结时间(账期/日期)"),
    ("snapshot_id", "数据快照标识"),
    ("answer_envelope", "答案包络(JSON)"),
    ("response_contract", "响应契约(JSON)"),
    ("plan_spec", "取数计划(JSON)"),
    ("risk_level", "风险等级(LOW/HIGH)"),
    ("owner", "用例负责人"),
    ("review_status", "审核状态"),
    ("dataset_version", "数据集版本"),
    ("dedupe_hash", "去重指纹"),
]

JSON_FIELDS = {
    "request_variables",
    "expected_rules",
    "turns",
    "session_expected",
    "hidden_context",
    "terminate_conditions",
    "prompt_overrides",
    "answer_envelope",
    "response_contract",
    "plan_spec",
}
LIST_FIELDS = {"tags", "judge_model_codes"}
BOOL_FIELDS = {"fail_fast"}
INT_FIELDS = {"max_turns"}
FLOAT_FIELDS = {"pass_threshold", "simulator_template_version", "judge_template_version"}


def _parse_cell(key: str, value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if key in JSON_FIELDS:
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"字段 {key} 不是合法 JSON: {exc}") from exc
    if key in BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).strip().upper() in {"TRUE", "1", "是", "YES"}
    if key in INT_FIELDS:
        return int(float(value))
    if key in FLOAT_FIELDS:
        return float(value)
    if key in LIST_FIELDS:
        if isinstance(value, list):
            return value
        text = str(value).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]
    return str(value).strip()


def parse_excel(content: bytes) -> list[dict[str, Any]]:
    try:
        workbook = load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise ValidationError(f"无法解析 Excel 文件: {exc}") from exc
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise ValidationError("Excel 内容为空")
    header = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
    label_to_key = {label: key for key, label in EXCEL_COLUMNS}
    key_to_index: dict[str, int] = {}
    for index, label in enumerate(header):
        key = label_to_key.get(label)
        if key is None:
            # 允许直接使用英文字段名
            key = label if label in {item[0] for item in EXCEL_COLUMNS} else None
        if key:
            key_to_index[key] = index
    if "code" not in key_to_index or "case_type" not in key_to_index:
        raise ValidationError("Excel 缺少必需列：用例编号 / 用例类型")

    records: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows[1:], start=2):
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        item: dict[str, Any] = {}
        for key, index in key_to_index.items():
            if index < len(row):
                value = _parse_cell(key, row[index])
                if value is not None:
                    item[key] = value
        item["__row__"] = row_index
        records.append(item)
    return records


def parse_json(content: bytes) -> list[dict[str, Any]]:
    """兼容旧调用：委托给 ``case_json.parse_case_file``。

    新代码请直接使用 ``case_json``，它支持版本化信封并返回文件级告警。
    """
    from billing_qa.services.io.case_json import parse_case_file

    return parse_case_file(content).records


def export_cases_xlsx(cases: list[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试用例"
    header_fill = PatternFill("solid", fgColor="1F2937")
    header_font = Font(color="FFFFFF", bold=True)

    for column_index, (_key, label) in enumerate(EXCEL_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column_index, value=label)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.column_dimensions[get_column_letter(column_index)].width = max(
            14, min(40, len(label) * 2)
        )

    for row_index, item in enumerate(cases, start=2):
        for column_index, (key, _) in enumerate(EXCEL_COLUMNS, start=1):
            value = item.get(key)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            sheet.cell(row=row_index, column=column_index, value=value)
    sheet.freeze_panes = "A2"

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
