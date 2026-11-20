"""AI 用例编写服务。

职责：
- 选择编写模型（显式 model_id → CASE_AUTHOR → JUDGE）；
- 召回业务口径与相似历史用例，渲染内置 Prompt；
- 调用 LLM 并对输出做白名单归一化；
- 对金额 / 笔数 / 账期等数值做「依据校验」，找不到权威依据的一律移入 missing，绝不编造；
- 生成建议编号但不落库，草稿默认 PENDING，等待人工确认。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.exceptions import ValidationError
from billing_qa.core.logging import get_logger
from billing_qa.models.candidate import Caliber
from billing_qa.models.capability import Capability
from billing_qa.models.enums import (
    CaseType,
    GradingMode,
    InputType,
    JudgeStrategy,
    ModelRole,
    TemplateType,
)
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case import PlanSpec, RuleConfig, validate_request_variables
from billing_qa.schemas.case_ai import (
    CaseAIDraft,
    CaseAIDraftContext,
    CaseAIDraftRequest,
    CaseAIDraftResponse,
    DraftAssumptionScenario,
    DraftAssumptionSpec,
    DraftClarifySpec,
    DraftFact,
    DraftHiddenContext,
    DraftMissing,
    DraftPartialSpec,
    DraftRefuseSpec,
    DraftResponseContract,
    DraftSource,
    DraftTurn,
)
from billing_qa.services.engine.context import resolve_template
from billing_qa.services.llm.client import LLMClient, ModelContext
from billing_qa.services.llm.json_parser import parse_json_object
from billing_qa.services.llm.registry import resolve_unique_active

logger = get_logger(__name__)

DEFAULT_PROMPT_CODE = "CASE_AUTHOR_DEFAULT"
DEFAULT_PROMPT_TYPE = TemplateType.CASE_AUTHOR
ALLOWED_SOURCES: set[str] = {"USER", "ASSET", "CASE", "LLM", "NONE"}
EVIDENCE_SOURCES = ("USER", "ASSET", "CASE")
FACT_KINDS = {"scalar", "text", "date", "set", "topk", "boolean"}

# 与 schemas.case.validate_request_variables 保持一致：变量名规则与运行时保留字。
_REQUEST_VARIABLE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_REQUEST_VARIABLES = {"query", "history", "history_text", "turn", "timestamp_iso"}
_CONTRACT_BEHAVIORS = ("clarify", "answer_with_assumption", "partial", "refuse")
# 能力资产为空时，用系统既有用例声明过的能力编码作为兜底白名单。
_CAPABILITY_PLAN_KEYS = ("required_capabilities", "alternatives")

# LLM 只能输出语义字段；这些系统字段即使出现也必须丢弃。
FORBIDDEN_FIELDS = {
    "code",
    "id",
    "case_id",
    "environment_id",
    "simulator_template_id",
    "judge_template_id",
    "simulator_template_version",
    "judge_template_version",
    "judge_model_ids",
    "snapshot_id",
    "dedupe_hash",
    "review_status",
    "status",
}

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DATE_PARTS_RE = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})(?:\s*[-/月.]\s*(\d{1,2}))?")
_CODE_LIKE_RE = re.compile(r"^[A-Z]+-\d{8}-\d{3}$")

# 多草稿生成时逐条追加的侧重点，只改变表述与覆盖角度，不允许改变权威数值。
_VARIANT_FOCUS = (
    "保持业务口径不变，名称、描述与提问措辞必须与其它候选明显不同。",
    "侧重异常与边界（如金额为 0、账期跨月）的覆盖，但不得编造用户未提供、资产中也找不到的数值。",
    "侧重费用明细与口径一致性，名称、标签与提问措辞必须与其它候选不同。",
    "侧重高风险数值的回归校验，描述与标签必须与其它候选不同。",
    "侧重用户表达不清时的关键要素，保持单轮用例且不得新增无依据数值。",
)


# ---------------------------------------------------------------------- #
# 模型选择
# ---------------------------------------------------------------------- #
async def select_author_model(db: AsyncSession) -> ModelConfig:
    """用例编写模型由系统配置决定：取唯一启用的 CASE_AUTHOR 模型。

    界面上不再为「AI 帮我填」提供模型选择，避免同一份注册配置在不同入口
    出现不同结果；要换模型就在模型注册里停用旧的、启用新的。
    """
    return await resolve_unique_active(db, ModelRole.CASE_AUTHOR)


# ---------------------------------------------------------------------- #
# 资产召回
# ---------------------------------------------------------------------- #
async def _load_calibers(db: AsyncSession, limit: int = 40) -> list[Caliber]:
    rows = await db.execute(select(Caliber).order_by(Caliber.id.asc()).limit(limit))
    return list(rows.scalars().all())


def _caliber_brief(caliber: Caliber) -> str:
    return (
        f"[{caliber.key}] {caliber.name}：{(caliber.definition or '').strip()}"
        f"（口径来源：{caliber.canonical_source or 'BUSINESS_UI'}）"
    )


async def _load_capabilities(db: AsyncSession) -> list[tuple[str, str]]:
    """加载取数能力清单（编码, 名称）。

    优先使用能力资产；资产为空时退化为「系统既有用例声明过的能力编码」，
    保证 plan_spec.required_capabilities 只能落在真实存在的编码上。
    """
    rows = await db.execute(
        select(Capability.key, Capability.name)
        .where(Capability.is_active.is_(True))
        .order_by(Capability.key.asc())
    )
    items = [(str(key), str(name or key)) for key, name in rows.all() if key]
    if items:
        return items

    codes: dict[str, str] = {}
    case_rows = await db.execute(
        select(TestCase.plan_spec)
        .where(TestCase.deleted_at.is_(None))
        .order_by(TestCase.id.desc())
        .limit(200)
    )
    for (plan,) in case_rows.all():
        if not isinstance(plan, dict):
            continue
        for field in _CAPABILITY_PLAN_KEYS:
            value = plan.get(field)
            if field == "required_capabilities" and isinstance(value, list):
                for item in value:
                    text = str(item or "").strip()
                    if text:
                        codes.setdefault(text, text)
            elif field == "alternatives" and isinstance(value, dict):
                for key, alternatives in value.items():
                    text = str(key or "").strip()
                    if text:
                        codes.setdefault(text, text)
                    for item in alternatives or []:
                        alt = str(item or "").strip()
                        if alt:
                            codes.setdefault(alt, alt)
    return sorted(codes.items(), key=lambda item: item[0])


def _capability_brief(items: list[tuple[str, str]]) -> str:
    if not items:
        return "（系统暂无取数能力资产，plan_spec.required_capabilities 可留空）"
    return "\n".join(f"[{code}] {name}" for code, name in items)


# 追加在编写模板之后的扩展字段说明：覆盖抽屉表单里所有可填语义字段。
# 使用 {{capabilities}} 占位，避免与 JSON 示例里的花括号冲突。
_EXTENDED_FIELDS_BLOCK = """# System Capabilities（能力清单）
{{capabilities}}

# Extended Output Fields（可选字段；没有依据时省略或留空，禁止编造）
- request_variables: 用例级请求变量（渲染进请求头/请求体模板），如
  {"billing_period": "2026-10", "phone": "13800138000"}。
  变量名只能匹配 [A-Za-z_][A-Za-z0-9_]*，且不能使用 query/history/history_text/turn/timestamp_iso；
  含数字/日期的取值必须来自用户描述、业务口径或相似用例，找不到依据时不要输出该变量，
  改为写入 missing（field=request_variables.<变量名>）。
- expected_rules: 4 维评分规则（LEGACY 模式），如
  {"amounts":[{"label":"账单总额","value":268.5,"tolerance":0.01,"aliases":["total_amount"],"require_name":true}],
   "counts":[{"label":"费用项数","value":4}],"dates":["2026-10"],"must_contain":[],"must_not_contain":[]}。
  金额/笔数/日期同样必须有依据，否则留空并写入 missing。
- turns[].expected_rules: 静态多轮中每一轮的 4 维规则，形状同上。
- session_expected: 静态多轮的会话级预期（JSON 对象）。
- fail_fast: 静态多轮是否任一轮失败即停止（true/false）。
- response_contract: 仅当 input_type 不是 CLEAR 时输出，如
  {"allowed":["clarify","refuse"],
   "clarify":{"must_ask_slot":["time_range"],"must_not_assert":["amount"]},
   "answer_with_assumption":{"must_state_assumption":true,"scenarios":[{"assumption":"按当前账期","must":{"amount":"268.50"}}]},
   "partial":{"must_disclose":true,"required_facts":["<Must 事实业务名称>"]},
   "refuse":{"must_reason":true}}。
