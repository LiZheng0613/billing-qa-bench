"""混合裁决引擎中的 LLM 软裁决：多裁判交叉裁决 + 红线策略。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger
from billing_qa.services.adapter.renderer import format_history, render_prompt
from billing_qa.services.llm.client import LLMClient, LLMError, ModelContext
from billing_qa.services.llm.json_parser import parse_structured

logger = get_logger(__name__)

DIMENSION_MAX: dict[str, float] = {
    "data_accuracy": 40,
    "logic_consistency": 30,
    "goal_completion": 20,
    "tone_clarity": 10,
}

DIMENSION_LABELS: dict[str, str] = {
    "data_accuracy": "数据准确性与防幻觉",
    "logic_consistency": "取数口径与逻辑一致性",
    "goal_completion": "目标达成与主动澄清",
    "tone_clarity": "服务规范与话术清晰度",
}


@dataclass
class JudgeOutcome:
    model_id: int | None
    model_name: str
    pass_flag: bool
    total_score: float
    dimension_scores: dict[str, float]
    error_tags: list[str]
    reason: str
    raw_response: dict[str, Any]
    judge_prompt: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    policy_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "pass": self.pass_flag,
            "total_score": self.total_score,
            "dimension_scores": self.dimension_scores,
            "error_tags": self.error_tags,
            "reason": self.reason,
            "policy_notes": self.policy_notes,
        }


def build_expected_text(snapshot: dict[str, Any]) -> str:
    """根据用例类型拼装喂给裁判的预期口径。"""
    case_type = snapshot.get("case_type")
    parts: list[str] = []
    if snapshot.get("user_goal"):
        parts.append(f"【测试目标】{snapshot['user_goal']}")
    if snapshot.get("expected_answer"):
        parts.append(f"【预期口径】{snapshot['expected_answer']}")
    if case_type == "STATIC_MULTI":
        session_expected = snapshot.get("session_expected") or {}
        if session_expected.get("expected_answer"):
            parts.append(f"【会话级预期】{session_expected['expected_answer']}")
        if session_expected.get("expected_rules"):
            parts.append(
                "【会话级规则】"
                + json.dumps(session_expected["expected_rules"], ensure_ascii=False)
            )
    if snapshot.get("expected_rules"):
        parts.append(
            "【规则硬校验期望】" + json.dumps(snapshot["expected_rules"], ensure_ascii=False)
        )
    if snapshot.get("hidden_context"):
        parts.append(
            "【隐藏信息（正确答案应覆盖）】"
            + json.dumps(snapshot["hidden_context"], ensure_ascii=False)
        )
    if not parts:
        parts.append("以完整对话是否解决用户诉求、数据是否自洽为准。")
    return "\n".join(parts)


def build_judge_variables(
    snapshot: dict[str, Any],
    turn_records: list[dict[str, Any]],
    termination_status: str,
) -> dict[str, Any]:
    """构造裁判模板变量。"""
    history: list[dict[str, str]] = []
    raw_db_data: list[Any] = []
    for item in turn_records:
        history.append({"role": "user", "content": item.get("query") or ""})
        history.append({"role": "assistant", "content": item.get("final_answer") or ""})
        if item.get("raw_db_data") is not None:
            raw_db_data.append(
                {"turn": item.get("turn_index"), "raw_db_data": item.get("raw_db_data")}
            )
    return {
        "case_type": snapshot.get("case_type") or "SINGLE",
        "chat_history": format_history(history, style="text"),
        "raw_db_data": json.dumps(raw_db_data, ensure_ascii=False, default=str)
        if raw_db_data
        else "（本轮未采集到白盒取数明细）",
        "expected_answer": build_expected_text(snapshot),
        "actual_turns": len(turn_records),
        "max_turns": snapshot.get("max_turns") or 1,
        "termination_status": termination_status,
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def coerce_judgement(
    data: dict[str, Any],
    *,
    termination_status: str,
    threshold: float,
) -> tuple[bool, float, dict[str, float], list[str], str, list[str]]:
    """把裁判原始 JSON 规整为可信分数，并施加红线策略。"""
    notes: list[str] = []
    raw_dims = data.get("dimension_scores") or {}
    dimensions: dict[str, float] = {}
    for name, maximum in DIMENSION_MAX.items():
        try:
            value = float(raw_dims.get(name, 0) or 0)
        except (TypeError, ValueError):
            value = 0.0
        dimensions[name] = round(_clamp(value, 0, maximum), 2)

    try:
        total = float(data.get("total_score") or 0)
    except (TypeError, ValueError):
        total = 0.0
    dim_sum = sum(dimensions.values())
    if dim_sum > 0 and (total <= 0 or abs(total - dim_sum) > 15):
        if total > 0:
            notes.append(f"总分与维度分不一致，已按维度合计校正为 {round(dim_sum, 2)}")
        total = dim_sum
    elif dim_sum == 0 and total > 0:
        scale = total / 100.0
        dimensions = {
            name: round(maximum * scale, 2) for name, maximum in DIMENSION_MAX.items()
        }
        notes.append("裁判未返回维度分，已按总分等比折算")
    total = round(_clamp(total, 0, 100), 2)

    raw_pass = data.get("pass")
    passed = bool(raw_pass) if isinstance(raw_pass, bool) else total >= threshold

    tags = [str(tag) for tag in (data.get("error_tags") or []) if str(tag).strip()]
    reason = str(data.get("reason") or "")

    # 红线 1：数据准确性 0 分 => 不通过
    if dimensions["data_accuracy"] <= 0:
        if passed:
            notes.append("红线策略：数据准确性为 0，强制判为不通过")
        passed = False
        if "数值错误/数据幻觉" not in tags:
            tags.append("数值错误/数据幻觉")

    # 红线 2：超出最大轮次 => 目标达成项 0 分且不通过
    if termination_status == "MAX_TURNS_EXCEEDED":
        if dimensions["goal_completion"] != 0:
            notes.append("红线策略：MAX_TURNS_EXCEEDED，目标达成项归零")
        dimensions["goal_completion"] = 0.0
        total = round(sum(dimensions.values()), 2)
        passed = False
        if "超出最大轮次" not in tags:
            tags.append("超出最大轮次")

    # 红线 3：复读机熔断 => 不通过
    if termination_status == "REPETITION_ABORT":
        passed = False
        if "复读机熔断" not in tags:
            tags.append("复读机熔断")

    # 红线 4：低于通过分数线
    if passed and total < threshold:
        passed = False
        notes.append(f"总分 {total} 低于通过线 {threshold}，判为不通过")

    return passed, total, dimensions, tags, reason, notes


async def _judge_one(
    model: ModelContext,
    prompt: str,
    *,
    termination_status: str,
    threshold: float,
    temperature: float | None,
) -> JudgeOutcome:
    async with LLMClient(model) as client:
        result = await client.chat(
            [
                {
                    "role": "system",
                    "content": "你是严格的评分引擎，只能输出合法 JSON，不得输出 Markdown。",
                },
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            temperature=temperature if temperature is not None else 0.0,
        )
    data = parse_structured(
        result.content, ["pass", "total_score", "dimension_scores", "error_tags", "reason"]
    )
    passed, total, dimensions, tags, reason, notes = coerce_judgement(
        data, termination_status=termination_status, threshold=threshold
    )
    return JudgeOutcome(
        model_id=model.id,
        model_name=f"{model.name}({model.model_name})",
        pass_flag=passed,
        total_score=total,
        dimension_scores=dimensions,
        error_tags=tags,
        reason=reason,
        raw_response=data,
        judge_prompt=prompt,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        policy_notes=notes,
    )


async def run_judges(
    *,
    models: list[ModelContext],
    prompt_template: str,
    variables: dict[str, Any],
    strategy: str,
    threshold: float,
    termination_status: str,
    temperature: float | None = None,
) -> tuple[dict[str, Any], list[JudgeOutcome]]:
    """并行调用全部裁判，并按 AVG / VETO 策略聚合。"""
    if not models:
        raise LLMError("未配置可用的裁判模型")
    prompt = render_prompt(prompt_template, variables, strict=False)
    tasks = [
        _judge_one(
            model,
            prompt,
            termination_status=termination_status,
            threshold=threshold,
            temperature=temperature,
        )
        for model in models
    ]
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    outcomes: list[JudgeOutcome] = []
    errors: list[str] = []
    for model, item in zip(models, settled, strict=False):
        if isinstance(item, BaseException):
            logger.warning("裁判 %s 调用失败: %s", model.name, item)
            errors.append(f"{model.name}: {item}")
        else:
            outcomes.append(item)
    if not outcomes:
        raise LLMError("全部裁判调用失败: " + "; ".join(errors))

    avg_total = round(sum(item.total_score for item in outcomes) / len(outcomes), 2)
    avg_dimensions = {
        name: round(sum(item.dimension_scores.get(name, 0) for item in outcomes) / len(outcomes), 2)
        for name in DIMENSION_MAX
    }
    merged_tags: list[str] = []
    for item in outcomes:
        for tag in item.error_tags:
            if tag not in merged_tags:
                merged_tags.append(tag)

    if strategy == "VETO":
        passed = all(item.pass_flag for item in outcomes)
    else:
        passed = avg_total >= threshold

    # 任一裁判命中数据红线则一票否决，避免平均分稀释严重错误
    if any(item.dimension_scores.get("data_accuracy", 0) <= 0 for item in outcomes):
        passed = False
    # 熔断类终止属于硬失败，无论评分策略如何都不允许通过
    if termination_status in {"MAX_TURNS_EXCEEDED", "REPETITION_ABORT"}:
        passed = False

    reason_parts = [
        f"【{item.model_name}】{item.total_score}分：{item.reason}" for item in outcomes
    ]
    if errors:
        reason_parts.append("【调用失败】" + "; ".join(errors))

    aggregate = {
        "pass": passed,
        "total_score": avg_total,
        "dimension_scores": avg_dimensions,
        "dimension_labels": DIMENSION_LABELS,
        "error_tags": merged_tags,
        "reason": "\n".join(reason_parts),
        "strategy": strategy,
        "threshold": threshold,
        "judge_count": len(outcomes),
        "policy_notes": [note for item in outcomes for note in item.policy_notes],
        "errors": errors,
        "simulator_tokens": 0,
        "judge_tokens": sum(item.prompt_tokens + item.completion_tokens for item in outcomes),
    }
    return aggregate, outcomes


def default_threshold() -> float:
    return settings.pass_threshold
