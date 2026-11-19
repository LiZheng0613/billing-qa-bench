"""LLM JSON 输出修复重试：模型输出不稳定时的语义层重试。

与 ``LLMClient`` 内置的传输层重试（网络异常 / HTTP 429/5xx）互补，这里处理的是
**模型成功返回、但内容不符合约定的标准化 JSON 结构**的场景：

- 整体不是合法 JSON（Markdown 代码块、前后噪声、空内容、截断）；
- JSON 可解析但缺少业务必需的字段（如裁判的评分字段）。

重试策略（带纠错反馈的多轮修复）：

1. 第 1 次按原始 messages 调用；
2. 解析 / 校验失败时，把「上一轮的错误输出 + 失败原因 + 输出要求」作为
   assistant / user 消息追加到对话，让模型自我修复后重新输出；
3. 退避后重试，直到成功或达到最大尝试次数，最终失败抛 ``LLMSchemaError``。

传输层异常（``LLMError``，已由 ``LLMClient`` 按模型配置重试过）不在此重复重试，
直接向上传播。多次尝试的 token 用量会累计，保证计费与统计口径不丢。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from billing_qa.core.config import settings
from billing_qa.core.exceptions import ValidationError
from billing_qa.core.logging import get_logger
from billing_qa.services.llm.client import LLMClient, LLMError, LLMResult
from billing_qa.services.llm.json_parser import parse_json_object

logger = get_logger(__name__)

# 坏输出反馈给模型时的截断长度，避免长噪声撑爆上下文
_BAD_OUTPUT_PREVIEW = 800


class LLMSchemaError(LLMError):
    """模型输出在重试后仍无法修正为约定的 JSON 结构。"""


@dataclass
class JsonChatResult:
    """``chat_json`` 的成功结果。"""

    data: dict[str, Any]
    """最后一次成功调用的原始 LLMResult（raw / model_name 等）。"""
    result: LLMResult
    attempts: int = 1
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    failures: list[str] = field(default_factory=list)
    """各次失败尝试的原因（成功时为空表示一次通过）。"""

    @property
    def retried(self) -> bool:
        return self.attempts > 1


Validator = Callable[[dict[str, Any]], "str | None"]
"""校验已解析 JSON 的回调：返回 ``None`` 表示通过，否则返回失败原因文案。"""


def _repair_messages(
    messages: list[dict[str, str]],
    bad_output: str,
    errors: list[str],
    *,
    required_keys: Sequence[str],
) -> list[dict[str, str]]:
    """构造纠错反馈对话：原对话 + 坏输出 + 明确的修复要求。"""
    preview = bad_output[:_BAD_OUTPUT_PREVIEW]
    parts = [
        "你上一轮的输出未能通过解析或校验，失败原因：",
        "；".join(errors[-2:]),
    ]
    if preview.strip():
        parts.append(f"上一轮输出（截断）如下：\n{preview}")
    parts.append("请重新作答，严格遵守：只返回一个合法 JSON 对象，")
    parts.append("不要使用 Markdown 代码块，不要输出任何解释性文字。")
    if required_keys:
        parts.append(f"必须包含字段：{', '.join(required_keys)}。")
    return [
        *messages,
        {"role": "assistant", "content": bad_output[:_BAD_OUTPUT_PREVIEW]},
        {"role": "user", "content": "\n".join(parts)},
    ]


async def chat_json(
    client: LLMClient,
    messages: list[dict[str, str]],
    *,
    json_mode: bool = True,
    temperature: float | None = None,
    required_keys: Sequence[str] = (),
    validator: Validator | None = None,
    max_attempts: int | None = None,
    backoff_seconds: float | None = None,
) -> JsonChatResult:
    """调用 LLM 并解析为约定的 JSON 对象，输出不合规时带纠错反馈重试。

    :param required_keys: 全部必需的字段名（任一缺失即视为失败）。
    :param validator: 返回 ``None`` 表示通过，否则返回失败原因（可表达
        「任一字段存在即可」等更复杂的契约，与 ``required_keys`` 叠加生效）。
    :raises LLMSchemaError: 重试耗尽仍无法得到合规 JSON。
    :raises LLMError: 传输层失败（已由 ``LLMClient`` 内部重试过，不在此重试）。
    """
    attempts_limit = 1 + max(0, settings.llm_json_retry_attempts)
    if max_attempts is not None:
        attempts_limit = max(1, int(max_attempts))
    backoff = settings.llm_json_retry_backoff_seconds if backoff_seconds is None else backoff_seconds

    current = list(messages)
    errors: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    latency_ms = 0.0
    result: LLMResult | None = None
    bad_output = ""

    for attempt in range(1, attempts_limit + 1):
        result = await client.chat(
            current,
            json_mode=json_mode,
            temperature=temperature,
        )
        prompt_tokens += result.prompt_tokens
        completion_tokens += result.completion_tokens
        latency_ms += getattr(result, "latency_ms", 0.0) or 0.0

        error: str | None = None
        data: dict[str, Any] | None = None
        try:
            data = parse_json_object(result.content)
        except ValidationError as exc:
            error = str(exc)
        if data is not None:
            if required_keys:
                missing = [key for key in required_keys if key not in data]
                if missing:
                    error = f"缺少必需字段: {', '.join(missing)}"
            if error is None and validator is not None:
                error = validator(data)
        if error is None and data is not None:
            if attempt > 1:
                logger.info(
                    "LLM JSON 输出经 %d 次重试后修复（前 %d 次失败）",
                    attempt - 1,
                    len(errors),
                )
            return JsonChatResult(
                data=data,
                result=result,
                attempts=attempt,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=round(latency_ms, 2),
                failures=errors,
            )

        errors.append(error or "未知解析失败")
        bad_output = str(result.content or "")
        logger.warning(
            "LLM JSON 输出不合规(第 %d/%d 次): %s", attempt, attempts_limit, error
        )
        if attempt < attempts_limit:
            current = _repair_messages(
                messages,
                bad_output,
                errors,
                required_keys=required_keys,
            )
            await asyncio.sleep(
                min(backoff * (2 ** (attempt - 1)), 8.0) * (1 + random.random() * 0.2)
            )

    preview = bad_output[:_BAD_OUTPUT_PREVIEW]
    raise LLMSchemaError(
        f"模型输出在 {attempts_limit} 次尝试后仍不符合约定 JSON 结构: "
        f"{'；'.join(errors[:3])}；最后一次输出: {preview}"
    )