- plan_spec: 取数计划（仅诊断，不参与判分），如
  {"required_capabilities":["bill_query"],"alternatives":{"bill_query":["usage_query"]},
   "max_tool_calls":2,"allow_parallel":true}。
  required_capabilities 与 alternatives 里的能力编码只能从上面的 System Capabilities 中选择。
- extras_policy: "strict" 或 "lenient"（答案包络策略，默认 lenient）。
- judge_strategy / pass_threshold: 仅当用户明确要求时才输出（AVG/VETO，0-100）。
- change_summary: 仅 EDIT 模式，用一句话说明本次改了什么。
"""


def _keywords(text: str) -> set[str]:
    """轻量关键词：中文二元组 + 拉丁词，无需分词依赖。"""
    text = (text or "").lower()
    words = {word for word in re.findall(r"[a-z][a-z0-9_]{1,}", text)}
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", text)
    words.update(cjk[i : i + 2] for i in range(max(0, len(cjk) - 1)))
    return {word for word in words if word}


def _case_haystack(case: TestCase) -> str:
    parts = [case.name or "", case.description or "", case.query or ""]
    parts.extend(case.tags or [])
    return " ".join(parts)


async def _load_similar_cases(
    db: AsyncSession, request: str, limit: int = 5
) -> list[TestCase]:
    rows = await db.execute(
        select(TestCase)
        .where(TestCase.deleted_at.is_(None))
        .order_by(TestCase.updated_at.desc())
        .limit(120)
    )
    cases = list(rows.scalars().all())
    wanted = _keywords(request)
    scored: list[tuple[int, TestCase]] = []
    for case in cases:
        overlap = len(wanted & _keywords(_case_haystack(case)))
        scored.append((overlap, case))
    scored.sort(key=lambda item: (-item[0], -item[1].id))
    return [case for score, case in scored if score > 0][:limit]


async def _load_base_case(
    db: AsyncSession, payload: CaseAIDraftRequest
) -> TestCase | None:
    """EDIT 模式加载并校验基线用例：必须存在、未删除且为可改写类型。

    单轮、静态多轮与动态多轮都可改写：动态多轮只改语义字段
    （用户目标 / 初始提问 / 隐藏信息 / 最大轮次 / 终止条件），脚本仍由模拟器实时生成。
    """
    if payload.mode != "EDIT":
        return None
    if not payload.base_case_id:
        raise ValidationError("EDIT 模式必须提供 base_case_id")
    base = await db.get(TestCase, payload.base_case_id)
    if base is None or base.deleted_at is not None:
        raise ValidationError("要改写的基线用例不存在或已删除")
    case_type = str(base.case_type or "").upper()
    if case_type not in {CaseType.SINGLE, CaseType.STATIC_MULTI, CaseType.DYNAMIC_MULTI}:
        raise ValidationError("AI 改写目前支持单轮、静态多轮与动态多轮用例")
    return base


def _evidence_case_text(base: TestCase | None, similar: list[TestCase]) -> str:
    """基线用例与相似用例一起作为 CASE 证据，供数值依据校验使用。"""
    blocks = [_case_brief(base)] if base is not None else []
    blocks.extend(_case_brief(item) for item in similar)
    return "\n\n".join(blocks)


def _variant_hint(index: int, total: int, *, retry: bool = False) -> str:
    focus = _VARIANT_FOCUS[(index - 1) % len(_VARIANT_FOCUS)]
    prefix = "上一轮候选重复或数量不足，请换一个明显不同的侧重点。" if retry else ""
    return (
        f"{prefix}候选 {index}/{total}：{focus}"
        f"第 {index} 条仍需满足 Output JSON Schema，只输出一个 JSON 对象。"
    )


def _fact_signature(fact: DraftFact) -> str:
    if fact.kind in {"set", "topk"}:
        value = ",".join(fact.values or [])
    else:
        value = "" if fact.value is None else str(fact.value)
    return f"{fact.label.strip().lower()}={value}".strip().lower()


def _draft_signature(draft: CaseAIDraft) -> str:
    """用于多候选去重：类型 / 名称 / 提问 / 轮次 / 事实 / 扩展字段一致即视为同一条。"""
    parts = [
        str(draft.case_type or ""),
        (draft.name or "").strip().lower(),
        re.sub(r"\s+", "", (draft.query or "").lower()),
        "|".join(re.sub(r"\s+", "", turn.query.lower()) for turn in draft.turns),
        "|".join(sorted(_fact_signature(item) for item in draft.must)),
        "|".join(sorted(_fact_signature(item) for item in draft.may)),
        "|".join(sorted(_fact_signature(item) for item in draft.must_not)),
        json.dumps(
            {
                "request_variables": draft.request_variables,
                "expected_rules": (
                    draft.expected_rules.model_dump() if draft.expected_rules else None
                ),
                "turn_rules": [
                    turn.expected_rules.model_dump() if turn.expected_rules else None
                    for turn in draft.turns
                ],
                "session_expected": draft.session_expected,
                "fail_fast": draft.fail_fast,
                "response_contract": (
                    draft.response_contract.model_dump()
                    if draft.response_contract
                    else None
                ),
                "plan_spec": draft.plan_spec.model_dump() if draft.plan_spec else None,
                "extras_policy": draft.extras_policy,
                "judge_strategy": (
                    str(draft.judge_strategy) if draft.judge_strategy else None
                ),
                "pass_threshold": draft.pass_threshold,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    ]
    return "\n".join(parts)


def _fact_line(fact: dict[str, Any]) -> str:
    label = str(fact.get("label") or fact.get("id") or "事实").strip()
    kind = str(fact.get("kind") or "scalar")
    if kind in {"set", "topk"}:
        value = "、".join(str(item) for item in fact.get("values") or [])
    else:
        value = fact.get("value")
        value = "" if value is None else str(value)
    unit = str(fact.get("unit") or "").strip()
    return f"{label}={value}{(' ' + unit) if unit else ''}"


def _turn_query(turn: Any) -> str:
    if not isinstance(turn, dict):
        return ""
    return str(turn.get("query") or "").strip()


def _turns_from_base(base: TestCase | None) -> list[DraftTurn]:
    """把基线静态多轮脚本转换成草稿轮次，供模型未返回 turns 时兜底。"""
    if base is None or str(base.case_type or "").upper() != CaseType.STATIC_MULTI:
        return []
    turns: list[DraftTurn] = []
    for item in base.turns or []:
        query = _turn_query(item)
        if not query:
            continue
        turns.append(
            DraftTurn(
                query=query[:2000],
                expected_answer=_as_text(item.get("expected_answer"), limit=4000),
                notes=_as_text(item.get("notes"), limit=1000),
            )
        )
        if len(turns) >= 20:
            break
    return turns


def _case_brief(case: TestCase) -> str:
    envelope = case.answer_envelope or {}
    must = [_fact_line(item) for item in envelope.get("must") or [] if isinstance(item, dict)]
    may = [_fact_line(item) for item in envelope.get("may") or [] if isinstance(item, dict)]
    banned = [
        _fact_line(item) for item in envelope.get("must_not") or [] if isinstance(item, dict)
    ]
    lines = [f"[{case.code}] {case.name}"]
    if case.description:
        lines.append(f"描述：{case.description.strip()}")
    if case.case_type:
        lines.append(f"case_type：{case.case_type}")
    if str(case.case_type or "").upper() == CaseType.STATIC_MULTI:
        for index, turn in enumerate(case.turns or [], start=1):
            if not isinstance(turn, dict):
                continue
            query = _turn_query(turn)
            if query:
                lines.append(f"第{index}轮：{query}")
            answer = _as_text(turn.get("expected_answer"), limit=4000)
            if answer:
                lines.append(f"第{index}轮预期：{answer}")
            notes = _as_text(turn.get("notes"), limit=1000)
            if notes:
                lines.append(f"第{index}轮备注：{notes}")
    if str(case.case_type or "").upper() == CaseType.DYNAMIC_MULTI:
        # 动态多轮没有固定脚本，改写时必须把目标 / 隐藏信息 / 终止条件写进基线。
        if case.user_goal:
            lines.append(f"user_goal：{case.user_goal.strip()}")
        if case.initial_query:
            lines.append(f"initial_query：{case.initial_query.strip()}")
        if case.persona_description:
            lines.append(f"persona：{case.persona_description.strip()}")
        lines.append(f"max_turns：{case.max_turns or 5}")
        for item in case.hidden_context or []:
            if not isinstance(item, dict):
                continue
            key = _as_text(item.get("key"), limit=64) or "信息"
            content = _as_text(item.get("content"), limit=2000)
            if not content:
                continue
            hint = _as_text(item.get("release_hint"), limit=512)
            lines.append(f"隐藏信息：{key}｜{content}｜{hint or ''}")
        keywords = [
            str(item).strip()
            for item in ((case.terminate_conditions or {}).get("keywords") or [])
            if str(item).strip()
        ]
        if keywords:
            lines.append("终止关键词：" + "、".join(keywords))
    if case.query:
        lines.append(f"query：{case.query.strip()}")
    if case.as_of:
        lines.append(f"as_of：{case.as_of}")
    lines.append(f"判分方式：{case.grading_mode or 'LEGACY'}")
    lines.append(f"优先级：{case.priority or 'P1'}")
    if must:
        lines.append("Must：" + "；".join(must))
    if may:
        lines.append("May：" + "；".join(may))
    if banned:
        lines.append("MustNot：" + "；".join(banned))
    if case.tags:
        lines.append("标签：" + "、".join(str(item) for item in case.tags))
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# Prompt 渲染
# ---------------------------------------------------------------------- #
def _render(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", value, rendered)
    # 未声明的占位符统一置空，避免把 {{xxx}} 原样喂给模型。
    return re.sub(r"\{\{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}\}", "（未提供）", rendered)


async def _build_prompt(
    db: AsyncSession,
    payload: CaseAIDraftRequest,
    *,
    calibers: list[Caliber],
    similar: list[TestCase],
    capabilities: list[tuple[str, str]],
    base: TestCase | None = None,
    variant_hint: str | None = None,
) -> str:
    _template, version = await resolve_template(
        db,
        None,
        "LATEST",
        None,
        DEFAULT_PROMPT_TYPE,
        DEFAULT_PROMPT_CODE,
    )
    caliber_text = "\n".join(_caliber_brief(item) for item in calibers) or "（暂无业务口径资产）"
    case_text = "\n\n".join(_case_brief(item) for item in similar) or "（暂无相似历史用例）"
    capability_text = _capability_brief(capabilities)
    request_text = payload.request.strip()
    if base is not None:
        request_text = (
            f"{request_text}\n\n# Base Case（保留其业务语义，按用户要求改写）\n"
            f"{_case_brief(base)}"
        )
    rendered = _render(
        version.content,
        {
            "user_request": request_text,
            "mode": payload.mode,
            # 多候选时每次调用只要求返回一个对象，候选序号由 Draft Variant 说明。
            "count": str(1 if variant_hint else payload.count),
            "calibers": caliber_text,
            "similar_cases": case_text,
            "capabilities": capability_text,
        },
    )
    # 扩展字段说明放在模板之后追加：即使库里仍是旧版模板，也能覆盖到请求变量、
    # 4 维规则、响应契约、取数计划等新增字段。
    rendered += "\n\n" + _EXTENDED_FIELDS_BLOCK.replace("{{capabilities}}", capability_text)
    # 多草稿场景在末尾追加变体提示：放在 JSON Schema 之后，避免破坏模板结构。
    if variant_hint:
        rendered += f"\n\n# Draft Variant\n{variant_hint}"
    if base is not None and str(base.case_type or "").upper() == CaseType.STATIC_MULTI:
        rendered += (
            "\n\n# Static Multi-Turn Edit\n"
            "基线是静态多轮用例。请继续保持 case_type=\"STATIC_MULTI\"，"
            "在 turns 中给出完整的轮次脚本（每轮 query 必填，expected_answer/notes 可选），"
            "不要输出单轮 query。用户只改措辞或账期时，保留其余轮次与原判分语义；"
            "未要求修改的 Must/May/MustNot 事实可以原样返回，不要无依据新增数值。\n"
            "Output JSON 追加字段示例："
            '{"case_type":"STATIC_MULTI","turns":[{"query":"第一轮提问",'
            '"expected_answer":"该轮预期答案，可为空","notes":"可选备注"}]}'
        )
    if base is not None and str(base.case_type or "").upper() == CaseType.DYNAMIC_MULTI:
        rendered += (
            "\n\n# Dynamic Multi-Turn Edit\n"
            '基线是动态多轮用例（脚本由模拟器实时生成）。请继续保持 case_type="DYNAMIC_MULTI"，'
            "只回填语义字段：user_goal、initial_query、persona_description、hidden_context、"
            "max_turns、terminate_conditions，不要输出单轮 query 或 turns。"
            "hidden_context 每项含 key / content / release_hint，content 里的号码、账期等取值"
            "必须来自用户描述或基线原文，不要编造；用户没要求修改的项原样返回。\n"
            "Output JSON 追加字段示例："
            '{"case_type":"DYNAMIC_MULTI","user_goal":"用户最终要拿到的结论",'
            '"initial_query":"用户的第一句提问","hidden_context":[{"key":"手机号",'
            '"content":"用户描述里出现过的号码","release_hint":"客服反问号码时释放"}],'
            '"max_turns":6,"terminate_conditions":{"keywords":["账单总额"]}}'
        )
    if base is None:
        rendered += (
            "\n\n# Static Multi-Turn Create\n"
            "新建默认输出单轮（case_type=\"SINGLE\" + query）。"
            "仅当用户明确要求多轮/追问/连续对话时，才返回 case_type=\"STATIC_MULTI\"，"
            "并在 turns 中给出至少两轮脚本（每轮 query 必填，expected_answer/notes 可选），"
            "此时不要输出单轮 query。轮次不足两轮会被回退为单轮。"
            "\n\n# Dynamic Multi-Turn Create\n"
            "当用户要求动态多轮、模拟器实时追问、或「隐藏信息在被反问后才释放」时，"
            "返回 case_type=\"DYNAMIC_MULTI\"，必须给出 user_goal（用户最终要拿到什么），"
            "可选 initial_query、persona_description、hidden_context（每项 key/content/release_hint）、"
            "max_turns（1-20，默认 5）、terminate_conditions（如 {\"keywords\":[\"账单总额\"]}），"
            "此时不要输出 query 或 turns。"
            "hidden_context 的 content 只能用用户描述里已经出现的条件，不要编造号码或金额；"
            "缺少 user_goal 的草稿会被回退为单轮。"
        )
    return rendered


# ---------------------------------------------------------------------- #
# 数值依据校验
# ---------------------------------------------------------------------- #
def _numbers(text: str) -> list[float]:
    values: list[float] = []
    # 先抹掉日期片段，避免「2026-10」被拆成 2026 / 10 后误当作金额依据。
    masked = _DATE_PARTS_RE.sub(" ", text or "")
    for raw in _NUMBER_RE.findall(masked):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return values


def _date_match_digits(match: re.Match[str]) -> str:
    """把一个日期片段统一成 YYYY[MM[DD]] 数字串（月/日补前导零）。"""
    year, month, day = match.group(1), int(match.group(2)), match.group(3)
    digits = f"{year}{month:02d}"
    if day:
        digits += f"{int(day):02d}"
    return digits


def _date_digits(value: Any) -> str:
    match = _DATE_PARTS_RE.search(str(value or ""))
    if match:
        return _date_match_digits(match)
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 6 else ""


def value_in_text(value: Any, text: str, kind: str) -> bool:
    """校验一个事实值是否真的出现在依据文本里。"""
    if value is None:
        return False
    source = text or ""
    if kind == "date":
        target = _date_digits(value)
        if not target:
            return False
        # 文本里的日期同样先规整再比对，否则「2026 年 3 月」会因单位数月缺少
        # 前导零而被判成「找不到依据」。
        for match in _DATE_PARTS_RE.finditer(source):
            if _date_match_digits(match) == target:
                return True
        return target in re.sub(r"\D", "", source)
    try:
        target = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return False
    return any(abs(item - target) <= 1e-6 for item in _numbers(source))


# ---------------------------------------------------------------------- #
# 归一化
# ---------------------------------------------------------------------- #
def _as_text(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number


def _infer_kind(raw_kind: Any, value: Any, values: list[str]) -> str:
    kind = str(raw_kind or "").strip().lower()
    if kind in FACT_KINDS:
        return kind
    if values:
        return "set"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "scalar"
    if isinstance(value, str):
        if _date_digits(value) and re.search(r"[-/年月]", value):
            return "date"
        if _as_float(value) is not None:
            return "scalar"
        return "text"
    return "text"


def _normalize_string_list(value: Any, limit: int = 12) -> list[str]:
    if isinstance(value, str):
        items: Iterable[Any] = re.split(r"[,，、\n]", value)
    elif isinstance(value, list):
        items = value
    else:
        return []
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _normalize_facts(
    raw_items: Any,
    section: str,
    *,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
) -> list[DraftFact]:
    if not isinstance(raw_items, list):
        return []
    facts: list[DraftFact] = []
    prefix = {"must": "must", "may": "may", "must_not": "must-not"}[section]

    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        label = _as_text(item.get("label"), limit=200)
        if not label:
            label = _as_text(item.get("id"), limit=200)
        if not label:
            warnings.append(f"{section} 第 {index} 条事实缺少业务名称，已忽略")
            continue

        values = _normalize_string_list(item.get("values"))
        value = item.get("value")
        kind = _infer_kind(item.get("kind"), value, values)
        # MustNot 的值最终按文本比对，保留模型给出的原始字符串（如 "999.00"），
        # 避免转成数值后丢失精度与展示形态。
        if section == "must_not" and kind in {"scalar", "date"} and isinstance(value, str):
            kind = "text"
        unit = _as_text(item.get("unit"), limit=16)
        tolerance = _as_float(item.get("tolerance"))
        tolerance = tolerance if tolerance is not None and tolerance >= 0 else 0.0
        aliases = _normalize_string_list(item.get("aliases"), limit=8)
        keywords = _normalize_string_list(item.get("keywords"), limit=8)
        require_name = _as_bool(item.get("require_name"))

        declared = str(item.get("source") or "LLM").strip().upper()
        if declared not in ALLOWED_SOURCES:
            declared = "LLM"
        source_ref = _as_text(item.get("source_ref"), limit=128)

        # ---- 值类型收敛 ----
        payload: dict[str, Any]
        if kind == "scalar":
            number = _as_float(value)
            if number is None:
                downgraded = _as_text(value)
                if not downgraded:
                    missing.append(
                        DraftMissing(
                            field=f"{section}.{label}",
                            label=label,
                            reason="缺少可判定的数值，已留空待补充",
                        )
                    )
                    continue
                kind = "text"
                value = downgraded
                warnings.append(f"「{label}」声明为数值但取值不是数字，已按文本事实处理")
            else:
                value = number
            payload = {"value": value}
        elif kind == "date":
            text_value = _as_text(value)
            if not text_value:
                missing.append(
                    DraftMissing(
                        field=f"{section}.{label}",
                        label=label,
                        reason="缺少日期/账期，已留空待补充",
                    )
                )
                continue
            value = text_value
            payload = {"value": value}
        elif kind in {"set", "topk"}:
            if not values:
                missing.append(
                    DraftMissing(
                        field=f"{section}.{label}",
                        label=label,
                        reason="集合类事实缺少候选值，已留空待补充",
                    )
                )
                continue
            payload = {"values": values, "value": None}
        elif kind == "boolean":
            value = _as_bool(value)
            payload = {"value": value, "keywords": keywords}
        else:
            text_value = _as_text(value)
            if not text_value:
                missing.append(
                    DraftMissing(
                        field=f"{section}.{label}",
                        label=label,
                        reason="缺少文本取值，已留空待补充",
                    )
                )
                continue
            value = text_value
            payload = {"value": value, "keywords": keywords}

        # ---- 数值 / 日期依据校验 ----
        if section != "must_not" and kind in {"scalar", "date"}:
            resolved: str | None = None
            for candidate in [declared, *EVIDENCE_SOURCES]:
                if candidate not in EVIDENCE_SOURCES:
                    continue
                if value_in_text(value, evidence.get(candidate, ""), kind):
                    resolved = candidate
                    break
            if resolved is None:
                missing.append(
                    DraftMissing(
                        field=f"{section}.{label}",
                        label=label,
                        reason="用户描述、业务口径与相似用例中均找不到该数值的依据，已留空待补充",
                    )
                )
                warnings.append(f"「{label}」缺少数值依据，已留空，请人工补充后再保存")
                continue
            if resolved != declared:
                warnings.append(
                    f"「{label}」的来源由 {declared} 校正为 {resolved}（以实际依据文本为准）"
                )
            declared = resolved

        if source_ref and declared == "ASSET" and f"[{source_ref}]" not in evidence.get(
            "ASSET", ""
        ):
            source_ref = None
        if source_ref and declared == "CASE" and f"[{source_ref}]" not in evidence.get(
            "CASE", ""
        ):
            source_ref = None

        facts.append(
            DraftFact(
                id=f"{prefix}-{len(facts) + 1}",
                kind=kind,
                label=label,
                unit=unit,
                tolerance=tolerance,
                aliases=aliases,
                require_name=require_name,
                evidence_required=section != "must_not",
                verification_status="PENDING",
                section=section,
                source=declared,  # type: ignore[arg-type]
                source_ref=source_ref,
                **payload,
            )
        )
    return facts


def _value_needs_evidence(value: Any) -> bool:
    """只有含数字/日期特征的取值才要求权威依据，纯枚举词允许模型给出。"""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    return bool(re.search(r"\d", str(value)))


def _json_evidence_ok(value: Any, evidence_text: str) -> bool:
    if isinstance(value, dict):
        return all(_json_evidence_ok(item, evidence_text) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_json_evidence_ok(item, evidence_text) for item in value)
    if not _value_needs_evidence(value):
        return True
    text = str(value).strip()
    if re.search(r"20\d{2}\s*[-/年.]\s*\d{1,2}", text) and value_in_text(
        text, evidence_text, "date"
    ):
        return True
    try:
        return value_in_text(float(text.replace(",", "")), evidence_text, "scalar")
    except ValueError:
        return text.lower() in (evidence_text or "").lower()


def _normalize_request_variables(
    raw: Any,
    *,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
) -> dict[str, Any] | None:
    """归一化用例级请求变量：命名合法、避开运行时保留字、含数字取值必须有依据。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        warnings.append("请求变量必须是对象（key-value 映射），已忽略")
        return None
    evidence_text = "\n".join(evidence.values())
    result: dict[str, Any] = {}
    for raw_key, value in raw.items():
        key = str(raw_key or "").strip()
        if not key or not _REQUEST_VARIABLE_KEY.match(key):
            missing.append(
                DraftMissing(
                    field=f"request_variables.{key or '?'}",
                    label=key or "请求变量",
                    reason="变量名不合法（字母/数字/下划线，且不以数字开头），已忽略",
                )
            )
            warnings.append(f"请求变量名「{key}」不合法，已忽略")
            continue
        if key in _RESERVED_REQUEST_VARIABLES:
            missing.append(
                DraftMissing(
                    field=f"request_variables.{key}",
                    label=key,
                    reason="该变量由执行器运行时注入，不可自定义，已忽略",
                )
            )
            warnings.append(f"请求变量「{key}」由运行时注入，已忽略")
            continue
        if not _json_evidence_ok(value, evidence_text):
            missing.append(
                DraftMissing(
                    field=f"request_variables.{key}",
                    label=key,
                    reason="请求变量取值在用户描述/业务口径/相似用例中找不到依据，已留空待补充",
                )
            )
            warnings.append(f"请求变量「{key}」缺少依据，已留空，请人工补充")
            continue
        result[key] = value
    if not result:
        return None
    try:
        normalized = validate_request_variables(result)
    except ValueError as exc:
        warnings.append(f"请求变量未通过校验，已忽略：{exc}")
        return None
    return normalized


