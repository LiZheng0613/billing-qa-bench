"""单用例历史执行报表：纯聚合 + 工作簿 / JSONL 渲染。

数据由调用方（API 层）按时间升序收集好传入（行结构与批次导出的
``_collect_task_results`` 行对齐，并经 ``enrich_result_rows`` 补充人读
派生字段），本模块只做统计与渲染，不连数据库，便于单元测试。

Excel 工作表（3 页）：
- 执行概览：用例信息、全量执行统计、版本 / 来源 / 环境分布、
  近 N 次趋势摘要（通过率、平均分、结果序列、稳定性）；
- 历史执行明细：一次执行一行（时间升序，即「第 N 次」），含状态 /
  分数（色阶）/ 用例版本 / 耗时与裁判结论等决策字段，执行号与
  Trace UID 渲染为平台超链接；
- 口径说明。

JSONL 导出（kind 区分 meta / row）：行数据不做任何截断，完整保留
轮次轨迹、裁判明细、用例快照等大字段，便于程序消费。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from io import BytesIO
from statistics import mean
from typing import Any

from openpyxl import Workbook

from billing_qa.models.test_case import TestCase
from billing_qa.services.io.execution_excel import (
    apply_hyperlinks,
    bool_text,
    case_status_label,
    case_type_label,
    exec_url_builder,
    format_datetime,
    grading_mode_label,
    review_status_label,
    risk_level_label,
    source_label,
    trace_url_builder,
    truncate_excerpt,
    write_overview,
    write_table,
)

CASE_HISTORY_JSONL_SCHEMA_NAME = "billing-qa/case-history"
CASE_HISTORY_JSONL_SCHEMA_VERSION = 1

# 近 N 次趋势窗口（与 /cases/{id}/trend 的「近 20 次」口径一致）。
NEAR_WINDOW = 20

# 历史执行明细页列（key, 表头, 列宽）。
CASE_HISTORY_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("seq", "序号", 8),
    ("execution_id", "执行号", 10),
    ("trace_uid", "Trace UID", 24),
    ("started_at", "执行时间", 19),
    ("task_name", "所属批次", 26),
    ("source", "来源", 10),
    ("status", "执行状态", 13),
    ("pass_flag", "是否通过", 10),
    ("total_score", "总分", 9),
    ("case_version", "用例版本", 10),
    ("turns_used", "轮次", 8),
    ("attempt_no", "尝试", 8),
    ("latency_ms", "耗时(ms)", 11),
    ("ttft_ms", "首字(ms)", 11),
    ("error_kind", "错误类型", 11),
    ("root_cause", "根因", 12),
    ("lucky_pass", "幸运通过", 10),
    ("self_check_status", "自检状态", 10),
    ("judge_conclusion", "裁判结论", 30),
    ("error_tags", "错误标签", 18),
    ("fact_hit", "事实命中", 10),
    ("fact_miss", "未命中事实", 32),
    ("rule_failures", "规则未过项", 32),
    ("diag_summary", "轨迹诊断摘要", 32),
    ("last_qa", "末轮问答摘录", 42),
    ("sql_excerpt", "关键SQL摘录", 32),
    ("error_message", "错误信息", 26),
    ("suggest_action", "建议排查方向", 30),
    ("environment_name", "环境", 12),
    ("expected_answer", "期望答案(执行时)", 30),
)


# ---------------------------------------------------------------- 统计
def _distribution(rows: list[dict[str, Any]], key: str) -> str:
    """把某列取值聚合成「值×次数」人读文本，按次数降序。"""
    counter = Counter(str(row.get(key)) for row in rows if row.get(key) is not None)
    if not counter:
        return "—"
    return "、".join(f"{value}×{count}" for value, count in counter.most_common())


def _pass_rate(rows: list[dict[str, Any]]) -> float:
    scored = [row for row in rows if row.get("pass_flag") not in (None, "—")]
    if not scored:
        return 0.0
    passed = sum(1 for row in scored if row.get("pass_flag") == "是")
    return round(passed / len(scored) * 100, 2)


def _avg(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(mean(values), 2) if values else None


def summarize_case_history(
    rows: list[dict[str, Any]], *, near_window: int = NEAR_WINDOW
) -> dict[str, Any]:
    """在按时间升序的行数据上计算概览统计（全量 + 近 N 次窗口）。

    口径与 /cases/{id}/trend 对齐：通过率分母为「有判定」的执行
    （pass_flag 非空，执行中 / 无裁判的记录不计入）。
    """
    near = rows[-near_window:] if near_window > 0 else []
    scored = [row for row in rows if row.get("pass_flag") not in (None, "—")]
    near_scored = [row for row in near if row.get("pass_flag") not in (None, "—")]
    scores = [float(row["total_score"]) for row in rows if row.get("total_score") is not None]
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    near_pass_rate = _pass_rate(near)
    flaky = len(near_scored) >= 3 and 0 < near_pass_rate < 100
    stability = (
        "波动 / Flaky（近窗口内既有通过也有失败）"
        if flaky
        else (
            "稳定（近窗口全部通过）"
            if near_scored and near_pass_rate == 100
            else ("存在失败（近窗口全部失败）" if near_scored else "样本不足（近窗口无有判定执行）")
        )
    )
    started_values = [row.get("started_at") for row in rows if row.get("started_at")]
    span_days: str | None = None
    if len(started_values) >= 2:
        try:
            first = datetime.strptime(str(started_values[0]), "%Y-%m-%d %H:%M:%S")
            last = datetime.strptime(str(started_values[-1]), "%Y-%m-%d %H:%M:%S")
            span_days = f"{(last - first).total_seconds() / 86400:.1f} 天"
        except ValueError:
            span_days = None
    return {
        "total": len(rows),
        "scored_count": len(scored),
        "no_verdict_count": len(rows) - len(scored),
        "pass_count": sum(1 for row in scored if row.get("pass_flag") == "是"),
        "fail_count": sum(1 for row in scored if row.get("pass_flag") == "否"),
        "pass_rate": _pass_rate(rows),
        "avg_score": _avg(rows, "total_score"),
        "max_score": max(scores) if scores else None,
        "min_score": min(scores) if scores else None,
        "avg_latency_ms": _avg(rows, "latency_ms"),
        "max_latency_ms": max(latencies) if latencies else None,
        "first_started_at": started_values[0] if started_values else None,
        "last_started_at": started_values[-1] if started_values else None,
        "span_days": span_days,
        "version_distribution": _distribution(rows, "case_version"),
        "source_distribution": _distribution(rows, "source"),
        "environment_distribution": _distribution(rows, "environment_name"),
        "near_total": len(near),
        "near_pass_rate": near_pass_rate,
        "near_avg_score": _avg(near, "total_score"),
        "near_avg_latency_ms": _avg(near, "latency_ms"),
        "near_sequence": "".join(
            "✓" if row.get("pass_flag") == "是" else ("✗" if row.get("pass_flag") == "否" else "－")
            for row in near
        )
        or None,
        "flaky": flaky,
        "stability": stability,
    }


# ---------------------------------------------------------------- Excel
def _case_overview_sections(
    case: TestCase,
    stats: dict[str, Any],
    *,
    exported_by: str | None,
    exported_at: datetime,
    include_sandbox: bool,
) -> list[tuple[str, list[tuple[str, Any]]]]:
    return [
        (
            "用例信息",
            [
                ("用例编号", case.code),
                ("用例名称", case.name),
                ("类型", case_type_label(case.case_type)),
                ("状态", case_status_label(case.status)),
                ("优先级", case.priority),
                ("当前版本", f"v{case.current_version}"),
                ("标签", "、".join(case.tags) if isinstance(case.tags, list) else case.tags),
                ("负责人", case.owner or "—"),
                ("数据集版本", case.dataset_version or "—"),
                ("评分模式", grading_mode_label(case.grading_mode)),
                ("风险等级", risk_level_label(case.risk_level)),
                ("审核状态", review_status_label(case.review_status)),
                ("期望答案（当前版本）", truncate_excerpt(case.expected_answer) or "—"),
            ],
        ),
        (
            "执行统计（全部历史）",
            [
                ("总执行次数", stats["total"]),
                (
                    "有判定 / 通过 / 失败",
                    f"{stats['scored_count']} / {stats['pass_count']} / {stats['fail_count']}",
                ),
                ("无判定次数（执行中 / 未出裁判）", stats["no_verdict_count"]),
                ("通过率（有判定口径）", f"{stats['pass_rate']}%"),
                (
                    "平均分 / 最高 / 最低",
                    (
                        f"{stats['avg_score']} / {stats['max_score']} / {stats['min_score']}"
                        if stats["avg_score"] is not None
                        else "—"
                    ),
                ),
                (
                    "平均耗时 / 最慢(ms)",
                    (
                        f"{stats['avg_latency_ms']} / {stats['max_latency_ms']}"
                        if stats["avg_latency_ms"] is not None
                        else "—"
                    ),
                ),
                ("首次执行时间", stats["first_started_at"] or "—"),
                ("最近执行时间", stats["last_started_at"] or "—"),
                ("执行跨度", stats["span_days"] or "—"),
            ],
        ),
        (
            "版本与来源分布",
            [
                ("用例版本分布", stats["version_distribution"]),
                ("执行来源分布", stats["source_distribution"]),
                ("环境分布", stats["environment_distribution"]),
            ],
        ),
        (
            f"近 {stats['near_total']} 次趋势",
            [
                ("通过率", f"{stats['near_pass_rate']}%"),
                (
                    "平均分 / 平均耗时(ms)",
                    (
                        f"{stats['near_avg_score']} / {stats['near_avg_latency_ms']}"
                        if stats["near_avg_score"] is not None
                        else "—"
                    ),
                ),
                ("结果序列（按时间先后）", stats["near_sequence"] or "—"),
                ("稳定性", stats["stability"]),
            ],
        ),
        (
            "导出信息",
            [
                ("导出人", exported_by or "—"),
                ("导出时间", format_datetime(exported_at)),
                (
                    "数据口径",
                    "全部历史执行（不含草稿试跑）"
                    if not include_sandbox
                    else "全部历史执行（含草稿试跑）",
                ),
                ("明细行数", stats["total"]),
                ("超链接说明", "明细页「执行号 / Trace UID」两列可点击跳转平台对应页面"),
            ],
        ),
    ]


def build_case_history_xlsx(
    case: TestCase,
    rows: list[dict[str, Any]],
    *,
    exported_by: str | None = None,
    exported_at: datetime | None = None,
    include_sandbox: bool = False,
    base_url: str = "",
) -> bytes:
    """生成单用例历史执行 Excel（概览 / 明细 / 口径说明），返回字节流。"""
    exported_at = exported_at or datetime.now()
    stats = summarize_case_history(rows)

    workbook = Workbook()

    # ---- 第 1 页：执行概览 ----
    overview = workbook.active
    overview.title = "执行概览"
    write_overview(
        overview,
        _case_overview_sections(
            case,
            stats,
            exported_by=exported_by,
            exported_at=exported_at,
            include_sandbox=include_sandbox,
        ),
    )

    # ---- 第 2 页：历史执行明细（时间升序 = 序号即「第 N 次」）----
    detail = workbook.create_sheet("历史执行明细")
    table_rows = [
        [index if key == "seq" else row.get(key) for key, _label, _width in CASE_HISTORY_COLUMNS]
        for index, row in enumerate(rows, start=1)
    ]
    write_table(detail, list(CASE_HISTORY_COLUMNS), table_rows, score_headers=("总分",))
    if base_url:
        apply_hyperlinks(
            detail,
            list(CASE_HISTORY_COLUMNS),
            len(table_rows),
            "执行号",
            exec_url_builder(base_url),
        )
        apply_hyperlinks(
            detail,
            list(CASE_HISTORY_COLUMNS),
            len(table_rows),
            "Trace UID",
            trace_url_builder(base_url),
        )

    # ---- 第 3 页：口径说明 ----
    notes = workbook.create_sheet("口径说明")
    write_overview(
        notes,
        [
            (
                "统计口径",
                [
                    ("行粒度", "一行 = 该用例的一次执行记录（含失败重试的全部尝试，按用例区分）"),
                    ("排序与序号", "按执行时间升序排列，「序号」即历史第 N 次执行"),
                    ("通过率分母", "只统计有判定结果的执行（pass_flag 非空）；执行中 / 未出裁判的记录不计入"),
                    ("草稿试跑", "默认排除（与用例趋势接口口径一致）；include_sandbox=true 时包含"),
                    ("稳定性判定", "近窗口内有判定的执行 ≥3 次且通过率在 (0,100) 区间 → 波动 / Flaky"),
                    ("用例版本", "每行取执行时绑定的 case_version；版本分布可判断失败是否由用例改版引入"),
                ],
            ),
            (
                "列说明",
                [
                    ("执行状态 / 是否通过", "颜色填充：绿=通过、红=失败、黄=告警类（幸运通过 / 压线）"),
                    ("总分", "红→黄→绿色阶（0→60→100），一眼看出分数趋势"),
                    ("期望答案(执行时)", "取自该次执行的用例快照；用例改版后期望可能变化"),
                    ("裁判结论 / 错误标签", "聚合裁决备注，缺失时回退各裁判归因说明"),
                    ("未命中事实 / 规则未过项", "Must 事实未命中清单与规则硬校验失败项"),
                    ("末轮问答摘录 / 关键SQL摘录", "截断保留前 500 字符，完整内容见 JSONL 导出"),
                    ("建议排查方向", "由确定性规则生成（环境异常 / 幸运通过 / 事实未命中等），不调用 LLM"),
                ],
            ),
            (
                "导出说明",
                [
                    ("Excel 截断", "单元格上限 32767 字符，超长字段（轮次轨迹等）截断加「…(已截断)」标记"),
                    ("JSONL 不截断", "format=jsonl 导出完整字段（轮次轨迹 / 裁决明细 / 用例快照）"),
                    ("超链接", "执行号 → /trace?execution=…；Trace UID → /trace?trace_uid=…"),
                ],
            ),
        ],
    )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- JSONL
def _dump_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)


def build_case_history_jsonl(
    case: TestCase,
    rows: list[dict[str, Any]],
    *,
    include_sandbox: bool = False,
    exported_by: str | None = None,
    exported_at: datetime | None = None,
) -> bytes:
    """生成单用例历史执行 JSONL 字节流，不做任何字段截断。"""
    exported_at = exported_at or datetime.now()
    stats = summarize_case_history(rows)
    meta: dict[str, Any] = {
        "kind": "meta",
        "schema": CASE_HISTORY_JSONL_SCHEMA_NAME,
        "schema_version": CASE_HISTORY_JSONL_SCHEMA_VERSION,
        "case_id": case.id,
        "case_code": case.code,
        "case_name": case.name,
        "case_current_version": case.current_version,
        "include_sandbox": include_sandbox,
        "exported_by": exported_by,
        "exported_at": exported_at.strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(rows),
        "stats": stats,
    }
    lines: list[str] = [_dump_line(meta)]
    column_keys = [key for key, _label, _width in CASE_HISTORY_COLUMNS if key != "seq"]
    for index, item in enumerate(rows, start=1):
        record: dict[str, Any] = {"kind": "row", "seq": index}
        for key in column_keys:
            record[key] = item.get(key)
        for key, value in item.items():
            if key not in record:
                record[key] = value
        lines.append(_dump_line(record))
    return ("\n".join(lines) + "\n").encode("utf-8")
