"""单次批量执行结果 Excel 导出（v2 决策报表）。

生成工作表（默认）：
- 执行概览：任务配置、指标、质量辅助与导出上下文（分组纵向 KV）；
- 问题清单：非通过 + 风险用例（幸运通过 / 压线通过），含归因、排查素材、
  建议排查方向、人工跟进列与平台超链接，一页完成「定位 → 决策 → 分派」；
- 用例结果：人读关键字段（标识 / 配置 / 判定 / 归因 / 内容 / 性能）；
- 口径说明：指标定义、阈值与操作提示。

可选（include_raw=True）：
- 原始明细：完整 70 列字段 + JSON 原文，供审计与程序消费兜底。

本模块只负责纯数据渲染，数据库查询由调用方完成，便于单元测试。
"""

from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskMetrics

# Excel 单元格文本上限为 32767，留出截断标记的空间。
MAX_CELL_LENGTH = 32000

# 问题清单长文本摘录的展示上限（完整内容见原始明细 / JSONL）。
EXCERPT_LENGTH = 500

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
    "RESUME": "继续执行",
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

ERROR_KIND_LABELS: dict[str, str] = {
    "ENV": "环境异常",
    "LLM": "模型异常",
    "BUSINESS": "业务异常",
}

SELF_CHECK_STATUS_LABELS: dict[str, str] = {
    "DONE": "已追问",
    "SKIPPED": "未触发",
    "ERROR": "追问失败",
}

ROOT_CAUSE_LABELS: dict[str, str] = {
    "RETRIEVAL": "取数错误",
    "SYNTHESIS": "答案组织错误",
    "HALLUCINATION": "数据幻觉",
    "BEHAVIOR": "行为不合规",
    "UNKNOWN": "未知",
}

# 问题清单 / 轮次排序：数字越小越靠前。
ISSUE_SEVERITY: dict[str, int] = {
    "ERROR": 0,
    "MAX_TURNS_EXCEEDED": 1,
    "REPETITION_ABORT": 2,
    "ABORTED": 3,
    "FAIL": 4,
    "RISK_PASS": 5,
}

PRIORITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# 人工跟进：问题归属下拉选项。
OWNERSHIP_OPTIONS: list[str] = ["用例问题", "系统问题", "环境问题", "待定"]

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
SECTION_FILL = PatternFill("solid", fgColor="E0E7FF")
SECTION_FONT = Font(bold=True, color="1E1B4B", size=10)
PASS_FILL = PatternFill("solid", fgColor="DCFCE7")
FAIL_FILL = PatternFill("solid", fgColor="FEE2E2")
WARN_FILL = PatternFill("solid", fgColor="FEF3C7")
NEUTRAL_FILL = PatternFill("solid", fgColor="F1F5F9")
LINK_FONT = Font(color="0563C1", underline="single", size=10)
WRAP_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)

STATUS_FILLS: dict[str, PatternFill] = {
    "通过": PASS_FILL,
    "失败": FAIL_FILL,
    "异常": WARN_FILL,
    "轮次超限": WARN_FILL,
    "复读熔断": WARN_FILL,
    "已终止": NEUTRAL_FILL,
    "执行中": NEUTRAL_FILL,
    "等待中": NEUTRAL_FILL,
    "稳定通过": PASS_FILL,
    "稳定失败": FAIL_FILL,
    "波动": WARN_FILL,
    "数据不足": NEUTRAL_FILL,
    "改善": PASS_FILL,
    "恶化": FAIL_FILL,
    "持平": NEUTRAL_FILL,
    "基线": NEUTRAL_FILL,
    "幸运通过": WARN_FILL,
    "压线通过": WARN_FILL,
}

# (字段键, 表头, 列宽) —— 原始明细页完整列清单，与 JSONL 行字段对齐。
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
    ("case_version", "用例版本", 9),
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