def _normalize_numeric_expectations(
    raw_items: Any,
    *,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
    field_prefix: str,
    label_prefix: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:20], start=1):
        if not isinstance(item, dict):
            continue
        label = _as_text(item.get("label"), limit=200) or f"{label_prefix}{index}"
        value = _as_float(item.get("value"))
        if value is None:
            missing.append(
                DraftMissing(
                    field=f"{field_prefix}.{label}",
                    label=label,
                    reason="缺少可判定的数值，已留空待补充",
                )
            )
            warnings.append(f"「{label}」缺少可判定数值，已留空，请人工补充")
            continue
        if not any(value_in_text(value, evidence[src], "scalar") for src in EVIDENCE_SOURCES):
            missing.append(
                DraftMissing(
                    field=f"{field_prefix}.{label}",
                    label=label,
                    reason="用户描述、业务口径与相似用例中均找不到该数值的依据，已留空待补充",
                )
            )
            warnings.append(f"「{label}」缺少数值依据，已留空，请人工补充")
            continue
        result.append(
            {
                "label": label,
                "value": value,
                "tolerance": max(0.0, _as_float(item.get("tolerance")) or 0.0),
                "aliases": _normalize_string_list(item.get("aliases"), limit=8),
                "require_name": _as_bool(item.get("require_name")),
            }
        )
    return result


