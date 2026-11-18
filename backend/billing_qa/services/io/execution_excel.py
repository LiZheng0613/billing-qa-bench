"""单次批量执行结果 Excel 导出。

生成两个工作表：
- 执行概览：任务配置、指标与导出上下文；
- 用例结果：逐用例执行结果、判分明细与完整轨迹。

本模块只负责纯数据渲染，数据库查询由调用方完成，便于单元测试。
"""

from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskMetrics

# Excel 单元格文本上限为 32767，留出截断标记的空间。
MAX_CELL_LENGTH = 32000

STATUS_LABELS: dict[str, str] = {
    "PASS": "通过",
    "FAIL": "失败",
    "ERROR": "异常",
    "MAX_TURNS_EXCEEDED": "轮次超限",
    "REPETITION_ABORT": "复读熔断",
    "ABORTED": "已终止",
    "RUNNING": "执行中",
    "PENDING": "等待中",
    "COMPLETED": "已完成",
    "FAILED": "执行失败",
}

SCHEDULE_TYPE_LABELS: dict[str, str] = {
    "IMMEDIATE": "立即执行",
    "SCHEDULED": "指定时间",
    "DAILY": "每天定时",
    "WEEKLY": "每周定时",
}

CASE_TYPE_LABELS: dict[str, str] = {
    "SINGLE": "单轮",
    "STATIC_MULTI": "静态多轮",
    "DYNAMIC_MULTI": "动态多轮",
}

SOURCE_LABELS: dict[str, str] = {
    "BATCH": "批量执行",
    "RETRY": "失败重跑",
    "DEBUG": "单条调试",
    "SANDBOX": "沙盒",
}

GRADING_MODE_LABELS: dict[str, str] = {
    "LEGACY": "4 维评分",
    "ANSWER_CONTRACT": "答案契约",
}

INPUT_TYPE_LABELS: dict[str, str] = {
    "CLEAR": "清晰",
    "UNDERSPECIFIED": "信息不足",
    "AMBIGUOUS_REF": "指代歧义",
    "MULTI_INTENT": "多意图",
    "VAGUE": "表述模糊",
    "UNANSWERABLE": "不可回答",
    "CONTRADICTORY": "前后矛盾",
    "UNAUTHORIZED": "越权请求",
}

RISK_LEVEL_LABELS: dict[str, str] = {"LOW": "低", "HIGH": "高"}

REVIEW_STATUS_LABELS: dict[str, str] = {
    "NOT_REQUIRED": "无需审核",
    "PENDING": "待审核",
    "APPROVED": "已通过",
    "REJECTED": "已驳回",
}

CASE_STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "启用",
    "DISABLED": "停用",
    "DRAFT": "草稿",
}

ERROR_KIND_LABELS: dict[str, str] = {"ENV": "环境异常", "BUSINESS": "业务异常"}

SELF_CHECK_STATUS_LABELS: dict[str, str] = {
    "DONE": "已追问",
    "SKIPPED": "未触发",
    "ERROR": "追问失败",
}


def self_check_status_label(value: str | None) -> str | None:
    if not value:
        return None
    return SELF_CHECK_STATUS_LABELS.get(value, value)

ROOT_CAUSE_LABELS: dict[str, str] = {
    "RETRIEVAL": "取数错误",
    "SYNTHESIS": "答案组织错误",
    "HALLUCINATION": "数据幻觉",
    "BEHAVIOR": "行为不合规",
    "UNKNOWN": "未知",
}

