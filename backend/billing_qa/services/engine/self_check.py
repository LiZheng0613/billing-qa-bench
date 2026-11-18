"""失败追问自检：LLM 裁决判失败后，在同一会话内追问被测 Agent 自查原因。

触发与执行链路（由 ``CaseExecutor`` 在裁决完成、落库之前调用）：

1. ``self_check_trigger_reason`` 判定是否触发——只有「LLM 裁决判不通过」才追问：
   - ``LEGACY``（4 维评分）：聚合裁决 ``judge_result["pass"] is False``；
   - ``ANSWER_CONTRACT``（答案契约）：契约判分 ``judge_result["passed"] is False``。
2. ``build_self_check_variables`` 构造自检模板变量：除对话上下文外，
   额外注入各 LLM 裁判的归因说明（模型名 / 分数 / 是否通过 / 错误标签 / 理由）。
3. ``run_self_check`` 用资产化模板（``SELF_CHECK_DEFAULT``，支持版本管理与单用例
   ``prompt_overrides.self_check`` 覆写）渲染追问 Prompt，复用原对话最后一轮的
   会话值与完整历史，向被测 Agent 追加一轮追问，采集其自检结论。
4. 结果以 ``ExecutionRecord.self_check`` JSON 落库，不影响原裁决结论。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from billing_qa.core.logging import get_logger
from billing_qa.models.enums import ExecutionStatus, GradingMode
from billing_qa.services.adapter.renderer import format_history, render_prompt
from billing_qa.services.adapter.session import (
    resolve_session_mode,
    session_updates_from_response,
)
from billing_qa.services.adapter.target_api import TargetApiAdapter
from billing_qa.services.engine.judge import build_expected_text

logger = get_logger(__name__)

# 自检执行状态
SELF_CHECK_DONE = "DONE"
SELF_CHECK_SKIPPED = "SKIPPED"
SELF_CHECK_ERROR = "ERROR"


@dataclass
class SelfCheckResult:
    """一次追问自检的完整结果（落库到 ``ExecutionRecord.self_check``）。"""

    status: str  # DONE / SKIPPED / ERROR
    trigger: str | None = None
    skip_reason: str | None = None
    session_id: str | None = None
    template_code: str | None = None
    template_version: int | None = None
    prompt: str | None = None
    conclusion: str | None = None
    request_payload: dict[str, Any] | None = None
    response_raw: dict[str, Any] | None = None
    latency_ms: float | None = None
    error: str | None = None
    performed_at: str | None = None
    notes: list[str] = field(default_factory=list)

    @classmethod
    def skipped(cls, reason: str, **kwargs: Any) -> SelfCheckResult:
        return cls(status=SELF_CHECK_SKIPPED, skip_reason=reason, **kwargs)

    @classmethod
    def failed(cls, error: str, **kwargs: Any) -> SelfCheckResult:
        return cls(status=SELF_CHECK_ERROR, error=error, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status}
        for key, value in self.__dict__.items():
            if key == "status" or value is None:
                continue
            if key == "notes" and not value:
                continue
            data[key] = value
        return data


def self_check_trigger_reason(
    *,
    status: str,
    judge_result: dict[str, Any] | None,
    has_records: bool,
) -> str | None:
    """判定是否触发追问自检；触发时返回原因文案，未触发返回 ``None``。

    触发口径：**LLM 裁决判当前用例执行失败**（用户需求），且存在可追问的对话。
    - LEGACY：LLM 聚合裁决 ``pass`` 为 False（含熔断类红线强制不通过）；
    - ANSWER_CONTRACT：契约判分 ``passed`` 为 False（该模式下 LLM 裁判仅质量分参考，
      判定对错的「裁决」就是契约判分本身）。
    环境异常（ERROR）与被终止（ABORTED）没有可归因的裁决，一律不追问。
    """
    if not has_records:
        return None
    if status in {ExecutionStatus.ERROR, ExecutionStatus.ABORTED}:
        return None
    if not isinstance(judge_result, dict):
        return None
    if str(judge_result.get("mode") or GradingMode.LEGACY) == GradingMode.ANSWER_CONTRACT:
        if judge_result.get("passed") is False:
            return "答案契约判分不通过"
        return None
    if judge_result.get("pass") is False:
        return "LLM 裁决不通过"
    return None


def self_check_skip_reason(
    *,
    status: str,
    judge_result: dict[str, Any] | None,
    has_records: bool,
) -> str:
    """未触发追问自检时，说明跳过原因（落库便于追溯）。"""
    if status == ExecutionStatus.ERROR:
        return "执行异常（环境 / 系统错误），无裁决可归因，跳过追问自检"
    if status == ExecutionStatus.ABORTED:
        return "执行被终止，跳过追问自检"
    if not has_records:
        return "未产生有效对话轮次，跳过追问自检"
    if not isinstance(judge_result, dict):
        return "未产出 LLM 裁决结果，跳过追问自检"
    if str(judge_result.get("mode") or GradingMode.LEGACY) == GradingMode.ANSWER_CONTRACT:
        return "契约判分通过，未触发追问自检"
    return "LLM 裁决通过，未触发追问自检"


def format_judge_reasons(
    judge_result: dict[str, Any] | None,
    judges: Any,
    fact_results: list[dict[str, Any]] | None = None,
) -> str:
    """把裁决信息整理成喂给被测 Agent 的「裁判理由」文本。

    ``judges`` 支持 ``JudgeOutcome``（执行链路）与 ``JudgeResult`` ORM 行
    （沙盒 / 历史回放）两种形态——按属性名鸭子类型访问。
    """
    parts: list[str] = []
    contract_mode = isinstance(judge_result, dict) and str(
        judge_result.get("mode") or ""
    ) == GradingMode.ANSWER_CONTRACT

    if contract_mode:
        gates = judge_result.get("gates") or {}
        failed_gates = [str(key) for key, value in gates.items() if not value]
        if failed_gates:
            parts.append("【契约判分】不通过，未过闸门：" + "、".join(failed_gates))
        else:
            parts.append("【契约判分】不通过")
        root_cause = judge_result.get("root_cause")
        if root_cause:
            parts.append(f"【失败根因】{root_cause}")
        for fact in fact_results or []:
            if fact.get("passed") or not fact.get("is_gate", True):
                continue
            label = fact.get("label") or fact.get("fact_id") or "事实"
            reason = str(fact.get("reason") or "").strip()
            detail = (
                f"【事实未命中】{label}："
                f"期望 {fact.get('expected')!r}，实际 {fact.get('actual')!r}"
            )
            if reason:
                detail += f"；{reason}"
            parts.append(detail)

    judge_label = "质量分参考" if contract_mode else "裁判"
    for judge in judges or []:
        name = getattr(judge, "model_name", None) or "裁判模型"
        passed = getattr(judge, "pass_flag", None)
        score = getattr(judge, "total_score", None)
        tags = getattr(judge, "error_tags", None) or []
        reason = str(getattr(judge, "reason", None) or "").strip()
        verdict = "通过" if passed else "未通过"
        tag_text = (
            "（错误标签：" + "、".join(str(tag) for tag in tags) + "）" if tags else ""
        )
        parts.append(
            f"【{judge_label}·{name}】{score} 分，{verdict}{tag_text}\n"
            f"归因说明：{reason or '（未给出）'}"
        )

    if isinstance(judge_result, dict):
        policy_notes = judge_result.get("policy_notes") or (
            (judge_result.get("quality") or {}).get("policy_notes")
        )
        for note in policy_notes or []:
            text = str(note).strip()
            if text:
                parts.append(f"【裁决策略】{text}")

    return "\n".join(parts) or "（未产出裁判理由）"


def format_rule_failures(rule_result: dict[str, Any] | None) -> str:
    """整理规则硬校验失败项；契约模式 / 全通过时给出占位说明。"""
    if not isinstance(rule_result, dict):
        return "（本用例未执行规则硬校验）"
    checks = rule_result.get("checks") or []
    failed = [item for item in checks if not item.get("passed")]
    if not failed:
        return "（规则硬校验全部通过或无校验项）"
    lines: list[str] = []
    for check in failed:
        label = check.get("label") or check.get("type") or "校验项"
        expected = check.get("expected")
        actual = check.get("actual")
        reason = str(check.get("reason") or "").strip()
        detail = f"期望 {expected!r}，实际 {actual!r}" if expected is not None or actual is not None else ""
        line = f"- {label}"
        if detail:
            line += f"：{detail}"
        if reason:
            line += f"；{reason}"
        lines.append(line)
    return "\n".join(lines)


def build_self_check_variables(
    *,
    snapshot: dict[str, Any],
    turn_records: list[dict[str, Any]],
    judge_result: dict[str, Any] | None,
    judges: Any,
    fact_results: list[dict[str, Any]] | None = None,
    rule_result: dict[str, Any] | None = None,
    total_score: float = 0.0,
    pass_threshold: float = 60.0,
    termination_status: str = "COMPLETED",
) -> dict[str, Any]:
    """构造追问自检模板的动态变量（``{{var}}`` 渲染）。"""
    history: list[dict[str, str]] = []
    for item in turn_records:
        history.append({"role": "user", "content": str(item.get("query") or "")})
        history.append(
            {"role": "assistant", "content": str(item.get("final_answer") or "")}
        )
    final_answer = next(
        (
            str(item.get("final_answer") or "")
            for item in reversed(turn_records)
            if item.get("final_answer")
        ),
        "",
    )
    first_query = next(
        (
            str(item.get("query") or "")
            for item in turn_records
            if item.get("query")
        ),
        "",
    )

    error_tags: list[str] = []
    if isinstance(judge_result, dict):
        for tag in judge_result.get("error_tags") or []:
            text = str(tag)
            if text and text not in error_tags:
                error_tags.append(text)
    for judge in judges or []:
        for tag in getattr(judge, "error_tags", None) or []:
            text = str(tag)
            if text and text not in error_tags:
                error_tags.append(text)

    return {
        "case_name": snapshot.get("name") or snapshot.get("code") or "（未命名用例）",
        "case_type": snapshot.get("case_type") or "SINGLE",
        "judge_total_score": total_score,
        "pass_threshold": pass_threshold,
        "termination_status": termination_status,
        "actual_turns": len(turn_records),
        "max_turns": snapshot.get("max_turns") or 1,
        "judge_error_tags": "、".join(error_tags) if error_tags else "（无）",
        "judge_reasons": format_judge_reasons(judge_result, judges, fact_results),
        "rule_failures": format_rule_failures(rule_result),
        "expected_answer": build_expected_text(snapshot),
        "first_query": first_query or "（无输入）",
        "chat_history": format_history(history, style="text"),
        "final_answer": final_answer or "（无回复）",
    }


async def run_self_check(
    *,
    adapter: TargetApiAdapter,
    prompt: str | None,
    template_code: str | None,
    template_version: int | None,
    snapshot: dict[str, Any],
    turn_records: list[dict[str, Any]],
    judge_result: dict[str, Any] | None,
    judges: Any,
    fact_results: list[dict[str, Any]] | None = None,
    rule_result: dict[str, Any] | None = None,
    total_score: float = 0.0,
    pass_threshold: float = 60.0,
    status: str = "FAIL",
    termination_status: str = "COMPLETED",
) -> SelfCheckResult:
    """执行一次追问自检：判定触发 → 渲染模板 → 同会话追问 → 采集结论。

    自检失败（模板缺失 / 被测接口异常）不影响原执行结果，仅记录 ERROR。
    """
    base = {
        "template_code": template_code,
        "template_version": template_version,
    }
    trigger = self_check_trigger_reason(
        status=status, judge_result=judge_result, has_records=bool(turn_records)
    )
    if trigger is None:
        return SelfCheckResult.skipped(
            self_check_skip_reason(
                status=status, judge_result=judge_result, has_records=bool(turn_records)
            ),
            **base,
        )
    if not prompt:
        return SelfCheckResult.failed(
            "追问自检模板不可用（内置模板 SELF_CHECK_DEFAULT 未初始化），已跳过",
            trigger=trigger,
            **base,
        )

    variables = build_self_check_variables(
        snapshot=snapshot,
        turn_records=turn_records,
        judge_result=judge_result,
        judges=judges,
        fact_results=fact_results,
        rule_result=rule_result,
        total_score=total_score,
        pass_threshold=pass_threshold,
        termination_status=termination_status,
    )
    rendered = render_prompt(prompt, variables, strict=False)

    # 复用原对话的会话值与完整历史：SERVER_RETURNED 采纳服务端回填值，
    # CLIENT_GENERATED 沿用执行期生成的会话值——与原对话最后一轮保持同一会话。
    history: list[dict[str, str]] = []
    for item in turn_records:
        history.append({"role": "user", "content": str(item.get("query") or "")})
        history.append(
            {"role": "assistant", "content": str(item.get("final_answer") or "")}
        )
    last_session: str | None = None
    for item in reversed(turn_records):
        if item.get("session_id"):
            last_session = str(item["session_id"])
            break

    started = time.perf_counter()
    response = await adapter.chat(
        rendered,
        session_id=last_session,
        history=history,
        extra_variables=snapshot.get("request_variables") or {},
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    session_mode = resolve_session_mode(adapter.env.session_mode)
    effective_session = session_updates_from_response(
        session_mode, last_session, response.session_id
    )
    notes: list[str] = []
    if not last_session and session_mode == "SERVER_RETURNED":
        notes.append("原对话未回填会话值，追问自检由服务端按新会话处理")
    return SelfCheckResult(
        status=SELF_CHECK_DONE,
        trigger=trigger,
        session_id=effective_session,
        prompt=rendered,
        conclusion=response.final_answer or "",
        request_payload=response.request_payload,
        response_raw=response.response_payload,
        latency_ms=latency_ms,
        performed_at=datetime.now().isoformat(timespec="seconds"),
        notes=notes,
        **base,
    )