def _normalize_expected_rules(
    raw: Any,
    *,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
    field_prefix: str = "expected_rules",
) -> RuleConfig | None:
    """归一化 4 维评分规则（LEGACY）：金额/笔数/日期同样遵守「无依据不落库」。"""
    if not isinstance(raw, dict):
        return None
    amounts = _normalize_numeric_expectations(
        raw.get("amounts"),
        evidence=evidence,
        missing=missing,
        warnings=warnings,
        field_prefix=f"{field_prefix}.amounts",
        label_prefix="金额",
    )
    counts = _normalize_numeric_expectations(
        raw.get("counts"),
        evidence=evidence,
        missing=missing,
        warnings=warnings,
        field_prefix=f"{field_prefix}.counts",
        label_prefix="笔数",
    )
    dates: list[str] = []
    raw_dates = raw.get("dates")
    for item in raw_dates if isinstance(raw_dates, list) else []:
        text = _as_text(item, limit=64)
        if not text:
            continue
        if not any(value_in_text(text, evidence[src], "date") for src in EVIDENCE_SOURCES):
            missing.append(
                DraftMissing(
                    field=f"{field_prefix}.dates.{text}",
                    label=text,
                    reason="找不到该日期的依据，已留空待补充",
                )
            )
            warnings.append(f"日期「{text}」缺少依据，已留空，请人工补充")
            continue
        dates.append(text)
    must_contain = _normalize_string_list(raw.get("must_contain"), limit=20)
    must_not_contain = _normalize_string_list(raw.get("must_not_contain"), limit=20)
    if not (amounts or counts or dates or must_contain or must_not_contain):
        return None
    return RuleConfig(
        amounts=amounts,
        counts=counts,
        dates=dates,
        must_contain=must_contain,
        must_not_contain=must_not_contain,
    )