# 用例结果页：人读关键字段（按 标识 / 配置 / 判定 / 归因 / 内容 / 性能 分组）。
RESULT_KEY_COLUMNS: list[tuple[str, str, int]] = [
    ("seq", "序号", 6),
    ("execution_id", "执行号", 10),
    ("trace_uid", "Trace UID", 22),
    ("case_id", "用例 ID", 9),
    ("case_code", "用例编号", 18),
    ("case_name", "用例名称", 24),
    ("case_type", "用例类型", 10),
    ("priority", "优先级", 8),
    ("case_status", "用例状态", 9),
    ("tags", "业务域标签", 14),
    ("owner", "用例负责人", 10),
    ("case_version", "用例版本", 9),
    ("grading_mode", "判分模式", 11),
    ("status", "执行状态", 10),
    ("status_code", "状态码", 12),
    ("pass_flag", "是否通过", 9),
    ("total_score", "总分", 8),
    ("termination_status", "终止状态", 10),
    ("attempt_count", "尝试次数", 9),
    ("lucky_pass", "幸运通过", 9),
    ("turns_used", "已用轮次", 9),
    ("max_turns", "最大轮次", 9),
    ("error_kind", "错误分类", 10),
    ("root_cause", "失败根因", 12),
    ("error_tags", "裁判标签", 18),
    ("judge_notes", "裁判备注", 36),
    ("query", "固定输入", 28),
    ("user_goal", "测试目标", 22),
    ("expected_answer", "期望答案", 36),
    ("final_answer", "最终答案", 36),
    ("self_check_conclusion", "追问自检结论", 30),
    ("started_at", "开始时间", 17),
    ("finished_at", "结束时间", 17),
    ("latency_ms", "耗时(ms)", 10),
    ("ttft_ms", "TTFT(ms)", 9),
    ("total_tokens", "总 Tokens", 10),
    ("environment_name", "执行环境", 15),
]

# 问题清单列。追加的 4 列为人工跟进空列（导出留空，人工在 Excel 内回填）。
ISSUE_COLUMNS: list[tuple[str, str, int]] = [
    ("seq", "序号", 6),
    ("priority", "优先级", 8),
    ("case_code", "用例编号", 18),
    ("case_name", "用例名称", 22),
    ("case_type", "用例类型", 10),
    ("tags", "业务域标签", 14),
    ("owner", "用例负责人", 10),
    ("case_version", "用例版本", 8),
    ("risk_type", "风险类型", 12),
    ("status", "执行状态", 10),
    ("total_score", "总分", 8),
    ("turns_ratio", "已用/最大对话轮次", 13),
    ("fail_turn", "失败发生轮次", 11),
    ("error_kind", "错误分类", 10),
    ("root_cause", "失败根因", 12),
    ("judge_conclusion", "裁判结论", 40),
    ("error_tags", "错误标签", 16),
    ("expected_answer", "期望答案", 36),
    ("final_answer", "实际最终答案", 36),
    ("fact_hit", "Must 事实命中", 12),
    ("fact_miss", "未命中事实", 28),
    ("rule_failures", "规则未通过项", 28),
    ("diag_summary", "轨迹诊断摘要", 32),
    ("sql_excerpt", "关键 SQL/接口摘录", 34),
    ("last_qa", "末轮问答摘录", 36),
    ("self_check_conclusion", "追问自检结论", 32),
    ("history_rate", "近10次通过率(全库)", 13),
    ("suggest_action", "建议排查方向", 40),
    ("execution_id", "执行号", 9),
    ("trace_uid", "Trace UID", 20),
    ("error_message", "错误信息", 26),
    ("latency_ms", "耗时(ms)", 10),
    ("follow_owner", "处理人（跟进）", 12),
    ("follow_conclusion", "处理结论", 24),
    ("follow_ownership", "问题归属", 11),
    ("follow_ticket", "缺陷单号", 13),
]


# ---------------------------------------------------------------- 标签与格式
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


def self_check_status_label(value: str | None) -> str | None:
    if not value:
        return None
    return SELF_CHECK_STATUS_LABELS.get(value, value)


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


def truncate_excerpt(text: str, limit: int = EXCERPT_LENGTH) -> str:
    """问题清单等关键页的摘录截断：短于单元格上限，保持一页可读。"""
    if len(text) <= limit:
        return text
    suffix = "…(截断，见原始明细/JSONL)"
    return text[: limit - len(suffix)] + suffix


