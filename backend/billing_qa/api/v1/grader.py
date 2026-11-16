"""判分器自检：变异集运行与历史结果查询。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.security import get_current_operator
from billing_qa.db.session import get_db
from billing_qa.models.candidate import GraderEval
from billing_qa.schemas.common import Page
from billing_qa.schemas.ops import GraderEvalOut, GraderEvalRunRequest
from billing_qa.services.grading.mutants import MUTANTS, run_mutants

router = APIRouter(
    prefix="/grader", tags=["判分器自检"], dependencies=[Depends(get_current_operator)]
)


@router.get("/mutants", summary="内置变异集")
async def list_mutants() -> dict[str, list[str]]:
    return {"mutants": sorted(MUTANTS)}


@router.post("/evals/run", response_model=GraderEvalOut, summary="运行判分器自检")
async def run_grader_eval(
    payload: GraderEvalRunRequest,
    db: AsyncSession = Depends(get_db),
) -> GraderEvalOut:
    result = await run_mutants(
        snapshot=payload.snapshot,
        correct_answer=payload.correct_answer,
    )
    record = GraderEval(
        dataset_version=payload.dataset_version or "builtin",
        grader_version=str(result["grader_version"]),
        mutant_kind="BUILTIN",
        total=int(result["total"]),
        detected=int(result["detected"]),
        false_kills=int(result["false_kills"]),
        details=result["details"],
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return GraderEvalOut.model_validate(record)


@router.get("/evals", response_model=Page[GraderEvalOut], summary="判分器自检历史")
async def list_grader_evals(
    grader_version: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[GraderEvalOut]:
    conditions = []
    if grader_version:
        conditions.append(GraderEval.grader_version == grader_version)
    total = (
        await db.execute(select(func.count()).select_from(GraderEval).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(GraderEval)
            .where(*conditions)
            .order_by(GraderEval.run_at.desc(), GraderEval.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[GraderEvalOut](
        items=[GraderEvalOut.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )
