"""AI 用例编写：请求 / 响应 Schema。

只承载语义字段，禁止出现模板 ID、模型 ID、环境 ID 等外键；
所有生成结果默认 ``verification_status=PENDING``，不写库，必须人工确认后应用。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from billing_qa.models.enums import CaseType, GradingMode, InputType, JudgeStrategy
from billing_qa.schemas.case import FactAtom, PlanSpec, RuleConfig

DraftSource = Literal["USER", "ASSET", "CASE", "LLM", "NONE"]


class CaseAIDraftRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000, description="用户的自然语言描述")
    mode: Literal["CREATE", "EDIT"] = Field(
        default="CREATE", description="CREATE=新建；EDIT=基于既有单轮用例改写"
    )
    count: int = Field(default=1, ge=1, le=5, description="期望生成的草稿数量")
    base_case_id: int | None = Field(default=None, description="EDIT 模式下的改写基线用例")
    environment_id: int | None = Field(default=None, description="仅作为语义上下文，不写入草稿")


class DraftFact(FactAtom):
    """草稿事实：在 FactAtom 基础上附加依据来源。"""

    section: Literal["must", "may", "must_not"] = "must"
    source: DraftSource = "LLM"
    source_ref: str | None = Field(default=None, max_length=128)


class DraftMissing(BaseModel):
    """缺少权威依据、必须由人工补充的字段。"""

    field: str = Field(max_length=128)
    label: str = Field(max_length=200)
    reason: str = Field(max_length=512)


class DraftTurn(BaseModel):
    """静态多轮草稿中的一轮脚本。"""

    query: str = Field(max_length=2000)
    expected_answer: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=1000)
    expected_rules: RuleConfig | None = Field(
        default=None, description="该轮的 4 维评分规则（仅 LEGACY 模式使用）"
    )


class DraftClarifySpec(BaseModel):
    """草稿版澄清契约：不做「至少两种行为」的强校验，保存前再人工补齐。"""

    must_ask_slot: list[str] = Field(default_factory=list)
    must_not_assert: list[str] = Field(default_factory=list)


class DraftAssumptionScenario(BaseModel):
    assumption: str = Field(default="", max_length=512)
    must: dict[str, Any] = Field(default_factory=dict)


class DraftAssumptionSpec(BaseModel):
    must_state_assumption: bool = True
    scenarios: list[DraftAssumptionScenario] = Field(default_factory=list)


class DraftPartialSpec(BaseModel):
    must_disclose: bool = True
    required_facts: list[str] = Field(default_factory=list)


class DraftRefuseSpec(BaseModel):
    must_reason: bool = True


class DraftResponseContract(BaseModel):
    """草稿版响应契约：与 ResponseContract 形状一致，但允许只给出部分行为。"""

    allowed: list[Literal["clarify", "answer_with_assumption", "partial", "refuse"]] = (
        Field(default_factory=list)
    )
    clarify: DraftClarifySpec | None = None
    answer_with_assumption: DraftAssumptionSpec | None = None
    partial: DraftPartialSpec | None = None
    refuse: DraftRefuseSpec | None = None


class DraftHiddenContext(BaseModel):
    """动态多轮草稿中的一条隐藏信息（客服反问后才释放）。"""

    key: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=2000)
    release_hint: str | None = Field(default=None, max_length=512)
    source: DraftSource = "LLM"


class CaseAIDraft(BaseModel):
    """一条可回填到抽屉表单的语义草稿。"""

    case_type: CaseType = CaseType.SINGLE
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    priority: str | None = Field(default=None, max_length=8)
    tags: list[str] = Field(default_factory=list)
    grading_mode: GradingMode | None = None
    input_type: InputType | None = None
    as_of: str | None = None
    query: str | None = None
    expected_answer: str | None = None
    turns: list[DraftTurn] = Field(default_factory=list)

    # ---- 动态多轮 ----
    user_goal: str | None = Field(default=None, max_length=2000)
    initial_query: str | None = Field(default=None, max_length=2000)
    persona_description: str | None = Field(default=None, max_length=2000)
    hidden_context: list[DraftHiddenContext] = Field(default_factory=list)
    max_turns: int | None = Field(default=None, ge=1, le=20)
    terminate_conditions: dict[str, Any] | None = None

    must: list[DraftFact] = Field(default_factory=list)
    may: list[DraftFact] = Field(default_factory=list)
    must_not: list[DraftFact] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)

    # ---- 全字段覆盖：请求变量 / 4 维规则 / 会话 / 契约 / 计划 ----
    request_variables: dict[str, Any] | None = Field(
        default=None, description="渲染进请求头/请求体的用例级变量"
    )
    expected_rules: RuleConfig | None = Field(
        default=None, description="单轮/动态多轮的 4 维评分规则（LEGACY 模式）"
    )
    session_expected: dict[str, Any] | None = Field(
        default=None, description="静态多轮的会话级预期"
    )
    fail_fast: bool | None = Field(
        default=None, description="静态多轮是否任一轮失败即停止"
    )
    response_contract: DraftResponseContract | None = Field(
        default=None, description="非清晰输入的响应契约（草稿允许只给部分行为）"
    )
    plan_spec: PlanSpec | None = Field(default=None, description="取数计划（仅诊断）")
    extras_policy: Literal["strict", "lenient"] | None = Field(
        default=None, description="答案包络策略"
    )
    judge_strategy: JudgeStrategy | None = Field(
        default=None, description="裁判聚合策略（仅用户明确要求时输出）"
    )
    pass_threshold: float | None = Field(
        default=None, ge=0, le=100, description="通过分数线（仅用户明确要求时输出）"
    )
    change_summary: str | None = Field(
        default=None, max_length=512, description="EDIT 模式本次改动摘要"
    )

    risk_level: Literal["LOW", "HIGH"] = "LOW"
    confidence: float = Field(default=0.5, ge=0, le=1)
    field_sources: dict[str, DraftSource] = Field(default_factory=dict)
    missing: list[DraftMissing] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_code: str | None = Field(default=None, max_length=64)


class CaseAIDraftContext(BaseModel):
    """本次生成实际参考的资产规模，用于前端提示。"""

    calibers: int = 0
    similar_cases: int = 0


class CaseAIDraftResponse(BaseModel):
    drafts: list[CaseAIDraft] = Field(default_factory=list)
    mode: Literal["CREATE", "EDIT"] = "CREATE"
    base_case_id: int | None = None
    base_case_code: str | None = None
    model_id: int | None = None
    model_code: str | None = None
    model_name: str | None = None
    elapsed_ms: int = 0
    context: CaseAIDraftContext = Field(default_factory=CaseAIDraftContext)
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "CaseAIDraft",
    "CaseAIDraftContext",
    "CaseAIDraftRequest",
    "CaseAIDraftResponse",
    "DraftAssumptionScenario",
    "DraftAssumptionSpec",
    "DraftClarifySpec",
    "DraftFact",
    "DraftHiddenContext",
    "DraftMissing",
    "DraftPartialSpec",
    "DraftRefuseSpec",
    "DraftResponseContract",
    "DraftSource",
    "DraftTurn",
]