def _normalize_response_contract(
    raw: Any,
    *,
    input_type: InputType,
    missing: list[DraftMissing],
    warnings: list[str],
) -> DraftResponseContract | None:
    """归一化非清晰输入的响应契约；草稿允许只给部分行为，保存前再补齐。"""
    if not isinstance(raw, dict):
        return None
    allowed: list[str] = []
    raw_allowed = raw.get("allowed")
    for item in raw_allowed if isinstance(raw_allowed, list) else []:
        key = str(item or "").strip().lower()
        if key in _CONTRACT_BEHAVIORS and key not in allowed:
            allowed.append(key)
    if input_type == InputType.CLEAR:
        if allowed:
            warnings.append("输入形态为「清晰明确」，响应契约不参与判分，已忽略")
        return None
    if not allowed:
        warnings.append("响应契约没有给出可接受行为，已忽略")
        return None
    if len(allowed) < 2:
        missing.append(
            DraftMissing(
                field="response_contract",
                label="响应契约",
                reason="非清晰输入至少需要两种可接受行为，草稿只给出一种，保存前请补齐",
            )
        )
        warnings.append("响应契约只给出一种可接受行为，保存前需补齐至少两种")

    contract = DraftResponseContract(allowed=allowed)  # type: ignore[arg-type]
    clarify_raw = raw.get("clarify") if isinstance(raw.get("clarify"), dict) else {}
    if "clarify" in allowed:
        slots = _normalize_string_list(clarify_raw.get("must_ask_slot"), limit=10)
        contract.clarify = DraftClarifySpec(
            must_ask_slot=slots or ["time_range"],
            must_not_assert=_normalize_string_list(
                clarify_raw.get("must_not_assert"), limit=10
            ),
        )
    assumption_raw = (
        raw.get("answer_with_assumption")
        if isinstance(raw.get("answer_with_assumption"), dict)
        else {}
    )
    if "answer_with_assumption" in allowed:
        scenarios: list[DraftAssumptionScenario] = []
        raw_scenarios = assumption_raw.get("scenarios")
        for item in raw_scenarios if isinstance(raw_scenarios, list) else []:
            if not isinstance(item, dict):
                continue
            assumption = _as_text(item.get("assumption"), limit=512) or ""
            must = item.get("must") if isinstance(item.get("must"), dict) else {}
            scenarios.append(DraftAssumptionScenario(assumption=assumption, must=must))
        if not scenarios:
            missing.append(
                DraftMissing(
                    field="response_contract.answer_with_assumption",
                    label="带假设作答",
                    reason="缺少假设场景，保存前请补充",
                )
            )
            warnings.append("「带假设作答」缺少假设场景，请人工补充")
        contract.answer_with_assumption = DraftAssumptionSpec(
            must_state_assumption=(
                _as_bool(assumption_raw.get("must_state_assumption"))
                if "must_state_assumption" in assumption_raw
                else True
            ),
            scenarios=scenarios,
        )
    partial_raw = raw.get("partial") if isinstance(raw.get("partial"), dict) else {}
    if "partial" in allowed:
        required = _normalize_string_list(partial_raw.get("required_facts"), limit=20)
        if not required:
            missing.append(
                DraftMissing(
                    field="response_contract.partial",
                    label="部分披露",
                    reason="未给出必须披露的事实，保存前请补充",
                )
            )
            warnings.append("「部分披露」未给出必须披露的事实，请人工补充")
        contract.partial = DraftPartialSpec(
            must_disclose=(
                _as_bool(partial_raw.get("must_disclose"))
                if "must_disclose" in partial_raw
                else True
            ),
            required_facts=required,
        )
    if "refuse" in allowed:
        refuse_raw = raw.get("refuse") if isinstance(raw.get("refuse"), dict) else {}
        contract.refuse = DraftRefuseSpec(
            must_reason=(
                _as_bool(refuse_raw.get("must_reason"))
                if "must_reason" in refuse_raw
                else True
            )
        )
    return contract


def _normalize_plan_spec(
    raw: Any,
    *,
    capability_codes: set[str],
    missing: list[DraftMissing],
    warnings: list[str],
) -> PlanSpec | None:
    """归一化取数计划：required_capabilities 只能落在真实能力编码上。"""
    if not isinstance(raw, dict):
        return None
    required: list[str] = []
    raw_required = raw.get("required_capabilities")
    for item in raw_required if isinstance(raw_required, list) else []:
        code = str(item or "").strip()
        if not code:
            continue
        if capability_codes and code not in capability_codes:
            warnings.append(f"取数能力「{code}」不在系统能力清单中，已忽略")
            continue
        if code not in required:
            required.append(code)
    alternatives: dict[str, list[str]] = {}
    raw_alts = raw.get("alternatives")
    if isinstance(raw_alts, dict):
        for key, values in raw_alts.items():
            code = str(key or "").strip()
            if not code:
                continue
            if capability_codes and code not in capability_codes:
                warnings.append(f"取数能力「{code}」不在系统能力清单中，已忽略")
                continue
            alts: list[str] = []
            for item in values if isinstance(values, list) else []:
                alt = str(item or "").strip()
                if not alt:
                    continue
                if capability_codes and alt not in capability_codes:
                    warnings.append(f"取数能力「{alt}」不在系统能力清单中，已忽略")
                    continue
                if alt not in alts:
                    alts.append(alt)
            if alts:
                alternatives[code] = alts
    max_calls = _as_float(raw.get("max_tool_calls"))
    max_tool_calls = int(max_calls) if max_calls is not None and max_calls >= 1 else None
    allow_parallel = _as_bool(raw.get("allow_parallel")) if "allow_parallel" in raw else True
    if not (required or alternatives or max_tool_calls):
        return None
    if not capability_codes and required:
        missing.append(
            DraftMissing(
                field="plan_spec",
                label="取数计划",
                reason="系统暂无能力资产，required_capabilities 未做白名单校验，请人工确认",
            )
        )
    return PlanSpec(
        required_capabilities=required,
        alternatives=alternatives,
        max_tool_calls=max_tool_calls,
        allow_parallel=allow_parallel,
    )


def _normalize_turns(
    raw_items: Any,
    *,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
) -> list[DraftTurn]:
    """归一化静态多轮脚本：每轮保留 query / expected_answer / notes / expected_rules。"""
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []
    turns: list[DraftTurn] = []
    for index, item in enumerate(raw_items, start=1):
        expected_rules: RuleConfig | None = None
        if isinstance(item, str):
            query = _as_text(item)
            expected_answer = None
            notes = None
        elif isinstance(item, dict):
            query = _as_text(item.get("query") or item.get("input"))
            expected_answer = _as_text(item.get("expected_answer"), limit=4000)
            notes = _as_text(item.get("notes"), limit=1000)
            expected_rules = _normalize_expected_rules(
                item.get("expected_rules"),
                evidence=evidence,
                missing=missing,
                warnings=warnings,
                field_prefix=f"turns.{index}.expected_rules",
            )
        else:
            continue
        if not query:
            warnings.append(f"第 {index} 轮缺少用户提问，已忽略")
            continue
        turns.append(
            DraftTurn(
                query=query[:2000],
                expected_answer=expected_answer,
                notes=notes,
                expected_rules=expected_rules,
            )
        )
        if len(turns) >= 20:
            break
    return turns