def cell_text(value: Any) -> Any:
    """任意值 → 可安全写入单元格的值（JSON 文本化 + 截断 + 公式注入防护）。"""
    if isinstance(value, (dict, list)):
        value = format_json(value)
    if isinstance(value, str):
        text = truncate_cell(value)
        # 防止用户内容被 Excel 识别为公式（CSV/Excel 注入防护）。
        if text.startswith(("=", "+", "-", "@")):
            return f"'{text}"
        return text
    return value


# ---------------------------------------------------------------- 判分素材提炼
def fact_summary(fact_results: Any) -> tuple[str | None, str | None]:
    """从事实级判分结果提炼「Must 命中」与「未命中事实」两段人读文本。"""
    if not isinstance(fact_results, list) or not fact_results:
        return None, None
    must = [f for f in fact_results if isinstance(f, dict) and f.get("is_gate", True)]
    if not must:
        return None, None
    passed = [f for f in must if f.get("passed")]
    hit_text = f"Must {len(passed)}/{len(must)} 命中"
    misses: list[str] = []
    for item in must:
        if item.get("passed"):
            continue
        label = item.get("label") or item.get("fact_id") or "未命名事实"
        expected = item.get("expected")
        actual = item.get("actual")
        if expected not in (None, "") and actual not in (None, ""):
            misses.append(f"{label}（期望 {expected} / 实际 {actual}）")
        else:
            misses.append(str(label))
    return hit_text, "；".join(misses) or None


def rule_failure_text(rule_result: Any) -> str | None:
    """规则硬校验未通过项 → 一段可读文本（无失败项返回 None）。"""
    if not isinstance(rule_result, dict):
        return None
    checks = rule_result.get("checks")
    if not isinstance(checks, list):
        return None
    failures: list[str] = []
    for item in checks:
        if not isinstance(item, dict) or item.get("passed", True):
            continue
        label = item.get("label") or item.get("type") or "未命名规则"
        expected = item.get("expected")
        actual = item.get("actual")
        if expected not in (None, "") and actual not in (None, ""):
            failures.append(f"{label}：期望 {expected}，实际 {actual}")
        else:
            failures.append(str(item.get("reason") or label))
    return "；".join(failures) or None


def diagnostic_summary(diagnostics: Any) -> str | None:
    """轨迹诊断 → 摘要：工具调用 / 取数 / 缺失能力 / 工具错误 / 证据缺口。"""
    if not isinstance(diagnostics, dict) or not diagnostics:
        return None
    parts: list[str] = []
    calls = diagnostics.get("tool_call_count")
    rows = diagnostics.get("evidence_rows")
    if calls is not None:
        parts.append(f"工具调用 {calls} 次")
    if rows is not None:
        parts.append(f"取数 {rows} 行")
    missing = diagnostics.get("missing_capabilities") or []
    if missing:
        parts.append("缺失能力：" + "、".join(str(m) for m in missing))
    errors = diagnostics.get("tool_errors") or []
    if errors:
        parts.append(f"工具错误 {len(errors)} 条")
    if diagnostics.get("no_tool_call_but_claims"):
        parts.append("无工具调用却给出数值结论")
    if diagnostics.get("evidence_gap"):
        parts.append("证据缺口：有数值结论但无取数行")
    repeated = diagnostics.get("repeated_queries") or []
    if repeated:
        parts.append(f"重复查询 {len(repeated)} 次")
    return " · ".join(parts) or None


def sql_excerpt(turns: Any) -> str | None:
    """取最后一个带 generated_sql 的对话轮次作为取数线索。"""
    if not isinstance(turns, list):
        return None
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            continue
        sql = turn.get("generated_sql")
        if sql not in (None, ""):
            text = str(sql).strip()
            return truncate_excerpt(text, 300) if text else None
    return None


def last_qa_excerpt(turns: Any) -> str | None:
    """末轮问答摘录：失败定位最快的「一手素材」。"""
    if not isinstance(turns, list):
        return None
    for turn in reversed(turns):
        if not isinstance(turn, dict):
            continue
        query = turn.get("query")
        answer = turn.get("final_answer")
        if query in (None, "") and answer in (None, ""):
            continue
        qa = f"Q：{query or '—'}\nA：{answer or '—'}"
        return truncate_excerpt(qa, 400)
    return None