# (字段键, 表头, 列宽)
TASK_RESULT_COLUMNS: list[tuple[str, str, int]] = [
    ("seq", "序号", 6),
    ("execution_id", "执行号", 10),
    ("trace_uid", "Trace UID", 38),
    ("case_id", "用例 ID", 9),
    ("case_code", "用例编号", 20),
    ("case_name", "用例名称", 32),
    ("case_type", "用例类型", 12),
    ("priority", "优先级", 8),
    ("case_status", "用例状态", 10),
    ("tags", "业务域标签", 22),
    ("owner", "用例负责人", 14),
    ("dataset_version", "数据集版本", 12),
    ("snapshot_id", "数据快照", 16),
    ("as_of", "冻结时间", 14),
    ("grading_mode", "判分模式", 14),
    ("input_type", "输入形态", 14),
    ("risk_level", "风险等级", 10),
    ("review_status", "审核状态", 12),
    ("expected_answer", "期望答案", 32),
    ("description", "用例描述", 30),
    ("query", "固定输入", 30),
    ("user_goal", "测试目标", 30),
    ("source", "执行来源", 10),
    ("status", "执行状态", 12),
    ("status_code", "状态码", 18),
    ("pass_flag", "是否通过", 10),
    ("total_score", "总分", 8),
    ("termination_status", "终止状态", 14),
    ("attempt_no", "尝试序号", 9),
    ("attempt_count", "用例总尝试次数", 14),
    ("error_kind", "错误分类", 10),
    ("root_cause", "失败根因", 12),
    ("lucky_pass", "幸运通过", 10),
    ("turns_used", "已用轮次", 9),
    ("max_turns", "最大轮次", 9),
    ("latency_ms", "耗时(ms)", 11),
    ("ttft_ms", "TTFT(ms)", 11),
    ("simulator_tokens", "模拟器 Tokens", 13),
    ("judge_tokens", "裁判 Tokens", 12),
    ("total_tokens", "总 Tokens", 11),
    ("simulator_template_version", "模拟器模板版本", 14),
    ("judge_template_version", "裁判模板版本", 13),
    ("grader_version", "判分器版本", 12),
    ("environment_id", "环境 ID", 8),
    ("environment_name", "执行环境", 18),
    ("started_at", "开始时间", 20),
    ("finished_at", "结束时间", 20),
    ("error_message", "错误信息", 40),
    ("judge_notes", "裁判备注", 40),
    ("final_answer", "最终答案", 50),
    ("self_check_status", "追问自检状态", 12),
    ("self_check_trigger", "追问自检触发原因", 22),
    ("self_check_skip_reason", "追问自检跳过原因", 30),
    ("self_check_conclusion", "追问自检结论", 50),
    ("self_check_session_id", "追问自检会话", 26),
    ("self_check_template", "追问自检模板", 22),
    ("self_check_latency_ms", "追问自检时延(ms)", 14),
    ("self_check_performed_at", "追问自检时间", 20),
    ("self_check_error", "追问自检错误", 32),
    ("self_check_notes", "追问自检备注", 32),
    ("self_check_prompt", "追问自检 Prompt(渲染后)", 50),
    ("self_check_request", "追问自检请求(JSON)", 32),
    ("self_check_response", "追问自检响应(JSON)", 32),
    ("rule_result", "规则校验结果(JSON)", 32),
    ("judge_result", "聚合裁决结果(JSON)", 32),
    ("answer_grading", "答案契约判分明细(JSON)", 32),
    ("fact_results", "事实级结果(JSON)", 32),
    ("diagnostics", "轨迹诊断(JSON)", 30),
    ("judges", "裁判明细(JSON)", 32),
    ("turns", "完整轮次轨迹(JSON)", 40),
    ("case_snapshot", "完整用例快照(JSON)", 32),
]

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")
WRAP_ALIGNMENT = Alignment(vertical="top", wrap_text=True)


def status_label(value: str | None) -> str | None:
    if not value:
        return None
    return STATUS_LABELS.get(value, value)


def schedule_type_label(value: str | None) -> str | None:
    if not value:
        return None
    return SCHEDULE_TYPE_LABELS.get(value, value)


def case_type_label(value: str | None) -> str | None:
    if not value:
        return None
    return CASE_TYPE_LABELS.get(value, value)


def source_label(value: str | None) -> str | None:
    if not value:
        return None
    return SOURCE_LABELS.get(value, value)


def grading_mode_label(value: str | None) -> str | None:
    if not value:
        return None
    return GRADING_MODE_LABELS.get(value, value)


def input_type_label(value: str | None) -> str | None:
    if not value:
        return None
    return INPUT_TYPE_LABELS.get(value, value)


def risk_level_label(value: str | None) -> str | None:
    if not value:
        return None
    return RISK_LEVEL_LABELS.get(value, value)


def review_status_label(value: str | None) -> str | None:
    if not value:
        return None
    return REVIEW_STATUS_LABELS.get(value, value)


def case_status_label(value: str | None) -> str | None:
    if not value:
        return None
    return CASE_STATUS_LABELS.get(value, value)


def error_kind_label(value: str | None) -> str | None:
    if not value:
        return None
    return ERROR_KIND_LABELS.get(value, value)


def root_cause_label(value: str | None) -> str | None:
    if not value:
        return None
    return ROOT_CAUSE_LABELS.get(value, value)


def bool_text(value: bool | None) -> str | None:
    if value is None:
        return None
    return "是" if value else "否"


def format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_json(value: Any) -> str | None:
    """将任意 JSON 值转成可写入单元格的文本，并处理超长截断。"""
    if value is None:
        return None
    if isinstance(value, str):
        return truncate_cell(value)
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return truncate_cell(text)


def truncate_cell(text: str) -> str:
    if len(text) <= MAX_CELL_LENGTH:
        return text
    suffix = "…(已截断)"
    return text[: MAX_CELL_LENGTH - len(suffix)] + suffix


def _cell_text(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        value = format_json(value)
    if isinstance(value, str):
        text = truncate_cell(value)
        # 防止用户内容被 Excel 识别为公式（CSV/Excel 注入防护）。
        if text.startswith(("=", "+", "-", "@")):
            return f"'{text}"
        return text
    return value


def _write_header(sheet: Any, columns: list[tuple[str, str, int]]) -> None:
    for column_index, (_key, label, width) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=column_index, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
        sheet.column_dimensions[get_column_letter(column_index)].width = width
    sheet.freeze_panes = "A2"


def _self_check_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """按自检原始状态码统计批次内的追问覆盖情况（用于执行概览）。"""
    counts = {"DONE": 0, "SKIPPED": 0, "ERROR": 0}
    for item in rows:
        code = item.get("self_check_status_code")
        if isinstance(code, str) and code in counts:
            counts[code] += 1
    return counts


def _overview_rows(
    task: BatchTask,
    metrics: TaskMetrics,
    *,
    environment_name: str | None,
    scope_label: str,
    exported_by: str | None,
    exported_at: datetime,
    row_count: int,
    self_check_counts: dict[str, int],
) -> list[tuple[str, Any]]:
    distribution = metrics.status_distribution
    return [
        ("任务 ID", task.id),
        ("任务编号", task.code),
        ("任务名称", task.name),
        ("任务描述", task.description),
        ("任务状态", status_label(task.status)),
        ("状态码", task.status),
        ("控制状态", task.control_state),
        ("执行方式", schedule_type_label(task.schedule_type)),
        ("连续轮次总数", task.repeat_rounds),
        ("当前轮次", task.round_index),
        ("计划生效", bool_text(task.schedule_active)),
        ("计划来源任务 ID", task.schedule_source_id),
        ("指定执行时间", format_datetime(task.scheduled_at)),
        ("周期执行时刻", task.schedule_time),
        ("周期执行星期", format_json(task.schedule_weekdays)),
        ("下次触发时间", format_datetime(task.next_run_at)),
        ("最近触发时间", format_datetime(task.last_run_at)),
        ("环境 ID", task.environment_id),
        ("执行环境", environment_name),
        ("创建人", task.created_by),
        ("创建时间", format_datetime(task.created_at)),
        ("开始时间", format_datetime(task.started_at)),
        ("结束时间", format_datetime(task.finished_at)),
        ("用例总数", task.total_cases),
        ("已完成用例数", metrics.finished_cases),
        ("通过数", distribution.PASS),
        ("失败数", distribution.FAIL),
        ("异常数", distribution.ERROR),
        ("轮次超限数", distribution.MAX_TURNS_EXCEEDED),
        ("复读熔断数", distribution.REPETITION_ABORT),
        ("已终止数", distribution.ABORTED),
        ("执行中数", distribution.RUNNING),
        ("等待中数", distribution.PENDING),
        ("进度(%)", metrics.progress),
        ("通过率(%)", metrics.pass_rate),
        ("平均 TTFT(ms)", metrics.avg_ttft_ms),
        ("平均耗时(ms)", metrics.avg_latency_ms),
        ("P95 耗时(ms)", metrics.p95_latency_ms),
        ("平均轮次", metrics.avg_turns),
        ("模拟器 Tokens", metrics.simulator_tokens),
        ("裁判 Tokens", metrics.judge_tokens),
        ("总 Tokens", metrics.total_tokens),
        ("环境重试用例数", metrics.env_retry_count),
        ("已耗时(秒)", metrics.elapsed_seconds),
        ("吞吐(条/分)", metrics.throughput_per_minute),
        ("并发数", task.concurrency),
        ("QPS 限制", task.qps_limit),
        ("环境失败自动重试", bool_text(task.retry_on_env_error)),
        ("最大环境重试次数", task.max_env_retries),
        ("重试退避(秒)", task.retry_backoff_seconds),
        ("失败追问自检", bool_text(task.enable_self_check)),
        ("追问自检已追问数", self_check_counts.get("DONE", 0)),
        ("追问自检未触发数", self_check_counts.get("SKIPPED", 0)),
        ("追问自检失败数", self_check_counts.get("ERROR", 0)),
        ("任务错误信息", task.error_message),
        ("指标汇总快照(JSON)", format_json(task.summary)),
        ("导出范围", scope_label),
        ("明细行数", row_count),
        ("导出人", exported_by),
        ("导出时间", format_datetime(exported_at)),
    ]


def build_task_results_xlsx(
    task: BatchTask,
    metrics: TaskMetrics,
    rows: list[dict[str, Any]],
    *,
    environment_name: str | None = None,
    scope_label: str = "用例最新",
    exported_by: str | None = None,
    exported_at: datetime | None = None,
) -> bytes:
    """生成单批次执行结果工作簿，返回 xlsx 字节。"""
    workbook = Workbook()

    overview = workbook.active
    overview.title = "执行概览"
    overview.column_dimensions["A"].width = 26
    overview.column_dimensions["B"].width = 90
    for row_index, (label, value) in enumerate(
        _overview_rows(
            task,
            metrics,
            environment_name=environment_name,
            scope_label=scope_label,
            exported_by=exported_by,
            exported_at=exported_at or datetime.now(),
            row_count=len(rows),
            self_check_counts=_self_check_counts(rows),
        ),
        start=1,
    ):
        label_cell = overview.cell(row=row_index, column=1, value=label)
        label_cell.font = Font(bold=True)
        label_cell.alignment = Alignment(vertical="top")
        value_cell = overview.cell(row=row_index, column=2, value=_cell_text(value))
        value_cell.alignment = WRAP_ALIGNMENT
    overview.freeze_panes = "A2"

    results = workbook.create_sheet("用例结果")
    _write_header(results, TASK_RESULT_COLUMNS)
    for row_index, item in enumerate(rows, start=2):
        for column_index, (key, _label, _width) in enumerate(TASK_RESULT_COLUMNS, start=1):
            value = item.get(key)
            if key == "seq":
                value = row_index - 1
            cell = results.cell(row=row_index, column=column_index, value=_cell_text(value))
            cell.alignment = WRAP_ALIGNMENT
    if rows:
        results.auto_filter.ref = (
            f"A1:{get_column_letter(len(TASK_RESULT_COLUMNS))}{len(rows) + 1}"
        )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
