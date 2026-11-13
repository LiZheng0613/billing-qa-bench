"""混合裁决：分数规整、红线策略与多裁判聚合单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from billing_qa.services.engine.judge import (
    DIMENSION_MAX,
    JudgeOutcome,
    coerce_judgement,
    run_judges,
)


def _data(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pass": True,
        "total_score": 92,
        "dimension_scores": {
            "data_accuracy": 40,
            "logic_consistency": 28,
            "goal_completion": 16,
            "tone_clarity": 8,
        },
        "error_tags": [],
        "reason": "金额与底层取数一致",
    }
    base.update(overrides)
    return base


def test_normal_judgement() -> None:
    passed, total, dims, tags, reason, notes = coerce_judgement(
        _data(), termination_status="GOAL_ACHIEVED", threshold=60
    )
    assert passed is True
    assert total == 92
    assert dims == {"data_accuracy": 40, "logic_consistency": 28, "goal_completion": 16, "tone_clarity": 8}
    assert tags == []
    assert reason and notes == []


def test_data_accuracy_red_line() -> None:
    data = _data(
        **{"pass": True},
        total_score=70,
        dimension_scores={
            "data_accuracy": 0,
            "logic_consistency": 30,
            "goal_completion": 30,
            "tone_clarity": 10,
        },
    )
    passed, total, dims, tags, _, notes = coerce_judgement(
        data, termination_status="COMPLETED", threshold=60
    )
    assert passed is False
    assert dims["data_accuracy"] == 0
    assert "数值错误/数据幻觉" in tags
    assert any("红线" in note for note in notes)


def test_max_turns_red_line_zeroes_goal() -> None:
    passed, total, dims, tags, _, notes = coerce_judgement(
        _data(total_score=80), termination_status="MAX_TURNS_EXCEEDED", threshold=60
    )
    assert passed is False
    assert dims["goal_completion"] == 0
    assert total == round(sum(dims.values()), 2)
    assert "超出最大轮次" in tags
    assert any("MAX_TURNS_EXCEEDED" in note for note in notes)


def test_repetition_abort_red_line() -> None:
    passed, _, _, tags, _, _ = coerce_judgement(
        _data(), termination_status="REPETITION_ABORT", threshold=60
    )
    assert passed is False
    assert "复读机熔断" in tags


def test_below_threshold_fails() -> None:
    passed, total, _, _, _, notes = coerce_judgement(
        _data(
            **{"pass": True},
            total_score=55,
            dimension_scores={
                "data_accuracy": 20,
                "logic_consistency": 15,
                "goal_completion": 12,
                "tone_clarity": 8,
            },
        ),
        termination_status="COMPLETED",
        threshold=60,
    )
    assert passed is False
    assert total == 55
    assert any("低于通过线" in note for note in notes)


def test_total_score_corrected_by_dimensions() -> None:
    data = _data(
        total_score=10,
        dimension_scores={
            "data_accuracy": 40,
            "logic_consistency": 30,
            "goal_completion": 20,
            "tone_clarity": 10,
        },
    )
    passed, total, _, _, _, notes = coerce_judgement(
        data, termination_status="COMPLETED", threshold=60
    )
    assert passed is True
    assert total == 100
    assert any("校正" in note for note in notes)


def test_dimensions_scaled_when_missing() -> None:
    data = _data(total_score=50, dimension_scores={})
    passed, total, dims, _, _, notes = coerce_judgement(
        data, termination_status="COMPLETED", threshold=60
    )
    assert passed is False
    assert total == 50
    assert dims == {name: round(maximum * 0.5, 2) for name, maximum in DIMENSION_MAX.items()}
    assert any("折算" in note for note in notes)


def _outcome(score: float, passed: bool, data_accuracy: float = 40) -> JudgeOutcome:
    return JudgeOutcome(
        model_id=1,
        model_name="judge-1",
        pass_flag=passed,
        total_score=score,
        dimension_scores={
            "data_accuracy": data_accuracy,
            "logic_consistency": 25,
            "goal_completion": 15,
            "tone_clarity": 8,
        },
        error_tags=[],
        reason="ok",
        raw_response={},
        judge_prompt="p",
    )


def test_run_judges_avg_strategy(monkeypatch: Any) -> None:
    async def fake_judge_one(model: Any, prompt: str, **kwargs: Any) -> JudgeOutcome:
        score = 90 if model.name == "A" else 70
        return _outcome(score, True)

    monkeypatch.setattr("billing_qa.services.engine.judge._judge_one", fake_judge_one)
    models = [
        SimpleNamespace(id=1, name="A", model_name="m1"),
        SimpleNamespace(id=2, name="B", model_name="m2"),
    ]
    aggregate, outcomes = asyncio.run(
        run_judges(
            models=models,  # type: ignore[arg-type]
            prompt_template="评分 {{case_type}}",
            variables={"case_type": "SINGLE"},
            strategy="AVG",
            threshold=60,
            termination_status="COMPLETED",
        )
    )
    assert len(outcomes) == 2
    assert aggregate["total_score"] == 80
    assert aggregate["pass"] is True
    assert aggregate["judge_count"] == 2