def error_tags_text(judge_result: Any, judges: Any = None) -> str | None:
    """聚合裁决与各裁判的错误标签去重合并。"""
    tags: list[str] = []
    if isinstance(judge_result, dict):
        for tag in judge_result.get("error_tags") or []:
            text = str(tag).strip()
            if text and text not in tags:
                tags.append(text)
    if isinstance(judges, list):
        for judge in judges:
            if not isinstance(judge, dict):
                continue
            for tag in judge.get("error_tags") or []:
                text = str(tag).strip()
                if text and text not in tags:
                    tags.append(text)
    return "、".join(tags) or None


def judge_conclusion(judge_notes: str | None, judges: Any = None) -> str | None:
    """裁判结论：优先聚合备注，缺失时回退到各裁判理由。"""
    if judge_notes:
        return judge_notes
    if isinstance(judges, list):
        reasons = [
            str(j.get("reason")).strip()
            for j in judges
            if isinstance(j, dict) and j.get("reason")
        ]
        return "；".join(reasons) or None
    return None


def suggest_action(
    *,
    status_code: str | None,
    error_kind: str | None = None,
    root_cause: str | None = None,
    lucky_pass: bool | None = None,
    fact_missed: bool = False,
    rule_failed: bool = False,
) -> str:
    """确定性规则生成「建议排查方向」（不调用 LLM，可复现可审计）。"""
    if status_code == "ERROR":
        if error_kind == "ENV":
            return "复跑确认；检查环境连通性 / 鉴权 / 超时配置（环境类异常）。"
        if error_kind == "LLM":
            return "检查裁判 / 模拟器模型配置与返回格式，必要时切换模型后重跑。"
        return "查看错误信息与轮次轨迹定位异常来源，复跑确认是否可稳定复现。"
    if status_code == "REPETITION_ABORT":
        return "检查上下文注入、会话隔离与复读熔断阈值（Agent 重复输出触发熔断）。"
    if status_code == "MAX_TURNS_EXCEEDED":
        return "检查终止条件与最大对话轮次设置；Agent 反复澄清未收敛，建议优化澄清策略后复跑。"
    if root_cause == "RETRIEVAL":
        return "核对取数工具、时间窗、权限与环境数据完整性（取数错误）。"
    if root_cause == "SYNTHESIS":
        return "核对期望答案、判分事实与汇总口径（答案组织 / 口径不一致）。"
    if root_cause == "HALLUCINATION":
        return "排查无依据数值：补充证据约束 / must_not，并核对相关口径（数据幻觉）。"
    if root_cause == "BEHAVIOR":
        return "检查响应契约：信息不足时应反问确认、假设需声明，不应直接作答（行为不合规）。"
    if lucky_pass:
        return "答案正确但无取数证据：为用例补充数值 / 事实 Must 约束，确认非「蒙对」。"
    if fact_missed or rule_failed:
        return "存在未命中事实 / 规则未过：优先核对用例期望与判分配置是否正确。"
    return "结合裁判理由与轮次轨迹人工判定：属用例期望问题则修订用例，否则提单跟踪系统缺陷。"


def enrich_result_rows(
    rows: list[dict[str, Any]],
    *,
    borderline_score: float = 70.0,
) -> list[dict[str, Any]]:
    """在收集好的行数据上追加「问题清单 / 多轮报表」所需的人读派生字段。

    直接在原 dict 上补充（原地修改并返回），供 Excel 问题清单与 JSONL 共用：
    - judge_conclusion / error_tags：裁判结论与错误标签（聚合裁决 + 各裁判合并）；
    - fact_hit / fact_miss：Must 事实命中数与未命中清单；
    - rule_failures：规则硬校验未通过项；
    - diag_summary：轨迹诊断摘要；
    - sql_excerpt / last_qa：取数线索与末轮问答摘录；
    - suggest_action：确定性规则生成的建议排查方向；
    - fact_missed / rule_failed：布尔标记，供多轮聚合复用。
    """
    for row in rows:
        rule_result = row.get("rule_result")
        fact_results = row.get("fact_results")
        judge_result = row.get("judge_result")
        judges = row.get("judges")
        turns = row.get("turns")
        diagnostics = row.get("diagnostics")
        row["judge_conclusion"] = judge_conclusion(row.get("judge_notes"), judges)
        row["error_tags"] = error_tags_text(judge_result, judges)
        fact_hit, fact_miss = fact_summary(fact_results)
        row["fact_hit"] = fact_hit
        row["fact_miss"] = fact_miss
        row["rule_failures"] = rule_failure_text(rule_result)
        row["diag_summary"] = diagnostic_summary(diagnostics)
        row["sql_excerpt"] = sql_excerpt(turns)
        row["last_qa"] = last_qa_excerpt(turns)
        row["fact_missed"] = bool(fact_miss)
        row["rule_failed"] = bool(row["rule_failures"])
        row["suggest_action"] = suggest_action(
            status_code=row.get("status_code"),
            error_kind=(
                "ENV"
                if row.get("error_kind") == "环境异常"
                else ("LLM" if row.get("error_kind") == "模型异常" else row.get("error_kind"))
            ),
            root_cause=_code_of(row.get("root_cause"), ROOT_CAUSE_LABELS),
            lucky_pass=row.get("lucky_pass") == "是",
            fact_missed=row["fact_missed"],
            rule_failed=row["rule_failed"],
        )
    return rows


