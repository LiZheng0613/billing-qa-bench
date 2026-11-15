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
    ("case_type", "用例类型(SINGLE/STATIC_MULTI/DYNAMIC_MULTI)"),
    ("priority", "优先级"),
    ("tags", "业务域标签(逗号分隔)"),
    ("environment_code", "环境编码"),
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
    ("simulator_template_code", "模拟器模板编码"),
    ("judge_template_code", "裁判模板编码"),
    ("simulator_template_strategy", "模拟器策略(LATEST/PINNED)"),
    ("simulator_template_version", "模拟器版本"),
    ("judge_template_strategy", "裁判策略(LATEST/PINNED)"),
    ("judge_template_version", "裁判版本"),
    ("judge_strategy", "裁决策略(AVG/VETO)"),
    ("pass_threshold", "通过分数线"),
]

JSON_FIELDS = {"expected_rules", "turns", "session_expected", "hidden_context", "terminate_conditions"}
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
    if key == "tags":
        if isinstance(value, list):
            return value
        return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]
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
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"无法解析 JSON 文件: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("items") or data.get("cases") or [data]
    if not isinstance(data, list):
        raise ValidationError("JSON 顶层必须是数组")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"第 {index} 条记录不是对象")
        record = dict(item)
        # 导出文件里 tags 是逗号分隔字符串（Excel 与 JSON 共用同一份扁平结构），
        # 直接回灌时需要还原成数组，否则 CaseUpdate 校验会失败
        if not isinstance(record.get("tags"), (list, type(None))):
            record["tags"] = [
                part.strip()
                for part in str(record["tags"]).replace("，", ",").split(",")
                if part.strip()
            ]
        record.setdefault("__row__", index)
        records.append(record)
    return records


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
