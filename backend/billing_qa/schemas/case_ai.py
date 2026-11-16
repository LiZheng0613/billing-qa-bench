"""AI 用例编写：请求 / 响应 Schema。

只承载语义字段，禁止出现模板 ID、模型 ID、环境 ID 等外键；
所有生成结果默认 ``verification_status=PENDING``，不写库，必须人工确认后应用。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from billing_qa.models.enums import CaseType, GradingMode, InputType
from billing_qa.schemas.case import FactAtom

DraftSource = Literal["USER", "ASSET", "CASE", "LLM", "NONE"]


class CaseAIDraftRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000, description="用户的自然语言描述")
    mode: Literal["CREATE", "EDIT"] = Field(
        default="CREATE", description="CREATE=新建；EDIT=基于既有单轮用例改写"
    )
    count: int = Field(default=1, ge=1, le=5, description="期望生成的草稿数量")
    base_case_id: int | None = Field(default=None, description="EDIT 模式下的改写基线用例")
    model_id: int | None = Field(default=None, description="指定编写模型；缺省用 CASE_AUTHOR / JUDGE")
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

    must: list[DraftFact] = Field(default_factory=list)
    may: list[DraftFact] = Field(default_factory=list)
    must_not: list[DraftFact] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)

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
    "DraftFact",
    "DraftMissing",
    "DraftSource",
    "DraftTurn",
]