def _code_of(label: str | None, labels: dict[str, str]) -> str | None:
    """中文标签 → 状态码（行数据里存的是标签，规则匹配需要码）。"""
    if label is None:
        return None
    for code, text in labels.items():
        if text == label:
            return code
    return label


# ---------------------------------------------------------------- 通用渲染
def write_table(
    sheet: Any,
    columns: list[tuple[str, str, int]],
    rows: list[list[Any]],
    *,
    color_headers: tuple[str, ...] = ("执行状态", "是否通过", "稳定性分级", "趋势判定", "风险类型"),
    score_headers: tuple[str, ...] = ("总分",),
) -> None:
    """写一张「表头 + 数据」的工作表：样式 / 冻结 / 筛选 / 条件格式。"""
    for column_index, (_key, label, width) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=column_index, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
        sheet.column_dimensions[get_column_letter(column_index)].width = width
    names = [c[1] for c in columns]
    for row_index, row in enumerate(rows, start=2):
        for column_index, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_index, column=column_index, value=cell_text(value))
            cell.alignment = WRAP_ALIGNMENT
            cell.font = Font(size=10)
            header = names[column_index - 1]
            if header in color_headers and isinstance(value, str):
                fill = STATUS_FILLS.get(value)
                if fill is None and value.startswith(("幸运通过", "压线通过")):
                    fill = WARN_FILL
                if fill is not None:
                    cell.fill = fill
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
        )
        for header in score_headers:
            if header in names:
                letter = get_column_letter(names.index(header) + 1)
                sheet.conditional_formatting.add(
                    f"{letter}2:{letter}{len(rows) + 1}",
                    ColorScaleRule(
                        start_type="num", start_value=0, start_color="F8696B",
                        mid_type="num", mid_value=60, mid_color="FFEB84",
                        end_type="num", end_value=100, end_color="63BE7B",
                    ),
                )


def write_overview(
    sheet: Any, sections: list[tuple[str, list[tuple[str, Any]]]]
) -> None:
    """分组纵向 KV：分区标题行加底色，标签加粗。"""
    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 92
    row = 1
    for title, pairs in sections:
        cell = sheet.cell(row=row, column=1, value=title)
        cell.fill = SECTION_FILL
        cell.font = SECTION_FONT
        sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        row += 1
        for label, value in pairs:
            label_cell = sheet.cell(row=row, column=1, value=label)
            label_cell.font = Font(bold=True, size=10)
            label_cell.alignment = Alignment(vertical="top")
            value_cell = sheet.cell(row=row, column=2, value=cell_text(value))
            value_cell.alignment = WRAP_ALIGNMENT
            value_cell.font = Font(size=10)
            row += 1
    sheet.freeze_panes = "A2"


def apply_hyperlinks(
    sheet: Any,
    columns: list[tuple[str, str, int]],
    row_count: int,
    header: str,
    url_builder: Any,
) -> None:
    """把某列的值（执行号 / Trace UID 等）渲染成可点击的平台超链接。"""
    names = [c[1] for c in columns]
    if header not in names:
        return
    column_index = names.index(header) + 1
    for row_index in range(2, row_count + 2):
        cell = sheet.cell(row=row_index, column=column_index)
        value = cell.value
        if value in (None, "", "—"):
            continue
        cell.hyperlink = url_builder(value)
        cell.font = LINK_FONT


