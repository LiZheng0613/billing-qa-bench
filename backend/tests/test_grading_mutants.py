"""判分器自检：对同一份正确用例做定点变异，必须全部被识别。"""

from __future__ import annotations

import pytest

from billing_qa.services.grading.answer_grader import grade_answer
from billing_qa.services.grading.mutants import (
    DEFAULT_CORRECT,
    DEFAULT_SNAPSHOT,
    MUTANTS,
    run_mutants,
)


async def _grade(answer: str):
    return await grade_answer(
        snapshot=DEFAULT_SNAPSHOT,
        turn_records=[{"query": "本期账单情况？", "final_answer": answer}],
    )


@pytest.mark.asyncio
async def test_correct_answer_passes() -> None:
    grading = await _grade(DEFAULT_CORRECT)
    assert grading.passed, grading.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,answer", sorted(MUTANTS.items()))
async def test_mutant_is_detected(name: str, answer: str) -> None:
    grading = await _grade(answer)
    assert not grading.passed, f"变异「{name}」未被识别"


@pytest.mark.asyncio
async def test_run_mutants_summary() -> None:
    result = await run_mutants()
    assert result["total"] == len(MUTANTS)
    assert result["detected"] == len(MUTANTS)
    assert result["false_kills"] == 0
