"""单次批量执行结果 JSONL 导出。

背景：Excel（xlsx）单元格文本上限为 32767 字符，超长字段（轮次轨迹、
用例快照、自检请求响应等）会被 ``execution_excel`` 截断加 ``…(已截断)``
标记。JSONL 导出不做任何截断，把这些大字段原样保留，便于程序消费
（jq / pandas.read_json(lines=True) / 二次分析）。

文件结构（每行一个独立 JSON 对象，用 ``kind`` 区分）：
- 第 1 行：``{"kind": "meta", ...}`` 导出上下文（任务、范围、时间、行数）；
- 其余行：``{"kind": "row", ...}`` 逐条执行结果，字段与 Excel
  ``TASK_RESULT_COLUMNS`` 对齐，dict/list 保持原生嵌套结构不序列化成字符串。

本模块只负责纯数据渲染，数据库查询由调用方完成，便于单元测试。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskMetrics
from billing_qa.services.io.execution_excel import TASK_RESULT_COLUMNS

JSONL_SCHEMA_NAME = "billing-qa/task-results"
# v2：行数据新增人读派生字段（judge_conclusion / error_tags / fact_hit / fact_miss /
# rule_failures / diag_summary / sql_excerpt / last_qa / suggest_action / case_version）。
JSONL_SCHEMA_VERSION = 2

# 行字段输出顺序：与 TASK_RESULT_COLUMNS 保持一致，便于人工阅读与 Excel 对照；
# 行 dict 里额外的调试字段（如 self_check_status_code）追加在末尾，不丢弃。
_ROW_KEYS: tuple[str, ...] = tuple(key for key, _label, _width in TASK_RESULT_COLUMNS)


def _dump_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)


def build_task_results_jsonl(
    task: BatchTask,
    metrics: TaskMetrics,
    rows: list[dict[str, Any]],
    *,
    environment_name: str | None = None,
    scope_label: str = "用例最新",
    exported_by: str | None = None,
    exported_at: datetime | None = None,
) -> bytes:
    """生成单批次执行结果 JSONL 字节流，不做任何字段截断。"""
    exported_at = exported_at or datetime.now()
    meta: dict[str, Any] = {
        "kind": "meta",
        "schema": JSONL_SCHEMA_NAME,
        "schema_version": JSONL_SCHEMA_VERSION,
        "task_id": task.id,
        "task_code": task.code,
        "task_name": task.name,
        "environment_name": environment_name,
        "scope_label": scope_label,
        "exported_by": exported_by,
        "exported_at": exported_at.strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(rows),
        "pass_rate": metrics.pass_rate,
    }

    lines: list[str] = [_dump_line(meta)]
    for index, item in enumerate(rows, start=1):
        record: dict[str, Any] = {"kind": "row", "seq": index}
        for key in _ROW_KEYS:
            if key == "seq":
                # 与 Excel 导出一致：序号由导出时的行位置生成，不取行数据。
                continue
            record[key] = item.get(key)
        # 行里的额外字段（不在 Excel 列清单内）一并保留，确保导出数据完整。
        for key, value in item.items():
            if key not in record:
                record[key] = value
        lines.append(_dump_line(record))

    # 每行以 \n 结尾（含最后一行），逐行独立可流式解析。
    return ("\n".join(lines) + "\n").encode("utf-8")