def _normalize_hidden_context(
    raw_items: Any,
    *,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
) -> list[DraftHiddenContext]:
    """归一化动态多轮的隐藏信息。

    隐藏信息是「客服反问后才释放」的条件，取值同样必须有依据：
    号码、金额等在用户描述 / 业务口径 / 相似用例里找不到的，一律丢弃并移入 missing。
    """
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []
    items: list[DraftHiddenContext] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(raw_items, start=1):
        source = "LLM"
        if isinstance(raw_item, str):
            key = f"info-{index}"
            content = _as_text(raw_item, limit=2000)
            release_hint = None
        elif isinstance(raw_item, dict):
            key = _as_text(raw_item.get("key") or raw_item.get("name"), limit=64) or f"info-{index}"
            content = _as_text(raw_item.get("content") or raw_item.get("value"), limit=2000)
            release_hint = _as_text(raw_item.get("release_hint"), limit=512)
            source = str(raw_item.get("source") or "LLM").strip().upper()
        else:
            continue
        if not content or key in seen:
            continue
        unsupported = [
            value
            for value in _numbers(content)
            if not any(value_in_text(value, text, "scalar") for text in evidence.values())
        ]
        if unsupported:
            missing.append(
                DraftMissing(
                    field=f"hidden_context.{key}",
                    label=key,
                    reason="隐藏信息里的取值在用户描述中找不到依据，已丢弃待补充",
                )
            )
            warnings.append(f"隐藏信息「{key}」缺少权威依据，已丢弃，请人工补充")
            continue
        if source not in ALLOWED_SOURCES:
            source = "LLM"
        if source == "LLM":
            # 模型常把用户原话里的取值也标成 LLM，按证据回填更准确的来源。
            source = _infer_source(content, evidence)
        seen.add(key)
        items.append(
            DraftHiddenContext(
                key=key,
                content=content,
                release_hint=release_hint,
                source=source,  # type: ignore[arg-type]
            )
        )
        if len(items) >= 20:
            break
    return items


def _normalize_dynamic(
    raw: dict[str, Any],
    *,
    base: TestCase | None,
    evidence: dict[str, str],
    missing: list[DraftMissing],
    warnings: list[str],
) -> dict[str, Any]:
    """归一化动态多轮字段；缺 user_goal 时交给上层回退单轮。"""
    user_goal = _as_text(raw.get("user_goal") or raw.get("goal"), limit=2000)
    if not user_goal and base is not None:
        user_goal = _as_text(base.user_goal, limit=2000)
    initial_query = (
        _as_text(raw.get("initial_query"), limit=2000)
        if "initial_query" in raw
        else (_as_text(base.initial_query, limit=2000) if base is not None else None)
    )
    if "persona_description" in raw or "persona" in raw:
        persona = _as_text(raw.get("persona_description") or raw.get("persona"), limit=2000)
    else:
        persona = _as_text(base.persona_description, limit=2000) if base is not None else None

    max_turns_raw = raw.get("max_turns")
    if max_turns_raw is None and base is not None:
        max_turns_raw = base.max_turns
    max_turns_value = _as_float(max_turns_raw)
    max_turns = int(max_turns_value) if max_turns_value is not None else 5
    if max_turns_value is not None and max_turns != max_turns_value:
        warnings.append(f"最大轮次 {max_turns_raw} 不是整数，已取整为 {max_turns}")
    if max_turns < 1 or max_turns > 20:
        clamped = min(20, max(1, max_turns))
        warnings.append(f"最大轮次 {max_turns} 超出 1-20，已收敛为 {clamped}")
        max_turns = clamped

    raw_terminate = raw.get("terminate_conditions")
    if raw_terminate is None and base is not None:
        raw_terminate = base.terminate_conditions
    terminate: dict[str, Any] | None = None
    if isinstance(raw_terminate, dict):
        keywords = [
            str(item).strip()
            for item in (raw_terminate.get("keywords") or [])
            if str(item).strip()
        ]
        if keywords:
            terminate = {"keywords": keywords[:10]}
        extra_keys = [key for key in raw_terminate if key != "keywords"]
        if extra_keys:
            warnings.append("终止条件只支持 keywords，其余字段已忽略：" + "、".join(extra_keys))

    if "hidden_context" in raw:
        hidden = _normalize_hidden_context(
            raw.get("hidden_context"), evidence=evidence, missing=missing, warnings=warnings
        )
        if base is not None:
            hidden = _align_hidden_with_base(hidden, base)
    elif base is not None:
        hidden = _normalize_hidden_context(
            base.hidden_context, evidence=evidence, missing=missing, warnings=warnings
        )
        hidden = [item.model_copy(update={"source": "CASE"}) for item in hidden]
    else:
        hidden = []

    return {
        "user_goal": user_goal,
        "initial_query": initial_query,
        "persona_description": persona,
        "hidden_context": hidden,
        "max_turns": max_turns,
        "terminate_conditions": terminate,
    }


def _infer_source(value: str, evidence: dict[str, str]) -> DraftSource:
    """按证据把隐藏信息/事实的来源回填为 USER / CASE / ASSET。"""
    for source in ("USER", "CASE", "ASSET"):
        text = evidence.get(source, "")
        if value_in_text(value, text, "scalar") or value_in_text(value, text, "date"):
            return source  # type: ignore[return-value]
    return "LLM"


def _align_hidden_with_base(
    items: list[DraftHiddenContext], base: TestCase
) -> list[DraftHiddenContext]:
    """编辑时隐藏信息若与基线取值一致，沿用基线键名并标记为 CASE 来源。"""
    base_items = base.hidden_context if isinstance(base.hidden_context, list) else []
    by_content: dict[str, str] = {}
    for item in base_items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        key = str(item.get("key") or "").strip()
        if content and key:
            by_content.setdefault(content, key)
    aligned: list[DraftHiddenContext] = []
    for item in items:
        base_key = by_content.get(item.content.strip())
        if base_key:
            aligned.append(item.model_copy(update={"key": base_key, "source": "CASE"}))
        else:
            aligned.append(item)
    return aligned


def _base_fact_items(base: TestCase | None, section: str) -> list[dict[str, Any]] | None:
    """EDIT 时模型未返回事实，则沿用基线事实，避免把契约语义静默清空。"""
    if base is None:
        return None
    envelope = base.answer_envelope or {}
    items = envelope.get(section)
    if not isinstance(items, list):
        return None
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        data.setdefault("source", "CASE")
        result.append(data)
    return result


