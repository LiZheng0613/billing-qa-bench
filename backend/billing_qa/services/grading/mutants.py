"""判分器自检：内置变异集与运行器（供测试与运营后台复用）。"""

from __future__ import annotations

from typing import Any

from billing_qa.services.grading.answer_grader import grade_answer

DEFAULT_SNAPSHOT: dict[str, Any] = {
    "grading_mode": "ANSWER_CONTRACT",
    "input_type": "CLEAR",
    "as_of": "2026-10",
    "answer_envelope": {
        "must": [
            {"id": "amount", "kind": "scalar", "value": 123.45, "unit": "CNY"},
            {"id": "due", "kind": "date", "value": "2026-10-31"},
            {"id": "package", "kind": "set", "values": ["套餐A", "套餐B"]},
        ],
        "must_not": [{"id": "banned", "kind": "text", "value": "系统错误"}],
        "extras_policy": "strict",
    },
}

DEFAULT_CORRECT = "本期账单金额 123.45 元，到期日 2026-10-31，当前订购套餐A 与套餐B。"

#: 对同一份正确用例做定点变异，全部必须被识别为不通过。
MUTANTS: dict[str, str] = {
    "金额抄错": "本期账单金额 123.54 元，到期日 2026-10-31，当前订购套餐A 与套餐B。",
    "日期抄错": "本期账单金额 123.45 元，到期日 2026-11-01，当前订购套餐A 与套餐B。",
    "漏掉套餐": "本期账单金额 123.45 元，到期日 2026-10-31，当前订购套餐A。",
    "编造金额": "本期账单金额 123.45 元，到期日 2026-10-31，另补偿 88 元，当前订购套餐A 与套餐B。",
    "出现禁用结论": "本期账单金额 123.45 元，到期日 2026-10-31，"
    "当前订购套餐A 与套餐B，系统错误已修复。",
}


async def _grade(snapshot: dict[str, Any], answer: str):
    return await grade_answer(
        snapshot=snapshot,
        turn_records=[{"query": "本期账单情况？", "final_answer": answer}],
    )


async def run_mutants(
    *,
    snapshot: dict[str, Any] | None = None,
    correct_answer: str | None = None,
    mutants: dict[str, str] | None = None,
) -> dict[str, Any]:
    """运行基线 + 变异集，返回可通过 ``GraderEval`` 持久化的汇总。"""
    target_snapshot = snapshot or DEFAULT_SNAPSHOT
    correct = correct_answer or DEFAULT_CORRECT
    cases = mutants or MUTANTS

    baseline = await _grade(target_snapshot, correct)
    false_kills = 0 if baseline.passed else 1

    details: list[dict[str, Any]] = []
    detected = 0
    for name, answer in cases.items():
        grading = await _grade(target_snapshot, answer)
        hit = not grading.passed
        detected += int(hit)
        details.append(
            {
                "mutant": name,
                "detected": hit,
                "gates": grading.gates,
                "root_cause": grading.root_cause,
                "notes": grading.notes,
            }
        )

    return {
        "total": len(cases),
        "detected": detected,
        "false_kills": false_kills,
        "baseline_passed": baseline.passed,
        "grader_version": baseline.grader_version,
        "details": {
            "baseline": {
                "passed": baseline.passed,
                "gates": baseline.gates,
                "notes": baseline.notes,
            },
            "mutants": details,
        },
    }
