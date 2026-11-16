"""AI 用例编写服务。

职责：
- 选择编写模型（显式 model_id → CASE_AUTHOR → JUDGE）；
- 召回业务口径与相似历史用例，渲染内置 Prompt；
- 调用 LLM 并对输出做白名单归一化；
- 对金额 / 笔数 / 账期等数值做「依据校验」，找不到权威依据的一律移入 missing，绝不编造；
- 生成建议编号但不落库，草稿默认 PENDING，等待人工确认。
"""

from __future__ import annotations

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
from billing_qa.models.enums import CaseType, GradingMode, InputType, ModelRole, TemplateType
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case_ai import (
    CaseAIDraft,
    CaseAIDraftContext,
    CaseAIDraftRequest,
    CaseAIDraftResponse,
    DraftFact,
    DraftMissing,
    DraftSource,
    DraftTurn,
)
from billing_qa.services.engine.context import resolve_template
from billing_qa.services.llm.client import LLMClient, ModelContext
from billing_qa.services.llm.json_parser import parse_json_object

logger = get_logger(__name__)

DEFAULT_PROMPT_CODE = "CASE_AUTHOR_DEFAULT"
DEFAULT_PROMPT_TYPE = TemplateType.CASE_AUTHOR
ALLOWED_SOURCES: set[str] = {"USER", "ASSET", "CASE", "LLM", "NONE"}
EVIDENCE_SOURCES = ("USER", "ASSET", "CASE")
FACT_KINDS = {"scalar", "text", "date", "set", "topk", "boolean"}

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
    "simulator_model_id",
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
async def select_author_model(db: AsyncSession, model_id: int | None) -> ModelConfig:
    """请求指定 → CASE_AUTHOR → JUDGE，逐级回退。"""
    if model_id:
        model = await db.get(ModelConfig, model_id)
        if model is None:
            raise ValidationError("指定的编写模型不存在")
        if not model.is_active:
            raise ValidationError(f"模型 {model.name} 已停用")
        return model

    for role in (ModelRole.CASE_AUTHOR, ModelRole.JUDGE):
        model = (
            await db.execute(
                select(ModelConfig)
                .where(ModelConfig.role == role.value, ModelConfig.is_active.is_(True))
                .order_by(ModelConfig.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if model is not None:
            return model
    raise ValidationError(
        "未配置可用的用例编写模型，请先在模型注册中新增 CASE_AUTHOR 角色的模型"
    )


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

    单轮与静态多轮均可改写；动态多轮由模拟器实时生成对话，当前无法稳定
    地基于基线脚本做增量改写，因此明确拒绝。
    """
    if payload.mode != "EDIT":
        return None
    if not payload.base_case_id:
        raise ValidationError("EDIT 模式必须提供 base_case_id")
    base = await db.get(TestCase, payload.base_case_id)
    if base is None or base.deleted_at is not None:
        raise ValidationError("要改写的基线用例不存在或已删除")
    case_type = str(base.case_type or "").upper()
    if case_type not in {CaseType.SINGLE, CaseType.STATIC_MULTI}:
        raise ValidationError("AI 改写目前支持单轮与静态多轮用例")
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
    """用于多候选去重：类型 / 名称 / 提问 / 轮次 / 事实组合一致即视为同一条。"""
    parts = [
        str(draft.case_type or ""),
        (draft.name or "").strip().lower(),
        re.sub(r"\s+", "", (draft.query or "").lower()),
        "|".join(re.sub(r"\s+", "", turn.query.lower()) for turn in draft.turns),
        "|".join(sorted(_fact_signature(item) for item in draft.must)),
        "|".join(sorted(_fact_signature(item) for item in draft.may)),
        "|".join(sorted(_fact_signature(item) for item in draft.must_not)),
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
        },
    )
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
    if base is None:
        rendered += (
            "\n\n# Static Multi-Turn Create\n"
            "新建默认输出单轮（case_type=\"SINGLE\" + query）。"
            "仅当用户明确要求多轮/追问/连续对话时，才返回 case_type=\"STATIC_MULTI\"，"
            "并在 turns 中给出至少两轮脚本（每轮 query 必填，expected_answer/notes 可选），"
            "此时不要输出单轮 query。轮次不足两轮会被回退为单轮。"
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


def _date_digits(value: Any) -> str:
    match = _DATE_PARTS_RE.search(str(value or ""))
    if match:
        year, month, day = match.group(1), int(match.group(2)), match.group(3)
        digits = f"{year}{month:02d}"
        if day:
            digits += f"{int(day):02d}"
        return digits
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 6 else ""


def value_in_text(value: Any, text: str, kind: str) -> bool:
    """校验一个事实值是否真的出现在依据文本里。"""
    if value is None:
        return False
    if kind == "date":
        target = _date_digits(value)
        if not target:
            return False
        haystack = re.sub(r"\D", "", text or "")
        return target in haystack
    try:
        target = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return False
    return any(abs(item - target) <= 1e-6 for item in _numbers(text))


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


def _normalize_turns(raw_items: Any, *, warnings: list[str]) -> list[DraftTurn]:
    """归一化静态多轮脚本：每轮只保留 query / expected_answer / notes。"""
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []
    turns: list[DraftTurn] = []
    for index, item in enumerate(raw_items, start=1):
        if isinstance(item, str):
            query = _as_text(item)
            expected_answer = None
            notes = None
        elif isinstance(item, dict):
            query = _as_text(item.get("query") or item.get("input"))
            expected_answer = _as_text(item.get("expected_answer"), limit=4000)
            notes = _as_text(item.get("notes"), limit=1000)
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
            )
        )
        if len(turns) >= 20:
            break
    return turns


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
    base: TestCase | None = None,
) -> CaseAIDraft:
    if not isinstance(raw, dict):
        raise ValidationError("模型未返回合法的用例草稿 JSON")

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
    if base_type == CaseType.STATIC_MULTI:
        # 用例类型创建后不可修改，编辑静态多轮时始终保持在静态多轮形态。
        case_type = CaseType.STATIC_MULTI
        if raw_type and raw_type not in {CaseType.SINGLE, CaseType.STATIC_MULTI}:
            warnings.append("基线为静态多轮，模型返回的动态多轮草稿已按静态多轮处理")
    elif raw_type == CaseType.STATIC_MULTI:
        # 新建：声明多轮且至少有两条有效提问才保留，否则退回单轮，避免建出空壳多轮用例。
        turns = _normalize_turns(raw.get("turns"), warnings=warnings)
        if len([turn for turn in turns if turn.query.strip()]) >= 2:
            case_type = CaseType.STATIC_MULTI
        else:
            case_type = CaseType.SINGLE
            warnings.append("新建多轮草稿的有效轮次不足两轮，已回退为单轮用例")
            if turns and not _as_text(raw.get("query")):
                raw = {**raw, "query": turns[0].query}
            turns = []
    else:
        case_type = CaseType.SINGLE
        if raw_type == CaseType.DYNAMIC_MULTI:
            warnings.append("动态多轮由模拟器实时生成，暂不支持 AI 生成；已按单轮处理")
        elif raw_type and raw_type != CaseType.SINGLE and base is not None:
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
        turns = _normalize_turns(raw.get("turns"), warnings=warnings)
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
        if not as_of:
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
        ):
            source = str(raw_sources.get(key) or "").strip().upper()
            if source in ALLOWED_SOURCES:
                field_sources[key] = source  # type: ignore[assignment]

    confidence = _as_float(raw.get("confidence"))
    confidence = 0.6 if confidence is None else min(1.0, max(0.0, confidence))
    if missing:
        confidence = min(confidence, max(0.15, 0.9 - 0.15 * len(missing)))

    model_warnings = _normalize_string_list(raw.get("warnings"), limit=10)
    warnings.extend(item for item in model_warnings if item not in warnings)

    return CaseAIDraft(
        case_type=case_type,
        name=_as_text(raw.get("name"), limit=200),
        description=_as_text(raw.get("description")),
        priority=priority,
        tags=_normalize_string_list(raw.get("tags"), limit=8),
        grading_mode=grading_mode,
        input_type=input_type,
        as_of=as_of,
        query=None if case_type == CaseType.STATIC_MULTI else _as_text(raw.get("query")),
        expected_answer=(
            None
            if case_type == CaseType.STATIC_MULTI
            else _as_text(raw.get("expected_answer"))
        ),
        turns=turns,
        must=must,
        may=may,
        must_not=must_not,
        forbidden_phrases=_normalize_string_list(raw.get("forbidden_phrases"), limit=10),
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
    model = await select_author_model(db, payload.model_id)
    base = await _load_base_case(db, payload)
    calibers = await _load_calibers(db)
    similar = await _load_similar_cases(db, payload.request)
    if base is not None:
        # 基线用例单独作为 Base Case 提示，避免在 Similar Cases 中重复。
        similar = [item for item in similar if item.id != base.id]
    caliber_text = "\n".join(_caliber_brief(item) for item in calibers)
    case_text = _evidence_case_text(base, similar)

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