def _normalize_draft(
    raw: Any,
    *,
    user_request: str,
    caliber_text: str,
    case_text: str,
    suggested_code: str,
    capability_codes: set[str] | None = None,
    base: TestCase | None = None,
) -> CaseAIDraft:
    if not isinstance(raw, dict):
        raise ValidationError("模型未返回合法的用例草稿 JSON")
    capability_codes = capability_codes or set()

    warnings: list[str] = []
    forbidden = sorted(FORBIDDEN_FIELDS & set(raw.keys()))
    if forbidden:
        warnings.append("模型输出了系统字段，已丢弃：" + "、".join(forbidden))
    if "drafts" in raw and isinstance(raw["drafts"], list):
        raw = raw["drafts"][0] if raw["drafts"] else {}

    semantic_keys = {
        "case_type",
        "name",
        "description",
        "priority",
        "tags",
        "grading_mode",
        "input_type",
        "as_of",
        "query",
        "expected_answer",
        "turns",
        "must",
        "may",
        "must_not",
        "forbidden_phrases",
        "risk_level",
        "missing",
        "warnings",
        "user_goal",
        "initial_query",
        "persona_description",
        "hidden_context",
        "max_turns",
        "terminate_conditions",
        "request_variables",
        "expected_rules",
        "session_expected",
        "fail_fast",
        "response_contract",
        "plan_spec",
        "extras_policy",
        "judge_strategy",
        "pass_threshold",
        "change_summary",
    }
    if not (semantic_keys & set(raw.keys())):
        raise ValidationError("模型返回内容不是合法的用例草稿，请检查编写模型或 Prompt 配置")

    # EDIT 时基线也作为 CASE 证据，避免"基线里的数值反而无依据"。
    if base is not None:
        case_text = f"{case_text}\n\n{_case_brief(base)}".strip()

    evidence = {"USER": user_request, "ASSET": caliber_text, "CASE": case_text}
    missing: list[DraftMissing] = []

    raw_type = str(raw.get("case_type") or "").strip().upper()
    base_type = str(base.case_type or "").upper() if base is not None else ""
    turns: list[DraftTurn] = []
    dynamic: dict[str, Any] = {}
    if base_type == CaseType.STATIC_MULTI:
        # 用例类型创建后不可修改，编辑静态多轮时始终保持在静态多轮形态。
        case_type = CaseType.STATIC_MULTI
        if raw_type and raw_type not in {CaseType.SINGLE, CaseType.STATIC_MULTI}:
            warnings.append("基线为静态多轮，模型返回的动态多轮草稿已按静态多轮处理")
    elif base_type == CaseType.DYNAMIC_MULTI:
        # 动态多轮同样不可改类型：只在语义字段上改写，脚本仍由模拟器实时生成。
        case_type = CaseType.DYNAMIC_MULTI
        if raw_type and raw_type != CaseType.DYNAMIC_MULTI:
            warnings.append("基线为动态多轮，模型返回的其他类型草稿已按动态多轮处理")
        dynamic = _normalize_dynamic(
            raw, base=base, evidence=evidence, missing=missing, warnings=warnings
        )
    elif raw_type == CaseType.STATIC_MULTI:
        # 新建：声明多轮且至少有两条有效提问才保留，否则退回单轮，避免建出空壳多轮用例。
        turns = _normalize_turns(
            raw.get("turns"), evidence=evidence, missing=missing, warnings=warnings
        )
        if len([turn for turn in turns if turn.query.strip()]) >= 2:
            case_type = CaseType.STATIC_MULTI
        else:
            case_type = CaseType.SINGLE
            warnings.append("新建多轮草稿的有效轮次不足两轮，已回退为单轮用例")
            if turns and not _as_text(raw.get("query")):
                raw = {**raw, "query": turns[0].query}
            turns = []
    elif raw_type == CaseType.DYNAMIC_MULTI:
        # 新建：动态多轮的脚本由模拟器实时生成，只要求给出 user_goal（以及可选的
        # 初始提问、人设、隐藏信息、最大轮次、终止条件）；缺目标则退回单轮。
        dynamic = _normalize_dynamic(
            raw, base=None, evidence=evidence, missing=missing, warnings=warnings
        )
        if dynamic.get("user_goal"):
            case_type = CaseType.DYNAMIC_MULTI
        else:
            case_type = CaseType.SINGLE
            dynamic = {}
            warnings.append("动态多轮草稿缺少用户目标（user_goal），已回退为单轮用例")
            fallback = _as_text(raw.get("query")) or _as_text(raw.get("initial_query"))
            if fallback:
                raw = {**raw, "query": fallback}
    else:
        case_type = CaseType.SINGLE
        if raw_type and raw_type != CaseType.SINGLE and base is not None:
            warnings.append("用例类型创建后不可修改，已按基线的单轮形态处理")

    grading_raw = str(raw.get("grading_mode") or "").strip().upper()
    if not grading_raw and base is not None:
        grading_raw = str(base.grading_mode or "").strip().upper()
    grading_mode = (
        GradingMode.ANSWER_CONTRACT
        if grading_raw == GradingMode.ANSWER_CONTRACT.value
        else GradingMode.LEGACY
    )
    input_raw = str(raw.get("input_type") or "").strip().upper()
    if not input_raw and base is not None:
        input_raw = str(base.input_type or "").strip().upper()
    input_type = InputType(input_raw) if input_raw in {item.value for item in InputType} else InputType.CLEAR
    # 模型给了响应契约却没标注输入形态：按「信息不全」处理，避免契约被静默丢弃。
    raw_contract = raw.get("response_contract")
    if (
        input_type == InputType.CLEAR
        and not input_raw
        and isinstance(raw_contract, dict)
        and raw_contract.get("allowed")
    ):
        input_type = InputType.UNDERSPECIFIED
        warnings.append("模型给出了响应契约但未标注输入形态，已按「信息不全」处理")

    raw_must = raw.get("must") if "must" in raw else _base_fact_items(base, "must")
    raw_may = raw.get("may") if "may" in raw else _base_fact_items(base, "may")
    raw_must_not = raw.get("must_not") if "must_not" in raw else _base_fact_items(base, "must_not")
    must = _normalize_facts(
        raw_must, "must", evidence=evidence, missing=missing, warnings=warnings
    )
    may = _normalize_facts(
        raw_may, "may", evidence=evidence, missing=missing, warnings=warnings
    )
    must_not = _normalize_facts(
        raw_must_not, "must_not", evidence=evidence, missing=missing, warnings=warnings
    )

    if case_type == CaseType.STATIC_MULTI:
        turns = _normalize_turns(
            raw.get("turns"), evidence=evidence, missing=missing, warnings=warnings
        )
        if not turns:
            fallback_query = _as_text(raw.get("query"))
            if fallback_query:
                turns = [DraftTurn(query=fallback_query[:2000])]
        if not turns:
            turns = _turns_from_base(base)
        if not turns:
            missing.append(
                DraftMissing(
                    field="turns",
                    label="多轮脚本",
                    reason="静态多轮至少需要一轮用户提问",
                )
            )
            warnings.append("静态多轮草稿缺少有效轮次，保存前必须补充")
    elif case_type == CaseType.DYNAMIC_MULTI:
        # 动态多轮没有固定脚本：把缺失的可选要素点出来，便于人工补齐。
        if not dynamic.get("initial_query") and not dynamic.get("hidden_context"):
            warnings.append(
                "动态多轮没有初始提问与隐藏信息，模拟器将以 user_goal 自行发起第一轮提问"
            )
        if not dynamic.get("terminate_conditions"):
            warnings.append("动态多轮未给出终止关键词，将按最大轮次自然结束")
        if not dynamic.get("hidden_context"):
            missing.append(
                DraftMissing(
                    field="hidden_context",
                    label="隐藏信息",
                    reason="动态多轮通常需要一条隐藏信息，否则无法验证「反问后才释放」",
                )
            )

    # 合并模型显式声明的缺失项（例如「费用项数」没有给出具体数字）。
    raw_missing = raw.get("missing")
    if isinstance(raw_missing, list):
        filled_labels = {
            str(fact.label)
            for fact in [*must, *may, *must_not]
            if getattr(fact, "label", None)
        }
        known_fields = {item.field for item in missing}
        for item in raw_missing[:20]:
            if not isinstance(item, dict):
                continue
            field = _as_text(item.get("field"), limit=128)
            label = _as_text(item.get("label"), limit=200)
            if not field or not label or label in filled_labels or field in known_fields:
                continue
            reason = _as_text(item.get("reason"), limit=512) or "缺少权威依据，已留空待补充"
            missing.append(DraftMissing(field=field, label=label, reason=reason))
            known_fields.add(field)

    as_of = _as_text(raw.get("as_of"), limit=64)
    if as_of is None and base is not None and "as_of" not in raw:
        as_of = _as_text(base.as_of, limit=64)
    if (
        as_of
        and not any(item.field == "as_of" for item in missing)
        and not value_in_text(as_of, user_request, "date")
        and not any(value_in_text(as_of, evidence[src], "date") for src in ("ASSET", "CASE"))
    ):
        missing.append(
            DraftMissing(
                field="as_of",
                label="冻结账期",
                reason="找不到账期依据，已留空待补充",
            )
        )
        warnings.append("账期缺少权威依据，已留空，请人工补充")
        as_of = None

    if grading_mode == GradingMode.ANSWER_CONTRACT:
        if not must:
            warnings.append("契约模式没有可用的 Must 事实，保存前必须人工补充至少一条")
            missing.append(
                DraftMissing(
                    field="must",
                    label="必答事实（Must）",
                    reason="契约模式至少需要一条 Must 事实",
                )
            )
        if not as_of and not any(item.field == "as_of" for item in missing):
            missing.append(
                DraftMissing(
                    field="as_of",
                    label="冻结账期",
                    reason="契约模式必须冻结 as_of（账期/日期）",
                )
            )

    priority = _as_text(raw.get("priority"), limit=8)
    priority = priority.upper() if priority else None
    if priority not in {"P0", "P1", "P2", "P3"}:
        if priority:
            warnings.append(f"优先级 {priority} 不合法，已回退为 P1")
        priority = "P1"

    risk_raw = str(raw.get("risk_level") or "LOW").strip().upper()
    risk_level = "HIGH" if risk_raw == "HIGH" else "LOW"

    # ---- 全字段覆盖：请求变量 / 4 维规则 / 会话 / 契约 / 计划 / 配置 ----
    request_variables = _normalize_request_variables(
        raw.get("request_variables"), evidence=evidence, missing=missing, warnings=warnings
    )
    expected_rules = _normalize_expected_rules(
        raw.get("expected_rules"), evidence=evidence, missing=missing, warnings=warnings
    )
    session_expected = (
        raw.get("session_expected") if isinstance(raw.get("session_expected"), dict) else None
    )
    fail_fast = _as_bool(raw.get("fail_fast")) if "fail_fast" in raw else None
    response_contract = _normalize_response_contract(
        raw.get("response_contract"),
        input_type=input_type,
        missing=missing,
        warnings=warnings,
    )
    plan_spec = _normalize_plan_spec(
        raw.get("plan_spec"),
        capability_codes=capability_codes,
        missing=missing,
        warnings=warnings,
    )
    extras_raw = str(raw.get("extras_policy") or "").strip().lower()
    extras_policy = extras_raw if extras_raw in {"strict", "lenient"} else None
    judge_raw = str(raw.get("judge_strategy") or "").strip().upper()
    judge_strategy = (
        JudgeStrategy(judge_raw)
        if judge_raw in {item.value for item in JudgeStrategy}
        else None
    )
    pass_threshold = _as_float(raw.get("pass_threshold"))
    if pass_threshold is not None and not 0 <= pass_threshold <= 100:
        warnings.append("通过分数线超出 0-100，已忽略")
        pass_threshold = None
    # 变更说明只在编辑既有用例时有意义。
    change_summary = (
        _as_text(raw.get("change_summary"), limit=512) if base is not None else None
    )

    field_sources: dict[str, DraftSource] = {}
    raw_sources = raw.get("field_sources")
    if isinstance(raw_sources, dict):
        for key in (
            "case_type",
            "name",
            "description",
            "priority",
            "tags",
            "grading_mode",
            "input_type",
            "as_of",
            "query",
            "expected_answer",
            "turns",
            "must",
            "may",
            "must_not",
            "risk_level",
            "user_goal",
            "initial_query",
            "persona_description",
            "hidden_context",
            "max_turns",
            "terminate_conditions",
            "request_variables",
            "expected_rules",
            "session_expected",
            "fail_fast",
            "response_contract",
            "plan_spec",
            "extras_policy",
            "judge_strategy",
            "pass_threshold",
            "change_summary",
        ):
            source = str(raw_sources.get(key) or "").strip().upper()
            if source in ALLOWED_SOURCES:
                # as_of 被依据校验清空后，不应残留「来源=用户」这类误导性标注。
                if key == "as_of" and not as_of:
                    continue
                field_sources[key] = source  # type: ignore[assignment]

    # 同一字段只保留一条待补充项（模型与服务端可能重复标记）。
    deduped_missing: list[DraftMissing] = []
    seen_missing_fields: set[str] = set()
    for item in missing:
        if item.field in seen_missing_fields:
            continue
        seen_missing_fields.add(item.field)
        deduped_missing.append(item)
    missing = deduped_missing

    confidence = _as_float(raw.get("confidence"))
    confidence = 0.6 if confidence is None else min(1.0, max(0.0, confidence))
    if missing:
        confidence = min(confidence, max(0.15, 0.9 - 0.15 * len(missing)))

    model_warnings = _normalize_string_list(raw.get("warnings"), limit=10)
    warnings.extend(item for item in model_warnings if item not in warnings)

    # 动态多轮的 query / turns 一律为空：脚本由模拟器实时生成。
    no_fixed_script = case_type in {CaseType.STATIC_MULTI, CaseType.DYNAMIC_MULTI}
    return CaseAIDraft(
        case_type=case_type,
        name=_as_text(raw.get("name"), limit=200),
        description=_as_text(raw.get("description")),
        priority=priority,
        tags=_normalize_string_list(raw.get("tags"), limit=8),
        grading_mode=grading_mode,
        input_type=input_type,
        as_of=as_of,
        query=None if no_fixed_script else _as_text(raw.get("query")),
        expected_answer=(
            None if no_fixed_script else _as_text(raw.get("expected_answer"))
        ),
        turns=turns,
        **(dynamic if case_type == CaseType.DYNAMIC_MULTI else {}),
        must=must,
        may=may,
        must_not=must_not,
        forbidden_phrases=_normalize_string_list(raw.get("forbidden_phrases"), limit=10),
        request_variables=request_variables,
        expected_rules=expected_rules,
        session_expected=session_expected,
        fail_fast=fail_fast,
        response_contract=response_contract,
        plan_spec=plan_spec,
        extras_policy=extras_policy,  # type: ignore[arg-type]
        judge_strategy=judge_strategy,
        pass_threshold=pass_threshold,
        change_summary=change_summary,
        risk_level=risk_level,  # type: ignore[arg-type]
        confidence=round(confidence, 2),
        field_sources=field_sources,
        missing=missing,
        warnings=warnings,
        suggested_code=suggested_code,
    )


