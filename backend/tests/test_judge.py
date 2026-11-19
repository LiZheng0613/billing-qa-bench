"""混合裁决：分数规整、红线策略与多裁判聚合单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Self

import pytest

from billing_qa.services.engine.judge import (
    DIMENSION_MAX,
    JudgeOutcome,
    _judge_one,
    coerce_judgement,
    run_judges,
)
from billing_qa.services.llm.client import LLMError


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


def test_judge_one_tolerates_partial_json(monkeypatch: Any) -> None:
    """OpenCode 裁判只返回 pass/total_score 时按总分折算，不应整单报错。"""

    class FakeClient:
        def __init__(self, model: Any) -> None:
            self.model = model

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def chat(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                content='{"pass": true, "total_score": 91, "reason": "取数一致"}',
                prompt_tokens=123,
                completion_tokens=45,
            )

    monkeypatch.setattr("billing_qa.services.engine.judge.LLMClient", FakeClient)
    model = SimpleNamespace(id=7, name="OpenCode 裁判", model_name="mock/judge-a")
    outcome = asyncio.run(
        _judge_one(
            model,  # type: ignore[arg-type]
            "评分",
            termination_status="COMPLETED",
            threshold=60,
            temperature=None,
        )
    )
    assert outcome.pass_flag is True
    assert outcome.total_score == 91
    assert outcome.dimension_scores["data_accuracy"] > 0
    assert outcome.prompt_tokens == 123
    assert outcome.completion_tokens == 45


def test_judge_one_rejects_non_scoring_json(monkeypatch: Any) -> None:
    from billing_qa.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "llm_json_retry_backoff_seconds", 0.0)

    class FakeClient:
        def __init__(self, model: Any) -> None:
            self.model = model

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def chat(self, *args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                content='{"note": "看不懂"}', prompt_tokens=1, completion_tokens=1
            )

    monkeypatch.setattr("billing_qa.services.engine.judge.LLMClient", FakeClient)
    model = SimpleNamespace(id=8, name="坏裁判", model_name="bad")
    with pytest.raises(LLMError):
        asyncio.run(
            _judge_one(
                model,  # type: ignore[arg-type]
                "评分",
                termination_status="COMPLETED",
                threshold=60,
                temperature=None,
            )
        )


def test_judge_one_recovers_after_format_retry(monkeypatch: Any) -> None:
    """模型输出不稳定（先坏后好）时，裁决应通过纠错重试自愈而不是报错。"""
    from billing_qa.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "llm_json_retry_backoff_seconds", 0.0)

    class FakeClient:
        def __init__(self, model: Any) -> None:
            self.model = model

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def chat(self, messages: Any, **kwargs: Any) -> Any:
            if len(messages) == 2:  # 首次调用：system + user 原始对话
                return SimpleNamespace(
                    content="综合来看该回答数据准确，我认为可以通过。",
                    prompt_tokens=100,
                    completion_tokens=50,
                )
            return SimpleNamespace(
                content='{"pass": true, "total_score": 91, "reason": "取数一致"}',
                prompt_tokens=120,
                completion_tokens=60,
            )

    monkeypatch.setattr("billing_qa.services.engine.judge.LLMClient", FakeClient)
    model = SimpleNamespace(id=9, name="不稳定裁判", model_name="mock/judge-b")
    outcome = asyncio.run(
        _judge_one(
            model,  # type: ignore[arg-type]
            "评分",
            termination_status="COMPLETED",
            threshold=60,
            temperature=None,
        )
    )
    assert outcome.pass_flag is True
    assert outcome.total_score == 91
    # 重试产生的过程说明进入 policy_notes，token 按全部尝试累计
    assert any("重试" in note for note in outcome.policy_notes)
    assert outcome.prompt_tokens == 220
    assert outcome.completion_tokens == 110


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
