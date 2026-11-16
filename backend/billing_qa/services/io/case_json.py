"""测试用例 JSON 导入导出（v1）。

设计原则：
- 一个文件就是一个可移植的用例集合，带 schema 版本与导出元信息；
- 字段使用**易读的业务字段名**（``environment_code`` / ``judge_model_codes``），
  不导出数据库自增 id，换环境可复用；
- 结构与 Excel 解耦：不再复用扁平列定义，允许嵌套 JSON 字段原样往返；
- 导入兼容三种输入：新格式信封、旧版扁平数组、单个用例对象。

字段清单与 ``services/revision/snapshot.py`` 的 ``SNAPSHOT_FIELDS`` 对齐，
避免新增字段后导入导出悄悄丢字段。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from billing_qa.core.exceptions import ValidationError
from billing_qa.services.revision.snapshot import SNAPSHOT_FIELDS

SCHEMA_NAME = "billing-qa/cases"
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = {1}

# 导出顺序：同一业务区块放在一起，便于人工阅读与 diff
EXPORT_ORDER: tuple[str, ...] = (
    "code",
    "name",
    "description",
    "case_type",
    "status",
    "priority",
    "tags",
    "environment_code",
    "query",
    "expected_answer",
    "expected_rules",
    "turns",
    "session_expected",
    "fail_fast",
    "user_goal",
    "initial_query",
    "persona_description",
    "hidden_context",
    "max_turns",
    "terminate_conditions",
    "simulator_template_code",
    "simulator_template_strategy",
    "simulator_template_version",
    "judge_template_code",
    "judge_template_strategy",
    "judge_template_version",
    "prompt_overrides",
    "simulator_model_code",
    "judge_model_codes",
    "judge_strategy",
    "pass_threshold",
    "grading_mode",
    "input_type",
    "as_of",
    "snapshot_id",
    "answer_envelope",
    "response_contract",
    "plan_spec",
    "risk_level",
    "owner",
    "review_status",
    "dataset_version",
    "dedupe_hash",
)

# 引用型字段：导出写编码，导入解析成 id
REFERENCE_CODES: dict[str, str] = {
    "environment_code": "environment_id",
    "simulator_template_code": "simulator_template_id",
    "judge_template_code": "judge_template_id",
    "simulator_model_code": "simulator_model_id",
    "judge_model_codes": "judge_model_ids",
}

# 只读字段：出现在文件里也不写入数据库
READONLY_FIELDS = {
    "id",
    "current_version",
    "created_by",
    "updated_by",
    "deleted_at",
    "reviewed_by",
    "created_at",
    "updated_at",
    "exported_at",
    "schema",
    "schema_version",
    "case_count",
}

_STRUCTURED_FIELDS = {
    "expected_rules",
    "turns",
    "session_expected",
    "hidden_context",
    "terminate_conditions",
    "answer_envelope",
    "response_contract",
    "plan_spec",
    "prompt_overrides",
}
_LIST_FIELDS = {"tags", "judge_model_ids", "judge_model_codes"}
_UPPER_FIELDS = {
    "case_type",
    "status",
    "priority",
    "grading_mode",
    "input_type",
    "risk_level",
    "review_status",
    "judge_strategy",
    "simulator_template_strategy",
    "judge_template_strategy",
}

ALLOWED_FIELDS = (set(SNAPSHOT_FIELDS) | set(REFERENCE_CODES) | {"judge_model_ids"}) - READONLY_FIELDS


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
def build_export_payload(
    cases: list[Any],
    *,
    env_map: dict[int, str] | None = None,
    template_map: dict[int, str] | None = None,
    model_map: dict[int, str] | None = None,
    exported_by: str | None = None,
    exported_at: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 ORM 用例列表转成带版本信封的 JSON 载荷。"""
    env_map = env_map or {}
    template_map = template_map or {}
    model_map = model_map or {}

    items: list[dict[str, Any]] = []
    for case in cases:
        values: dict[str, Any] = {field_name: getattr(case, field_name, None) for field_name in SNAPSHOT_FIELDS}
        values["environment_code"] = env_map.get(case.environment_id) if case.environment_id else None
        values["simulator_template_code"] = (
            template_map.get(case.simulator_template_id) if case.simulator_template_id else None
        )
        values["judge_template_code"] = (
            template_map.get(case.judge_template_id) if case.judge_template_id else None
        )
        values["simulator_model_code"] = (
            model_map.get(case.simulator_model_id) if case.simulator_model_id else None
        )
        values["judge_model_codes"] = [
            model_map[int(model_id)]
            for model_id in (case.judge_model_ids or [])
            if int(model_id) in model_map
        ]
        for internal in ("environment_id", "simulator_template_id", "judge_template_id"):
            values.pop(internal, None)
        values.pop("simulator_model_id", None)
        values.pop("judge_model_ids", None)

        item = {key: values[key] for key in EXPORT_ORDER if key in values}
        items.append(item)

    return {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "exported_at": exported_at,
        "exported_by": exported_by,
        "case_count": len(items),
        "filters": filters or {},
        "items": items,
    }


