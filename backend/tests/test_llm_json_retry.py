"""LLM JSON 输出修复重试单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from billing_qa.core.config import settings
from billing_qa.services.llm.client import LLMError
from billing_qa.services.llm.retry import LLMSchemaError, chat_json


def _result(content: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> Any:
    return SimpleNamespace(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=100.0,
        model_name="mock",
        raw={},
    )


class FakeClient:
    """按脚本顺序返回预设输出，记录每次收到的对话。"""

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls.append([dict(item) for item in messages])
        # 仅剩一个脚本输出时重复返回（模拟一直失败）；否则按顺序弹出
        output = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(output, Exception):
            raise output
        return output


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "llm_json_retry_attempts", 2)
    monkeypatch.setattr(settings, "llm_json_retry_backoff_seconds", 0.0)


def test_success_first_attempt() -> None:
    client = FakeClient([_result('{"pass": true, "total_score": 90}')])
    chat = asyncio.run(chat_json(client, [{"role": "user", "content": "评分"}]))
    assert chat.attempts == 1
    assert chat.retried is False
    assert chat.data["total_score"] == 90
    assert chat.failures == []
    assert chat.prompt_tokens == 10
    assert len(client.calls) == 1


def test_retries_with_repair_feedback_until_valid() -> None:
    client = FakeClient(
        [
            _result("抱歉，我认为该回答数据准确，评分为 90 分。", 10, 5),
            _result('{"pass": true, "total_score": 90}', 30, 15),
        ]
    )
    chat = asyncio.run(chat_json(client, [{"role": "user", "content": "评分"}]))
    assert chat.attempts == 2
    assert chat.data["total_score"] == 90
    # token 按全部尝试累计
    assert chat.prompt_tokens == 40
    assert chat.completion_tokens == 20
    assert len(chat.failures) == 1

    # 第二次调用应包含坏输出反馈与修复要求
    assert len(client.calls) == 2
    repair = client.calls[1]
    roles = [item["role"] for item in repair]
    assert roles == ["user", "assistant", "user"]
    assert "90 分" in repair[1]["content"]
    assert "合法 JSON" in repair[2]["content"]


def test_missing_required_keys_triggers_retry() -> None:
    client = FakeClient(
        [
            _result('{"note": "看不懂"}'),
            _result('{"pass": true, "total_score": 88}'),
        ]
    )
    chat = asyncio.run(
        chat_json(
            client,
            [{"role": "user", "content": "评分"}],
            required_keys=("pass", "total_score"),
        )
    )
    assert chat.attempts == 2
    assert "缺少必需字段" in chat.failures[0]


def test_validator_supports_any_of_keys() -> None:
    def any_score_key(data: dict[str, Any]) -> str | None:
        if not any(key in data for key in ("total_score", "dimension_scores", "pass")):
            return "缺少评分字段"
        return None

    client = FakeClient(
        [
            _result('{"note": "跑题"}'),
            _result('{"pass": true}'),
        ]
    )
    chat = asyncio.run(
        chat_json(client, [{"role": "user", "content": "评分"}], validator=any_score_key)
    )
    assert chat.attempts == 2
    assert chat.data == {"pass": True}


def test_raises_after_all_attempts_exhausted() -> None:
    client = FakeClient([_result("这不是 JSON")])
    with pytest.raises(LLMSchemaError) as exc_info:
        asyncio.run(chat_json(client, [{"role": "user", "content": "评分"}]))
    assert "3 次尝试" in str(exc_info.value)
    assert "这不是 JSON" in str(exc_info.value)
    assert len(client.calls) == 3


def test_transport_error_propagates_without_format_retry() -> None:
    client = FakeClient([LLMError("模型调用失败（已重试 2 次）: connection reset")])
    with pytest.raises(LLMError) as exc_info:
        asyncio.run(chat_json(client, [{"role": "user", "content": "评分"}]))
    assert not isinstance(exc_info.value, LLMSchemaError)
    assert len(client.calls) == 1


def test_json_mode_and_temperature_passed_through() -> None:
    class KwargsClient:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def chat(self, messages: Any, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return _result('{"ok": true}')

    client = KwargsClient()
    asyncio.run(chat_json(client, [{"role": "user", "content": "x"}], temperature=0.3))
    assert client.kwargs == {"json_mode": True, "temperature": 0.3}