# ---------------------------------------------------------------------- #
# 建议编号
# ---------------------------------------------------------------------- #
async def _next_ai_codes(db: AsyncSession, count: int = 1) -> list[str]:
    prefix = f"AI-{datetime.now():%Y%m%d}-"
    rows = await db.execute(select(TestCase.code).where(TestCase.code.like(f"{prefix}%")))
    used: set[int] = set()
    for (code,) in rows.all():
        match = re.fullmatch(re.escape(prefix) + r"(\d{3})", str(code))
        if match:
            used.add(int(match.group(1)))
    codes: list[str] = []
    seq = 1
    while len(codes) < max(1, count):
        if seq not in used:
            codes.append(f"{prefix}{seq:03d}")
        seq += 1
    return codes


def _extract_drafts(raw: Any) -> list[Any]:
    if isinstance(raw, dict) and isinstance(raw.get("drafts"), list):
        return raw["drafts"]
    return [raw]


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
async def generate_case_drafts(
    db: AsyncSession, payload: CaseAIDraftRequest
) -> CaseAIDraftResponse:
    started = time.perf_counter()
    model = await select_author_model(db)
    base = await _load_base_case(db, payload)
    calibers = await _load_calibers(db)
    capabilities = await _load_capabilities(db)
    similar = await _load_similar_cases(db, payload.request)
    if base is not None:
        # 基线用例单独作为 Base Case 提示，避免在 Similar Cases 中重复。
        similar = [item for item in similar if item.id != base.id]
    caliber_text = "\n".join(_caliber_brief(item) for item in calibers)
    case_text = _evidence_case_text(base, similar)
    capability_codes = {code for code, _name in capabilities}

    target = payload.count
    # 多候选需要重复调用模型；多给一次机会，用于尝试补齐去重后不足的数量。
    attempts = target if target <= 1 else target + 1
    # EDIT 不生成建议编号：改写的是既有用例，不应诱导用户另建一条。
    codes = [] if payload.mode == "EDIT" else await _next_ai_codes(db, target)

    drafts: list[CaseAIDraft] = []
    seen: set[str] = set()
    response_warnings: list[str] = []
    last_error: str | None = None

    try:
        async with LLMClient(ModelContext.from_orm(model)) as client:
            for attempt in range(attempts):
                if len(drafts) >= target:
                    break
                variant = (
                    _variant_hint(
                        min(attempt + 1, target),
                        target,
                        retry=attempt + 1 > target,
                    )
                    if target > 1
                    else None
                )
                prompt = await _build_prompt(
                    db,
                    payload,
                    calibers=calibers,
                    similar=similar,
                    capabilities=capabilities,
                    base=base,
                    variant_hint=variant,
                )
                temperature = min(model.effective_temperature or 0.2, 0.3)
                if attempt:
                    temperature = min(temperature + 0.08 * attempt, 0.5)
                result = await client.chat(
                    [
                        {
                            "role": "system",
                            "content": "你是严谨的问账测试用例编写助手，只输出合法 JSON 对象。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    json_mode=True,
                    temperature=temperature,
                )
                raw = parse_json_object(result.content)
                for item in _extract_drafts(raw):
                    if len(drafts) >= target:
                        break
                    try:
                        draft = _normalize_draft(
                            item,
                            user_request=payload.request,
                            caliber_text=caliber_text,
                            case_text=case_text,
                            suggested_code=codes[len(drafts)] if codes else None,
                            capability_codes=capability_codes,
                            base=base,
                        )
                    except ValidationError as exc:
                        last_error = str(exc)
                        continue
                    signature = _draft_signature(draft)
                    if signature in seen:
                        logger.info("AI 草稿重复，已跳过：%s", draft.name or "（未命名）")
                        continue
                    seen.add(signature)
                    drafts.append(draft)
    except ValidationError:
        raise
    except Exception as exc:
        logger.warning("AI 用例草稿生成失败: %s", exc)
        raise ValidationError(f"AI 用例草稿生成失败：{exc}") from exc

    if not drafts:
        raise ValidationError(last_error or "AI 未返回任何用例草稿")
    if len(drafts) < target:
        response_warnings.append(
            f"只生成 {len(drafts)} 条不重复草稿（期望 {target} 条），可补充描述后重新生成"
        )

    elapsed = int((time.perf_counter() - started) * 1000)
    return CaseAIDraftResponse(
        drafts=drafts,
        mode=payload.mode,
        base_case_id=base.id if base is not None else None,
        base_case_code=base.code if base is not None else None,
        model_id=model.id,
        model_code=model.code,
        model_name=model.name,
        elapsed_ms=elapsed,
        context=CaseAIDraftContext(calibers=len(calibers), similar_cases=len(similar)),
        warnings=response_warnings,
    )
