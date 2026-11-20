"""用例候选池：创建、分诊、拒绝与转正。"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.cases import (
    _add_revision,
    _validate_references,
    _validate_strategy,
)
from billing_qa.core.authz import require_module_access
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.search import keyword_condition
from billing_qa.core.security import get_current_operator
from billing_qa.db.session import get_db
from billing_qa.models.candidate import CaseCandidate
from billing_qa.models.enums import RevisionChangeType
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case import CaseCreate
from billing_qa.schemas.common import Page
from billing_qa.schemas.ops import (
    CandidateCreate,
    CandidateOut,
    CandidatePromoteOut,
    CandidatePromoteRequest,
    CandidateUpdate,
)

router = APIRouter(
    prefix="/candidates",
    tags=["用例候选池"],
    dependencies=[
        Depends(require_module_access("candidate:read", ("candidate:triage", "candidate:promote")))
    ],
)


def _dedupe_hash(raw_question: str, context: dict | None) -> str:
    payload = json.dumps(
        {
            "question": " ".join((raw_question or "").split()).lower(),
            "context": context or {},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _get_candidate(db: AsyncSession, candidate_id: int) -> CaseCandidate:
    candidate = await db.get(CaseCandidate, candidate_id)
    if candidate is None:
        raise NotFoundError("候选用例不存在")
    return candidate


@router.get("", response_model=Page[CandidateOut], summary="候选池列表")
async def list_candidates(
    status: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CandidateOut]:
    conditions = []
    if status:
        conditions.append(CaseCandidate.status == status)
    if source:
        conditions.append(CaseCandidate.source == source)
    keyword_filter = keyword_condition(
        (CaseCandidate.raw_question, CaseCandidate.note), keyword
    )
    if keyword_filter is not None:
        conditions.append(keyword_filter)
    total = (
        await db.execute(select(func.count()).select_from(CaseCandidate).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(CaseCandidate)
            .where(*conditions)
            .order_by(CaseCandidate.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[CandidateOut](
        items=[CandidateOut.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=CandidateOut, summary="登记候选用例")
async def create_candidate(
    payload: CandidateCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CandidateOut:
    dedupe = payload.dedupe_hash or _dedupe_hash(payload.raw_question, payload.context)
    exists = (
        await db.execute(
            select(CaseCandidate.id).where(CaseCandidate.dedupe_hash == dedupe)
        )
    ).scalar_one_or_none()
    if exists:
        raise ConflictError("相同问题与上下文的候选已存在")
    candidate = CaseCandidate(
        **payload.model_dump(exclude={"dedupe_hash"}),
        dedupe_hash=dedupe,
        reviewed_by=operator if payload.draft else None,
    )
    db.add(candidate)
    await db.commit()
    await db.refresh(candidate)
    return CandidateOut.model_validate(candidate)


@router.get("/{candidate_id}", response_model=CandidateOut, summary="候选详情")
async def get_candidate(
    candidate_id: int, db: AsyncSession = Depends(get_db)
) -> CandidateOut:
    return CandidateOut.model_validate(await _get_candidate(db, candidate_id))


@router.patch("/{candidate_id}", response_model=CandidateOut, summary="分诊候选")
async def update_candidate(
    candidate_id: int,
    payload: CandidateUpdate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CandidateOut:
    candidate = await _get_candidate(db, candidate_id)
    updates = payload.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] == "PROMOTED":
        raise ValidationError("转正请使用 /candidates/{id}/promote 接口")
    for key, value in updates.items():
        setattr(candidate, key, value)
    candidate.reviewed_by = operator
    await db.commit()
    await db.refresh(candidate)
    return CandidateOut.model_validate(candidate)


@router.post("/{candidate_id}/reject", response_model=CandidateOut, summary="拒绝候选")
async def reject_candidate(
    candidate_id: int,
    note: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CandidateOut:
    candidate = await _get_candidate(db, candidate_id)
    candidate.status = "REJECTED"
    candidate.note = note or candidate.note
    candidate.reviewed_by = operator
    await db.commit()
    await db.refresh(candidate)
    return CandidateOut.model_validate(candidate)


@router.post(
    "/{candidate_id}/promote",
    response_model=CandidatePromoteOut,
    summary="候选转正为测试用例",
)
async def promote_candidate(
    candidate_id: int,
    payload: CandidatePromoteRequest,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> CandidatePromoteOut:
    candidate = await _get_candidate(db, candidate_id)
    if candidate.status == "PROMOTED" and candidate.promoted_case_id:
        raise ConflictError("候选已转正，不能重复转正")
    if candidate.status == "REJECTED":
        raise ValidationError("已拒绝的候选不能转正")

    data = dict(candidate.draft or {})
    data.update(payload.case or {})
    data.setdefault("case_type", "SINGLE")
    data.setdefault("name", candidate.raw_question[:200])
    data.setdefault("description", candidate.raw_question)
    if data["case_type"] == "SINGLE":
        data.setdefault("query", candidate.raw_question)
    if payload.code:
        data["code"] = payload.code
    if payload.name:
        data["name"] = payload.name
    data.setdefault("environment_id", candidate.environment_id)
    if not data.get("code"):
        data["code"] = f"CAND-{candidate.id:05d}"
    if not data.get("name"):
        raise ValidationError("转正用例必须提供名称")

    exists = (
        await db.execute(select(TestCase.id).where(TestCase.code == data["code"]))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"用例编号 {data['code']} 已存在")

    validated = CaseCreate(**data)
    case_data = validated.model_dump()
    await _validate_references(db, case_data)
    _validate_strategy(case_data)
    case = TestCase(**case_data)
    case.current_version = 1
    case.created_by = operator
    case.updated_by = operator
    db.add(case)
    await db.flush()
    await _add_revision(db, case, RevisionChangeType.CREATE, operator, "候选转正创建")
    candidate.status = "PROMOTED"
    candidate.promoted_case_id = case.id
    candidate.reviewed_by = operator
    await db.commit()
    await db.refresh(case)
    return CandidatePromoteOut(
        candidate_id=candidate.id, case_id=case.id, case_code=case.code
    )