# ---------------------------------------------------------------------- #
# 解析
# ---------------------------------------------------------------------- #
@dataclass
class ParsedCaseFile:
    """解析结果：逐条记录 + 文件级提示。"""

    records: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    schema_version: int | None = None


def parse_case_file(content: bytes) -> ParsedCaseFile:
    """解析 JSON 文件，兼容信封 / 数组 / 单个对象三种形态。"""
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"无法解析 JSON 文件: {exc}") from exc

    warnings: list[str] = []
    version: int | None = None

    if isinstance(data, dict):
        schema = data.get("schema")
        raw_version = data.get("schema_version")
        if schema and schema != SCHEMA_NAME:
            raise ValidationError(f"不支持的 schema: {schema}（应为 {SCHEMA_NAME}）")
        if raw_version is not None:
            try:
                version = int(raw_version)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"schema_version 必须是整数: {raw_version}") from exc
            if version not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValidationError(
                    f"不支持的 schema_version: {version}（当前支持 {sorted(SUPPORTED_SCHEMA_VERSIONS)}）"
                )
        raw_items = data.get("items")
        if raw_items is None:
            raw_items = data.get("cases")
        if raw_items is None:
            # 单个用例对象
            if "code" in data:
                raw_items = [data]
            else:
                raise ValidationError("JSON 缺少 items 数组，也不是单个用例对象")
        records_raw = raw_items
    elif isinstance(data, list):
        records_raw = data
        if data:
            warnings.append("文件为旧版扁平数组，已按兼容模式导入")
    else:
        raise ValidationError("JSON 顶层必须是对象或数组")

    if not isinstance(records_raw, list):
        raise ValidationError("items 必须是数组")
    if not records_raw:
        raise ValidationError("文件中没有用例")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(records_raw, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"第 {index} 条记录不是对象")
        record = dict(item)
        record["__row__"] = index
        records.append(record)

    return ParsedCaseFile(records=records, warnings=warnings, schema_version=version)


def normalize_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把单条记录规整成可交给 Pydantic 校验的 dict。

    返回 ``(record, warnings)``。未知字段只告警不报错，方便上游版本新增字段时
    旧代码仍能导入；readonly 字段直接丢弃。
    """
    row = record.pop("__row__", 0)
    warnings: list[str] = []
    cleaned: dict[str, Any] = {}

    unknown = sorted(set(record) - ALLOWED_FIELDS - READONLY_FIELDS)
    if unknown:
        warnings.append(f"第 {row} 条：忽略未知字段 {', '.join(unknown)}")

    for key, value in record.items():
        if key in READONLY_FIELDS or key not in ALLOWED_FIELDS:
            continue
        if key in _STRUCTURED_FIELDS:
            value = _coerce_json(key, value)
        elif key in _LIST_FIELDS:
            value = _coerce_list(key, value)
        elif key in _UPPER_FIELDS and isinstance(value, str):
            value = value.strip().upper() or None
        elif key == "fail_fast" and isinstance(value, str):
            value = value.strip().upper() in {"TRUE", "1", "YES", "是"}
        elif isinstance(value, str):
            value = value.strip() or None
        cleaned[key] = value

    return cleaned, warnings


def resolve_references(
    record: dict[str, Any],
    *,
    env_map: dict[str, int],
    template_map: dict[str, int],
    model_map: dict[str, int],
) -> dict[str, Any]:
    """把 *_code 引用解析成数据库 id；解析失败直接报错，不静默置空。"""
    resolved = dict(record)
    if resolved.get("environment_code"):
        code = str(resolved.pop("environment_code"))
        if code not in env_map:
            raise ValidationError(f"环境编码不存在: {code}")
        resolved["environment_id"] = env_map[code]
    if resolved.get("simulator_template_code"):
        code = str(resolved.pop("simulator_template_code"))
        if code not in template_map:
            raise ValidationError(f"模拟器模板编码不存在: {code}")
        resolved["simulator_template_id"] = template_map[code]
    if resolved.get("judge_template_code"):
        code = str(resolved.pop("judge_template_code"))
        if code not in template_map:
            raise ValidationError(f"裁判模板编码不存在: {code}")
        resolved["judge_template_id"] = template_map[code]
    if resolved.get("simulator_model_code"):
        code = str(resolved.pop("simulator_model_code"))
        if code not in model_map:
            raise ValidationError(f"模拟器模型编码不存在: {code}")
        resolved["simulator_model_id"] = model_map[code]
    codes = resolved.pop("judge_model_codes", None)
    if codes is not None:
        id_list: list[int] = []
        for code in codes:
            if str(code) not in model_map:
                raise ValidationError(f"裁判模型编码不存在: {code}")
            id_list.append(model_map[str(code)])
        resolved["judge_model_ids"] = id_list
    return resolved


# ---------------------------------------------------------------------- #
# 工具
# ---------------------------------------------------------------------- #
def _coerce_json(key: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"字段 {key} 不是合法 JSON: {exc}") from exc


def _coerce_list(key: str, value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]