def apply_dropdown(
    sheet: Any,
    columns: list[tuple[str, str, int]],
    row_count: int,
    header: str,
    options: list[str],
) -> None:
    """给某列加下拉校验（问题归属等人工跟进字段）。"""
    names = [c[1] for c in columns]
    if header not in names or row_count < 1:
        return
    letter = get_column_letter(names.index(header) + 1)
    validation = DataValidation(
        type="list", formula1='"' + ",".join(options) + '"', allow_blank=True
    )
    sheet.add_data_validation(validation)
    validation.add(f"{letter}2:{letter}{row_count + 1}")


def exec_url_builder(base_url: str) -> Any:
    def build(exec_id: Any) -> str:
        return f"{base_url}/trace?execution={exec_id}"

    return build


def trace_url_builder(base_url: str) -> Any:
    def build(trace_uid: Any) -> str:
        return f"{base_url}/trace?trace_uid={trace_uid}"

    return build


# ---------------------------------------------------------------- 概览组装
def first_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def judge_model_name(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        judges = row.get("judges")
        if isinstance(judges, list) and judges:
            first = judges[0]
            if isinstance(first, dict) and first.get("model_name"):
                return str(first["model_name"])
    return None


def distribution_text(rows: list[dict[str, Any]], key: str, labels: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        code = row.get("status_code")
        if code == "PASS":
            continue
        value = row.get(key)
        if value in (None, "", "否"):
            continue
        text = labels.get(str(value), str(value)) if isinstance(value, str) else str(value)
        counts[text] = counts.get(text, 0) + 1
    return " · ".join(f"{k} {v}" for k, v in counts.items()) or "—"


def _overview_sections(
    task: BatchTask,
    metrics: TaskMetrics,
    rows: list[dict[str, Any]],
    *,
    environment_name: str | None,
    scope_label: str,
    exported_by: str | None,
    exported_at: datetime,
    include_raw: bool,
    plan_context: dict[str, Any] | None,
) -> list[tuple[str, list[tuple[str, Any]]]]:
    distribution = metrics.status_distribution
    sections: list[tuple[str, list[tuple[str, Any]]]] = [
        (
            "基本信息",
            [
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
            ],
        ),
        (
            "版本信息",
            [
                ("模拟器模板版本", first_value(rows, "simulator_template_version")),
                ("裁判模板版本", first_value(rows, "judge_template_version")),
                ("判分器版本", first_value(rows, "grader_version")),
                ("裁判模型", judge_model_name(rows)),
            ],
        ),
        (
            "执行配置",
            [
                ("并发数", task.concurrency),
                ("QPS 限制", task.qps_limit),
                ("异常失败自动重试", bool_text(task.retry_on_error)),
                ("最大自动重试次数", task.max_error_retries),
                ("重试退避(秒)", task.retry_backoff_seconds),
                ("失败追问自检", bool_text(task.enable_self_check)),
            ],
        ),
        (
            "状态与耗时",
            [
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
            ],
        ),
        (
            "结果概览",
            [
                ("通过率(%)", metrics.pass_rate),
                ("平均分", metrics.avg_score),
                ("平均 TTFT(ms)", metrics.avg_ttft_ms),
                ("平均耗时(ms)", metrics.avg_latency_ms),
                ("P95 耗时(ms)", metrics.p95_latency_ms),
                ("平均轮次", metrics.avg_turns),
                ("模拟器 Tokens", metrics.simulator_tokens),
                ("裁判 Tokens", metrics.judge_tokens),
                ("总 Tokens", metrics.total_tokens),
                ("自动重试用例数", metrics.retry_count),
                ("已耗时(秒)", metrics.elapsed_seconds),
                ("吞吐(条/分)", metrics.throughput_per_minute),
            ],
        ),
        (
            "质量辅助",
            [
                ("幸运通过数", sum(1 for r in rows if r.get("lucky_pass") == "是")),
                ("失败根因分布", distribution_text(rows, "root_cause", ROOT_CAUSE_LABELS)),
                ("错误分类分布", distribution_text(rows, "error_kind", ERROR_KIND_LABELS)),
                (
                    "追问自检已追问数",
                    sum(1 for r in rows if r.get("self_check_status_code") == "DONE"),
                ),
                (
                    "追问自检未触发数",
                    sum(1 for r in rows if r.get("self_check_status_code") == "SKIPPED"),
                ),
                (
                    "追问自检失败数",
                    sum(1 for r in rows if r.get("self_check_status_code") == "ERROR"),
                ),
                ("任务错误信息", task.error_message),
            ],
        ),
    ]
    if plan_context:
        sections.append(("同计划参照", list(plan_context.get("rows", []))))
    sections.append(
        (
            "导出信息",
            [
                ("导出范围", scope_label),
                ("明细行数", len(rows)),
                (
                    "原始明细",
                    "已包含" if include_raw else "未包含（导出时勾选「包含原始明细」可附加）",
                ),
                ("指标汇总快照(JSON)", format_json(task.summary)),
                ("导出人", exported_by),
                ("导出时间", format_datetime(exported_at)),
            ],
        )
    )
    return sections


# ---------------------------------------------------------------- 问题清单
def build_issue_rows(
    rows: list[dict[str, Any]],
    *,
    borderline_score: float = 70.0,
    history_rates: dict[int, str] | None = None,
) -> list[list[Any]]:
    """问题清单行：非通过 + 幸运通过 / 压线通过，按严重度与优先级排序。"""
    history = history_rates or {}
    candidates: list[tuple[int, int, str, dict[str, Any], str]] = []
    for row in rows:
        status_code = str(row.get("status_code") or "")
        score = row.get("total_score")
        if status_code != "PASS":
            risk = status_label(status_code) or status_code
            severity = ISSUE_SEVERITY.get(status_code, 4)
        elif row.get("lucky_pass") == "是":
            risk = "幸运通过"
            severity = ISSUE_SEVERITY["RISK_PASS"]
        elif isinstance(score, (int, float)) and score < borderline_score:
            risk = "压线通过"
            severity = ISSUE_SEVERITY["RISK_PASS"]
        else:
            continue
        priority = str(row.get("priority") or "P3")
        candidates.append(
            (
                severity,
                PRIORITY_ORDER.get(priority, 9),
                str(row.get("case_code") or ""),
                row,
                risk,
            )
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    issue_rows: list[list[Any]] = []
    for severity, _priority, _code, row, risk in candidates:
        turns_used = row.get("turns_used")
        max_turns = row.get("max_turns")
        turns_ratio = (
            f"{turns_used}/{max_turns}"
            if turns_used is not None and max_turns is not None
            else None
        )
        if row.get("status_code") in ("PASS", None):
            fail_turn = None
        elif isinstance(turns_used, int) and turns_used > 1:
            fail_turn = (
                f"第 {turns_used} 轮收敛失败"
                if turns_used == max_turns
                else f"第 {turns_used} 轮"
            )
        else:
            fail_turn = "—"
        issue_rows.append(
            [
                len(issue_rows) + 1,
                row.get("priority"),
                row.get("case_code"),
                row.get("case_name"),
                row.get("case_type"),
                row.get("tags"),
                row.get("owner"),
                row.get("case_version"),
                risk,
                row.get("status"),
                row.get("total_score"),
                turns_ratio,
                fail_turn,
                row.get("error_kind"),
                row.get("root_cause"),
                row.get("judge_conclusion"),
                row.get("error_tags"),
                row.get("expected_answer"),
                row.get("final_answer") or "（无有效回复）",
                row.get("fact_hit"),
                row.get("fact_miss"),
                row.get("rule_failures"),
                row.get("diag_summary"),
                row.get("sql_excerpt"),
                row.get("last_qa"),
                row.get("self_check_conclusion"),
                history.get(row.get("case_id")),
                row.get("suggest_action"),
                row.get("execution_id"),
                row.get("trace_uid"),
                row.get("error_message"),
                row.get("latency_ms"),
                None,  # 处理人（跟进）
                None,  # 处理结论
                None,  # 问题归属
                None,  # 缺陷单号
            ]
        )
    return issue_rows


# ---------------------------------------------------------------- 工作簿
def build_task_results_xlsx(
    task: BatchTask,
    metrics: TaskMetrics,
    rows: list[dict[str, Any]],
    *,
    environment_name: str | None = None,
    scope_label: str = "用例最新",
    exported_by: str | None = None,
    exported_at: datetime | None = None,
    include_raw: bool = False,
    plan_context: dict[str, Any] | None = None,
    history_rates: dict[int, str] | None = None,
    base_url: str = "",
    borderline_score: float = 70.0,
) -> bytes:
    """生成单批次执行结果工作簿，返回 xlsx 字节。"""
    exported_at = exported_at or datetime.now()
    workbook = Workbook()

    overview = workbook.active
    overview.title = "执行概览"
    write_overview(
        overview,
        _overview_sections(
            task,
            metrics,
            rows,
            environment_name=environment_name,
            scope_label=scope_label,
            exported_by=exported_by,
            exported_at=exported_at,
            include_raw=include_raw,
            plan_context=plan_context,
        ),
    )

    issue_sheet = workbook.create_sheet("问题清单")
    issue_rows = build_issue_rows(
        rows, borderline_score=borderline_score, history_rates=history_rates
    )
    write_table(issue_sheet, ISSUE_COLUMNS, issue_rows)
    if base_url:
        apply_hyperlinks(
            issue_sheet, ISSUE_COLUMNS, len(issue_rows), "执行号", exec_url_builder(base_url)
        )
        apply_hyperlinks(
            issue_sheet, ISSUE_COLUMNS, len(issue_rows), "Trace UID", trace_url_builder(base_url)
        )
    apply_dropdown(
        issue_sheet, ISSUE_COLUMNS, len(issue_rows), "问题归属", OWNERSHIP_OPTIONS
    )

    results = workbook.create_sheet("用例结果")
    write_table(results, RESULT_KEY_COLUMNS, _result_key_rows(rows))
    if base_url:
        apply_hyperlinks(
            results, RESULT_KEY_COLUMNS, len(rows), "执行号", exec_url_builder(base_url)
        )
        apply_hyperlinks(
            results, RESULT_KEY_COLUMNS, len(rows), "Trace UID", trace_url_builder(base_url)
        )

    if include_raw:
        raw = workbook.create_sheet("原始明细")
        write_table(raw, TASK_RESULT_COLUMNS, _raw_rows(rows), color_headers=("执行状态", "是否通过"))

    write_overview(
        workbook.create_sheet("口径说明"),
        [
            (
                "口径速览",
                [
                    ("通过率分母", "只统计已产生终态结果的执行；执行中 / 等待中不计入。"),
                    ("尝试口径", "默认每用例取最新一次尝试，失败自动重跑不重复计数；导出时可展开全部尝试。"),
                    (
                        "风险类型",
                        "失败 / 异常 / 轮次超限 / 复读熔断 / 幸运通过（答案对但无取数证据）"
                        f"/ 压线通过（通过但总分低于 {borderline_score:g}）。",
                    ),
                    ("近10次通过率(全库)", "该用例在所有任务中的最近 10 次执行通过率，用于区分新 / 老问题。"),
                    ("执行轮次 vs 对话轮次", "执行轮次 = 计划中的第 k/N 批次；对话轮次 = 会话内交互轮数。"),
                    (
                        "超链接",
                        f"执行号 / Trace UID 可点击跳转平台详情页（基址 {base_url or '未配置'}）。",
                    ),
                    (
                        "人工跟进列",
                        "处理人 / 处理结论 / 问题归属（下拉：用例问题、系统问题、环境问题、待定）"
                        "/ 缺陷单号，留空供人工填写。",
                    ),
                    ("长文本截断", "问题清单摘录列截断并标注，完整内容见原始明细页或 JSONL 导出。"),
                ],
            )
        ],
    )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _result_key_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    data: list[list[Any]] = []
    for index, row in enumerate(rows, start=1):
        data.append([index if key == "seq" else row.get(key) for key, _label, _w in RESULT_KEY_COLUMNS])
    return data


def _raw_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    data: list[list[Any]] = []
    for index, row in enumerate(rows, start=1):
        data.append([index if key == "seq" else row.get(key) for key, _label, _w in TASK_RESULT_COLUMNS])
    return data
