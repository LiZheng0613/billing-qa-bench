"""AI 用例草稿归一化：数值依据校验与字段白名单。"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, Self

import pytest
from sqlalchemy import delete

from billing_qa.core.exceptions import ValidationError
from billing_qa.db.session import SessionLocal
from billing_qa.models.test_case import TestCase as CaseModel
from billing_qa.schemas.case_ai import CaseAIDraftRequest
from billing_qa.services.author import service
from billing_qa.services.author.service import (
    _case_brief,
    _draft_signature,
    _evidence_case_text,
    _load_base_case,
    _normalize_draft,
    _variant_hint,
    value_in_text,
)


def _draft(
    raw: dict,
    *,
    request: str = "查一下 13800138000 在 2026-10 的账单总额 268.50 元",
    base: CaseModel | None = None,
):
    return _normalize_draft(
        raw,
        user_request=request,
        caliber_text="[bill_total] 账单总额：套餐月费+超套费。",
        case_text="",
        suggested_code="AI-20261116-001",
        base=base,
    )


def test_user_number_is_kept() -> None:
    draft = _draft(
        {
            "name": "账单总额校验",
            "query": "帮我查一下 13800138000 在 2026-10 的账单总额",
            "grading_mode": "ANSWER_CONTRACT",
            "as_of": "2026-10",
            "must": [
                {
                    "label": "账单总额",
                    "kind": "scalar",
                    "value": 268.5,
                    "unit": "CNY",
                    "source": "USER",
                }
            ],
        }
    )
    assert len(draft.must) == 1
    fact = draft.must[0]
    assert fact.value == 268.5
    assert fact.source == "USER"
    assert fact.verification_status == "PENDING"
    assert not draft.missing
    assert draft.as_of == "2026-10"


def test_number_without_evidence_moves_to_missing() -> None:
    draft = _draft(
        {
            "name": "编造金额",
            "grading_mode": "ANSWER_CONTRACT",
            "as_of": "2026-10",
            "must": [
                {
                    "label": "账单总额",
                    "kind": "scalar",
                    "value": 1234.56,
                    "unit": "CNY",
                    "source": "LLM",
                }
            ],
        }
    )
    assert draft.must == []
    assert any(item.field == "must.账单总额" for item in draft.missing)
    assert any("缺少数值依据" in item for item in draft.warnings)


def test_asset_number_can_back_a_fact() -> None:
    draft = _normalize_draft(
        {
            "name": "超套费",
            "grading_mode": "LEGACY",
            "must": [
                {
                    "label": "超套单价",
                    "kind": "scalar",
                    "value": 3,
                    "unit": "CNY",
                    "source": "ASSET",
                }
            ],
        },
        user_request="超套费怎么算",
        caliber_text="[data_overage] 流量超套：套餐外流量按 3 元/GB 计费。",
        case_text="",
        suggested_code="AI-20261116-002",
    )
    assert len(draft.must) == 1
    assert draft.must[0].source == "ASSET"


def test_system_fields_are_dropped() -> None:
    draft = _draft(
        {
            "name": "带外键的草稿",
            "environment_id": 9,
            "judge_template_id": 3,
            "must": [],
            "grading_mode": "LEGACY",
        }
    )
    assert draft.name == "带外键的草稿"
    assert any("系统字段" in item and "environment_id" in item for item in draft.warnings)


def test_non_numeric_scalar_is_downgraded_to_text() -> None:
    draft = _draft(
        {
            "name": "文本事实",
            "grading_mode": "LEGACY",
            "must": [
                {
                    "label": "结清状态",
                    "kind": "scalar",
                    "value": "已结清",
                    "source": "USER",
                }
            ],
        }
    )
    assert len(draft.must) == 1
    assert draft.must[0].kind == "text"
    assert draft.must[0].value == "已结清"


def test_contract_mode_requires_as_of_and_must() -> None:
    draft = _draft(
        {
            "name": "空契约",
            "grading_mode": "ANSWER_CONTRACT",
            "must": [],
        }
    )
    fields = {item.field for item in draft.missing}
    assert "as_of" in fields
    assert "must" in fields


def test_date_digits_do_not_back_scalar_fact() -> None:
    """账期里的 2026 / 10 不能被当成金额依据。"""
    draft = _draft(
        {
            "name": "账期不是金额",
            "grading_mode": "ANSWER_CONTRACT",
            "as_of": "2026-10",
            "must": [
                {
                    "label": "费用项数",
                    "kind": "scalar",
                    "value": 10,
                    "unit": "项",
                    "source": "LLM",
                }
            ],
        },
        request="查一下 2026-10 的账单",
    )
    assert draft.must == []
    assert any(item.field == "must.费用项数" for item in draft.missing)


def test_must_not_keeps_original_string() -> None:
    draft = _draft(
        {
            "name": "禁止错误金额",
            "grading_mode": "LEGACY",
            "must_not": [
                {
                    "label": "错误金额",
                    "kind": "scalar",
                    "value": "999.00",
                    "source": "USER",
                }
            ],
        }
    )
    assert len(draft.must_not) == 1
    assert draft.must_not[0].kind == "text"
    assert draft.must_not[0].value == "999.00"


# ---------------------------------------------------------------------- #
# EDIT 基线 / 多候选
# ---------------------------------------------------------------------- #
def test_variant_hints_are_distinct() -> None:
    hints = [_variant_hint(index, 3) for index in (1, 2, 3)]
    assert len(set(hints)) == 3
    assert "候选 2/3" in hints[1]


def test_draft_signature_distinguishes_name_and_query() -> None:
    base = _draft({"name": "同一名称", "query": "同一个问题", "grading_mode": "LEGACY"})
    same = _draft({"name": "同一名称", "query": "同一个问题", "grading_mode": "LEGACY"})
    changed = _draft({"name": "另一名称", "query": "同一个问题", "grading_mode": "LEGACY"})
    assert _draft_signature(base) == _draft_signature(same)
    assert _draft_signature(base) != _draft_signature(changed)


def test_base_case_joins_case_evidence() -> None:
    base = CaseModel(
        code="BASE-001",
        name="基线用例",
        case_type="SINGLE",
        query="查一下 13800138000 在 2026-10 的账单总额",
        as_of="2026-10",
        grading_mode="ANSWER_CONTRACT",
        answer_envelope={
            "must": [
                {
                    "id": "must-1",
                    "kind": "scalar",
                    "label": "账单总额",
                    "value": 268.5,
                }
            ],
            "may": [],
            "must_not": [],
        },
    )
    text = _evidence_case_text(base, [])
    assert "[BASE-001]" in text
    assert "268.5" in text


def test_static_multi_base_brief_includes_turns() -> None:
    base = CaseModel(
        code="BASE-MULTI-001",
        name="静态多轮基线",
        case_type="STATIC_MULTI",
        turns=[
            {"index": 1, "query": "查一下账单", "expected_answer": "总额 268.50"},
            {"index": 2, "query": "那明细呢？", "notes": "追问明细"},
        ],
        as_of="2026-10",
        answer_envelope={"must": [], "may": [], "must_not": []},
    )
    text = _case_brief(base)
    assert "case_type：STATIC_MULTI" in text
    assert "第1轮：查一下账单" in text
    assert "第2轮预期" not in text
    assert "第2轮备注：追问明细" in text
    assert "优先级：P1" in text


def test_create_static_multi_with_two_turns_is_kept() -> None:
    draft = _draft(
        {
            "case_type": "STATIC_MULTI",
            "name": "新建多轮",
            "turns": [
                {"query": "查一下 13800138000 在 2026-10 的账单总额"},
                {"query": "那这笔账单的费用明细有哪些？"},
            ],
            "grading_mode": "LEGACY",
        }
    )
    assert draft.case_type == "STATIC_MULTI"
    assert [turn.query for turn in draft.turns] == [
        "查一下 13800138000 在 2026-10 的账单总额",
        "那这笔账单的费用明细有哪些？",
    ]
    assert draft.query is None
    assert not any("回退为单轮" in item for item in draft.warnings)


def test_create_static_multi_with_one_turn_falls_back_to_single() -> None:
    draft = _draft(
        {
            "case_type": "STATIC_MULTI",
            "name": "轮次不够",
            "turns": [{"query": "第一轮"}],
            "grading_mode": "LEGACY",
        }
    )
    assert draft.case_type == "SINGLE"
    assert draft.turns == []
    assert draft.query == "第一轮"
    assert any("不足两轮" in item for item in draft.warnings)


def test_create_dynamic_multi_keeps_goal_and_hidden_context() -> None:
    draft = _draft(
        {
            "case_type": "DYNAMIC_MULTI",
            "name": "动态多轮 · 隐藏号码",
            "user_goal": "不主动给号码，被反问后才释放 13800138000，拿到账单总额",
            "initial_query": "我想核对话费",
            "persona_description": "说话简短的普通用户",
            "hidden_context": [
                {
                    "key": "手机号",
                    "content": "13800138000",
                    "release_hint": "客服反问号码时提供",
                }
            ],
            "max_turns": 6,
            "terminate_conditions": {"keywords": ["账单总额"]},
            "grading_mode": "LEGACY",
        }
    )
    assert draft.case_type == "DYNAMIC_MULTI"
    assert draft.user_goal and "13800138000" in draft.user_goal
    assert draft.initial_query == "我想核对话费"
    assert draft.persona_description == "说话简短的普通用户"
    assert [item.key for item in draft.hidden_context] == ["手机号"]
    assert draft.hidden_context[0].content == "13800138000"
    assert draft.max_turns == 6
    assert draft.terminate_conditions == {"keywords": ["账单总额"]}
    # 动态多轮没有固定脚本。
    assert draft.query is None
    assert draft.turns == []
    assert not any("回退为单轮" in item for item in draft.warnings)


def test_hidden_context_source_is_inferred_from_evidence() -> None:
    draft = _draft(
        {
            "case_type": "DYNAMIC_MULTI",
            "user_goal": "反问后再释放 13800138000，拿到账单总额",
            "hidden_context": [
                {"key": "手机号", "content": "13800138000", "source": "LLM"},
                {"key": "账期", "content": "2026-10", "source": "LLM"},
            ],
        }
    )
    assert {item.key: item.source for item in draft.hidden_context} == {
        "手机号": "USER",
        "账期": "USER",
    }


def test_create_dynamic_multi_without_goal_falls_back_to_single() -> None:
    draft = _draft(
        {
            "case_type": "DYNAMIC_MULTI",
            "name": "缺目标",
            "query": "随便聊聊",
            "grading_mode": "LEGACY",
        }
    )
    assert draft.case_type == "SINGLE"
    assert draft.query == "随便聊聊"
    assert any("缺少用户目标" in item for item in draft.warnings)


def test_dynamic_multi_hidden_context_without_evidence_is_dropped() -> None:
    """隐藏信息里的号码必须是用户真的给过的，不能凭空编造。"""
    draft = _draft(
        {
            "case_type": "DYNAMIC_MULTI",
            "name": "编造隐藏信息",
            "user_goal": "通过反问拿到账单总额",
            "hidden_context": [
                {"key": "手机号", "content": "13900139000"},
                {"key": "账期", "content": "2026-10"},
            ],
            "grading_mode": "LEGACY",
        }
    )
    assert [item.key for item in draft.hidden_context] == ["账期"]
    assert any(item.field == "hidden_context.手机号" for item in draft.missing)
    assert any("隐藏信息「手机号」" in item for item in draft.warnings)


def test_create_dynamic_multi_clamps_max_turns() -> None:
    draft = _draft(
        {
            "case_type": "DYNAMIC_MULTI",
            "name": "轮次越界",
            "user_goal": "通过反问拿到账单总额",
            "max_turns": 99,
            "grading_mode": "LEGACY",
        }
    )
    assert draft.max_turns == 20
    assert any("最大轮次" in item for item in draft.warnings)


def test_dynamic_multi_edit_keeps_base_fields() -> None:
    base = CaseModel(
        code="BASE-DYN-001",
        name="动态多轮基线",
        case_type="DYNAMIC_MULTI",
        user_goal="通过反问拿到 13800138000 的账单总额",
        initial_query="我想核对话费",
        persona_description="说话简短",
        hidden_context=[
            {"key": "手机号", "content": "13800138000", "release_hint": "反问时释放"}
        ],
        max_turns=5,
        terminate_conditions={"keywords": ["账单总额"]},
        answer_envelope={"must": [], "may": [], "must_not": []},
    )
    draft = _draft(
        {"case_type": "DYNAMIC_MULTI", "name": "改写后的动态多轮", "max_turns": 8},
        request="最多 8 轮就结束",
        base=base,
    )
    assert draft.case_type == "DYNAMIC_MULTI"
    assert draft.name == "改写后的动态多轮"
    assert draft.max_turns == 8
    # 未声明的字段沿用基线，且来源标成 CASE。
    assert draft.user_goal == "通过反问拿到 13800138000 的账单总额"
    assert draft.initial_query == "我想核对话费"
    assert [(item.key, item.content, item.source) for item in draft.hidden_context] == [
        ("手机号", "13800138000", "CASE")
    ]
    assert draft.terminate_conditions == {"keywords": ["账单总额"]}
    assert draft.query is None and draft.turns == []


def test_dynamic_multi_edit_aligns_hidden_context_with_base() -> None:
    base = CaseModel(
        code="BASE-DYN-003",
        name="动态多轮基线",
        case_type="DYNAMIC_MULTI",
        user_goal="通过反问拿到 13800138000 的账单总额",
        hidden_context=[
            {"key": "phone", "content": "13800138000", "release_hint": "反问时释放"}
        ],
        max_turns=5,
        terminate_conditions={"keywords": ["账单总额"]},
        answer_envelope={"must": [], "may": [], "must_not": []},
    )
    draft = _draft(
        {
            "case_type": "DYNAMIC_MULTI",
            "hidden_context": [
                {"key": "手机号", "content": "13800138000", "source": "LLM"}
            ],
        },
        request="隐藏信息不变，仍然反问后才给号码",
        base=base,
    )
    # 取值与基线一致：沿用基线键名，来源标成 CASE，避免被误判为新编内容。
    assert [(item.key, item.content, item.source) for item in draft.hidden_context] == [
        ("phone", "13800138000", "CASE")
    ]


def test_missing_fields_are_deduplicated() -> None:
    draft = _draft(
        {
            "grading_mode": "ANSWER_CONTRACT",
            "missing": [
                {"field": "as_of", "label": "账期", "reason": "未给出账期"},
                {"field": "as_of", "label": "冻结账期", "reason": "契约模式必须冻结账期"},
            ],
        }
    )
    assert [item.field for item in draft.missing].count("as_of") == 1


def test_dynamic_multi_base_brief_includes_semantics() -> None:
    base = CaseModel(
        code="BASE-DYN-002",
        name="动态多轮基线",
        case_type="DYNAMIC_MULTI",
        user_goal="通过反问拿到账单总额",
        initial_query="我想核对话费",
        persona_description="说话简短",
        hidden_context=[{"key": "手机号", "content": "13800138000", "release_hint": "反问时释放"}],
        max_turns=6,
        terminate_conditions={"keywords": ["账单总额", "费用项数"]},
        answer_envelope={"must": [], "may": [], "must_not": []},
    )
    text = _case_brief(base)
    assert "case_type：DYNAMIC_MULTI" in text
    assert "user_goal：通过反问拿到账单总额" in text
    assert "initial_query：我想核对话费" in text
    assert "max_turns：6" in text
    assert "隐藏信息：手机号｜13800138000｜反问时释放" in text
    assert "终止关键词：账单总额、费用项数" in text


def test_static_multi_edit_keeps_base_turns_and_contract() -> None:
    base = CaseModel(
        code="BASE-MULTI-002",
        name="静态多轮基线",
        case_type="STATIC_MULTI",
        turns=[
            {"index": 1, "query": "查一下 13800138000 在 2026-10 的账单", "expected_answer": "总额 268.50"},
            {"index": 2, "query": "那明细呢？"},
        ],
        as_of="2026-10",
        grading_mode="ANSWER_CONTRACT",
        answer_envelope={
            "must": [
                {
                    "id": "must-1",
                    "kind": "scalar",
                    "label": "账单总额",
                    "value": 268.5,
                    "unit": "CNY",
                }
            ],
            "may": [],
            "must_not": [],
        },
    )
    draft = _draft(
        {"case_type": "STATIC_MULTI", "name": "改写后的静态多轮"},
        request="把第一轮问得更口语化",
        base=base,
    )
    assert draft.case_type == "STATIC_MULTI"
    assert [turn.query for turn in draft.turns] == [
        "查一下 13800138000 在 2026-10 的账单",
        "那明细呢？",
    ]
    # 模型未返回判分与事实时，沿用基线，避免静默清空契约语义。
    assert draft.grading_mode == "ANSWER_CONTRACT"
    assert draft.as_of == "2026-10"
    assert [fact.label for fact in draft.must] == ["账单总额"]
    assert draft.query is None


def test_static_multi_edit_normalizes_explicit_turns() -> None:
    base = CaseModel(
        code="BASE-MULTI-003",
        name="静态多轮基线",
        case_type="STATIC_MULTI",
        turns=[{"index": 1, "query": "第一轮"}],
    )
    draft = _draft(
        {
            "case_type": "STATIC_MULTI",
            "name": "改写后的静态多轮",
            "turns": [
                "第一轮：查一下账单",
                {"query": "第二轮：那明细呢？", "expected_answer": "3 笔", "notes": "追问"},
            ],
            "grading_mode": "LEGACY",
        },
        request="补一轮明细追问",
        base=base,
    )
    assert [turn.query for turn in draft.turns] == [
        "第一轮：查一下账单",
        "第二轮：那明细呢？",
    ]
    assert draft.turns[1].expected_answer == "3 笔"
    assert draft.turns[1].notes == "追问"


@pytest.mark.asyncio
async def test_edit_base_case_accepts_single_static_and_dynamic_multi() -> None:
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        single = CaseModel(code=f"AI-TEST-S-{suffix}", name="单轮", case_type="SINGLE")
        static_multi = CaseModel(
            code=f"AI-TEST-M-{suffix}",
            name="静态多轮",
            case_type="STATIC_MULTI",
            turns=[{"index": 1, "query": "查一下账单"}],
        )
        dynamic_multi = CaseModel(
            code=f"AI-TEST-D-{suffix}",
            name="动态多轮",
            case_type="DYNAMIC_MULTI",
            user_goal="通过追问拿到账单",
        )
        db.add_all([single, static_multi, dynamic_multi])
        await db.commit()
        try:
            base = await _load_base_case(
                db,
                CaseAIDraftRequest(request="改一下", mode="EDIT", base_case_id=single.id),
            )
            assert base is not None and base.code == single.code

            base = await _load_base_case(
                db,
                CaseAIDraftRequest(
                    request="改一下", mode="EDIT", base_case_id=static_multi.id
                ),
            )
            assert base is not None and base.code == static_multi.code

            base = await _load_base_case(
                db,
                CaseAIDraftRequest(
                    request="改一下", mode="EDIT", base_case_id=dynamic_multi.id
                ),
            )
            assert base is not None and base.code == dynamic_multi.code

            with pytest.raises(ValidationError, match="不存在"):
                await _load_base_case(
                    db,
                    CaseAIDraftRequest(request="改一下", mode="EDIT", base_case_id=-1),
                )
            with pytest.raises(ValidationError, match="base_case_id"):
                await _load_base_case(
                    db, CaseAIDraftRequest(request="改一下", mode="EDIT")
                )
        finally:
            await db.execute(
                delete(CaseModel).where(
                    CaseModel.id.in_([single.id, static_multi.id, dynamic_multi.id])
                )
            )
            await db.commit()


class _FakeLLM:
    """按调用顺序返回固定草稿，用于验证多候选去重与编号分配。"""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def chat(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))


def _stub_generate(monkeypatch: pytest.MonkeyPatch, payloads: list[dict[str, Any]]) -> _FakeLLM:
    fake = _FakeLLM(payloads)

    async def fake_model(*_: object, **__: object) -> SimpleNamespace:
        return SimpleNamespace(
            id=50, code="MOCK-AUTHOR", name="作者模型", effective_temperature=0.2
        )

    async def fake_calibers(*_: object, **__: object) -> list[object]:
        return []

    async def fake_capabilities(*_: object, **__: object) -> list[tuple[str, str]]:
        return []

    async def fake_similar(*_: object, **__: object) -> list[object]:
        return []

    async def fake_codes(*_: object, **__: object) -> list[str]:
        return ["AI-20261116-001", "AI-20261116-002", "AI-20261116-003"]

    async def fake_prompt(*_: object, **__: object) -> str:
        return "prompt"

    monkeypatch.setattr(service, "select_author_model", fake_model)
    monkeypatch.setattr(service, "_load_calibers", fake_calibers)
    monkeypatch.setattr(service, "_load_capabilities", fake_capabilities)
    monkeypatch.setattr(service, "_load_similar_cases", fake_similar)
    monkeypatch.setattr(service, "_next_ai_codes", fake_codes)
    monkeypatch.setattr(service, "_build_prompt", fake_prompt)
    monkeypatch.setattr(service, "LLMClient", lambda _ctx: fake)
    monkeypatch.setattr(
        service, "ModelContext", SimpleNamespace(from_orm=lambda _model: None)
    )
    return fake


@pytest.mark.asyncio
async def test_multi_drafts_dedupe_and_numbering(monkeypatch: pytest.MonkeyPatch) -> None:
    duplicate = {"name": "账单校验", "query": "查账单", "grading_mode": "LEGACY"}
    other = {"name": "账单校验（边界）", "query": "请问账单是多少", "grading_mode": "LEGACY"}
    fake = _stub_generate(monkeypatch, [duplicate, duplicate, other])

    response = await service.generate_case_drafts(
        None,  # type: ignore[arg-type]  # 所有 DB 访问都已被 stub
        CaseAIDraftRequest(request="查账单", count=3),
    )

    assert [draft.name for draft in response.drafts] == ["账单校验", "账单校验（边界）"]
    assert [draft.suggested_code for draft in response.drafts] == [
        "AI-20261116-001",
        "AI-20261116-002",
    ]
    assert response.warnings and "只生成 2 条" in response.warnings[0]
    assert fake.calls == 4


@pytest.mark.asyncio
async def test_edit_drafts_have_no_suggested_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_generate(monkeypatch, [{"name": "改写后的用例", "grading_mode": "LEGACY"}])

    async def fake_base(*_: object, **__: object) -> CaseModel:
        base = CaseModel(
            code="CASE-BILL-001",
            name="基线用例",
            case_type="SINGLE",
            query="查一下账单",
            answer_envelope={"must": [], "may": [], "must_not": []},
        )
        base.id = 42
        return base

    monkeypatch.setattr(service, "_load_base_case", fake_base)
    monkeypatch.setattr(service, "_next_ai_codes", _should_not_be_called)

    response = await service.generate_case_drafts(
        None,  # type: ignore[arg-type]
        CaseAIDraftRequest(request="改个名字", mode="EDIT", base_case_id=42, count=1),
    )

    assert response.mode == "EDIT"
    assert response.base_case_id == 42
    assert response.base_case_code == "CASE-BILL-001"
    assert response.drafts[0].suggested_code is None


async def _should_not_be_called(*_: object, **__: object) -> list[str]:
    raise AssertionError("EDIT 模式不应分配建议编号")


# ---------------------------------------------------------------------- #
# 账期依据：单位数月与来源标注
# ---------------------------------------------------------------------- #
def test_date_evidence_accepts_single_digit_month() -> None:
    assert value_in_text("2026-03", "2026 年 3 月账单", "date") is True
    assert value_in_text("2026-03", "2026/3", "date") is True
    assert value_in_text("2026-03", "2026-03-01", "date") is True
    assert value_in_text("2026-04", "2026 年 3 月账单", "date") is False


def test_as_of_single_digit_month_is_kept() -> None:
    draft = _normalize_draft(
        {
            "name": "3 月账单总额",
            "query": "帮我查一下 2026 年 3 月账单总金额",
            "grading_mode": "ANSWER_CONTRACT",
            "as_of": "2026-03",
            "field_sources": {"as_of": "USER"},
            "must": [
                {
                    "label": "账单总额",
                    "kind": "scalar",
                    "value": 128000.5,
                    "source": "USER",
                }
            ],
        },
        user_request="新建一条单轮用例：询问 2026 年 3 月账单总金额，期望回答 128000.50 元。",
        caliber_text="",
        case_text="",
        suggested_code="AI-20261116-004",
    )
    assert draft.as_of == "2026-03"
    assert not any(item.field == "as_of" for item in draft.missing)
    assert draft.field_sources.get("as_of") == "USER"


def test_unbacked_as_of_drops_field_source() -> None:
    draft = _normalize_draft(
        {
            "name": "无依据账期",
            "grading_mode": "LEGACY",
            "as_of": "2028-05",
            "field_sources": {"as_of": "USER"},
        },
        user_request="查一下账单总额",
        caliber_text="",
        case_text="",
        suggested_code="AI-20261116-005",
    )
    assert draft.as_of is None
    assert any(item.field == "as_of" for item in draft.missing)
    assert "as_of" not in draft.field_sources


# ---------------------------------------------------------------------- #
# 全字段覆盖：请求变量 / 规则 / 契约 / 计划 / 配置
# ---------------------------------------------------------------------- #
def test_request_variables_keep_backed_values() -> None:
    draft = _draft(
        {
            "name": "请求变量",
            "grading_mode": "LEGACY",
            "request_variables": {
                "phone": "13800138000",
                "billing_period": "2026-10",
                "channel": "app",
            },
        }
    )
    assert draft.request_variables == {
        "phone": "13800138000",
        "billing_period": "2026-10",
        "channel": "app",
    }
    assert not any(item.field.startswith("request_variables.") for item in draft.missing)


def test_request_variables_unbacked_number_moves_to_missing() -> None:
    draft = _draft(
        {
            "name": "请求变量",
            "grading_mode": "LEGACY",
            "request_variables": {"phone": "13900139000"},
        }
    )
    assert draft.request_variables is None
    assert any(item.field == "request_variables.phone" for item in draft.missing)


def test_request_variables_drop_reserved_and_invalid_keys() -> None:
    draft = _draft(
        {
            "name": "请求变量",
            "grading_mode": "LEGACY",
            "request_variables": {"query": "x", "2bad": "y", "ok_key": "value"},
        }
    )
    assert draft.request_variables == {"ok_key": "value"}
    assert any(item.field == "request_variables.query" for item in draft.missing)
    assert any(item.field == "request_variables.2bad" for item in draft.missing)


def test_expected_rules_require_evidence() -> None:
    draft = _draft(
        {
            "name": "4 维规则",
            "grading_mode": "LEGACY",
            "as_of": "2026-10",
            "expected_rules": {
                "amounts": [{"label": "账单总额", "value": 268.5}],
                "counts": [{"label": "编造项数", "value": 99}],
                "dates": ["2026-10", "2028-05"],
                "must_contain": ["账单"],
            },
        }
    )
    rules = draft.expected_rules
    assert rules is not None
    assert [item.value for item in rules.amounts] == [268.5]
    assert rules.counts == []
    assert rules.dates == ["2026-10"]
    assert rules.must_contain == ["账单"]
    assert any(item.field == "expected_rules.counts.编造项数" for item in draft.missing)
    assert any(item.field == "expected_rules.dates.2028-05" for item in draft.missing)


def test_plan_spec_capabilities_filtered_by_whitelist() -> None:
    draft = _normalize_draft(
        {
            "name": "取数计划",
            "grading_mode": "LEGACY",
            "plan_spec": {
                "required_capabilities": ["bill_query", "made_up"],
                "max_tool_calls": 2,
            },
        },
        user_request="查一下账单总额",
        caliber_text="",
        case_text="",
        suggested_code="AI-20261116-006",
        capability_codes={"bill_query"},
    )
    assert draft.plan_spec is not None
    assert draft.plan_spec.required_capabilities == ["bill_query"]
    assert draft.plan_spec.max_tool_calls == 2
    assert any("made_up" in warning for warning in draft.warnings)


def test_response_contract_ignored_for_clear_input() -> None:
    draft = _draft(
        {
            "name": "契约",
            "grading_mode": "LEGACY",
            "input_type": "CLEAR",
            "response_contract": {"allowed": ["clarify", "refuse"]},
        }
    )
    assert draft.response_contract is None


def test_response_contract_kept_for_underspecified_input() -> None:
    draft = _draft(
        {
            "name": "契约",
            "grading_mode": "LEGACY",
            "input_type": "UNDERSPECIFIED",
            "response_contract": {
                "allowed": ["clarify", "refuse"],
                "clarify": {"must_ask_slot": ["time_range"]},
                "refuse": {"must_reason": True},
            },
        }
    )
    assert draft.response_contract is not None
    assert draft.response_contract.allowed == ["clarify", "refuse"]
    assert draft.response_contract.clarify is not None
    assert draft.response_contract.clarify.must_ask_slot == ["time_range"]


def test_single_behavior_contract_warns_and_marks_missing() -> None:
    draft = _draft(
        {
            "name": "契约",
            "grading_mode": "LEGACY",
            "input_type": "UNDERSPECIFIED",
            "response_contract": {"allowed": ["clarify"], "clarify": {}},
        }
    )
    assert draft.response_contract is not None
    assert any(item.field == "response_contract" for item in draft.missing)
    assert any("一种可接受行为" in warning for warning in draft.warnings)


def test_turn_expected_rules_are_normalized() -> None:
    draft = _draft(
        {
            "name": "多轮规则",
            "grading_mode": "LEGACY",
            "case_type": "STATIC_MULTI",
            "turns": [
                {"query": "第一轮 2026-10 账单", "expected_rules": {"dates": ["2026-10"]}},
                {
                    "query": "第二轮 明细",
                    "expected_rules": {"amounts": [{"label": "编造金额", "value": 999}]},
                },
            ],
        }
    )
    assert draft.case_type == "STATIC_MULTI"
    assert draft.turns[0].expected_rules is not None
    assert draft.turns[0].expected_rules.dates == ["2026-10"]
    assert draft.turns[1].expected_rules is None
    assert any(
        item.field.startswith("turns.2.expected_rules.amounts") for item in draft.missing
    )


def test_config_fields_are_normalized() -> None:
    draft = _draft(
        {
            "name": "配置",
            "grading_mode": "LEGACY",
            "extras_policy": "strict",
            "judge_strategy": "VETO",
            "pass_threshold": 85,
            "fail_fast": True,
            "session_expected": {"turns": 2},
            "change_summary": "改成更严格的阈值",
        }
    )
    assert draft.extras_policy == "strict"
    assert draft.judge_strategy == "VETO"
    assert draft.pass_threshold == 85
    assert draft.fail_fast is True
    assert draft.session_expected == {"turns": 2}
    # 新建模式不保留变更说明。
    assert draft.change_summary is None


def test_invalid_pass_threshold_is_dropped() -> None:
    draft = _draft(
        {
            "name": "阈值",
            "grading_mode": "LEGACY",
            "pass_threshold": 200,
        }
    )
    assert draft.pass_threshold is None
    assert any("通过分数线" in warning for warning in draft.warnings)

def test_contract_without_input_type_infers_underspecified() -> None:
    draft = _draft(
        {
            "name": "契约",
            "grading_mode": "LEGACY",
            "response_contract": {"allowed": ["clarify", "refuse"], "clarify": {}},
        }
    )
    assert draft.input_type == "UNDERSPECIFIED"
    assert draft.response_contract is not None
    assert any("未标注输入形态" in warning for warning in draft.warnings)


def test_draft_signature_includes_extension_fields() -> None:
    left = _draft(
        {
            "name": "同一核心",
            "query": "查账单",
            "grading_mode": "LEGACY",
            "request_variables": {"phone": "13800138000"},
        }
    )
    right = _draft(
        {
            "name": "同一核心",
            "query": "查账单",
            "grading_mode": "LEGACY",
            "request_variables": {"billing_period": "2026-10"},
        }
    )
    assert service._draft_signature(left) != service._draft_signature(right)
