"""LLM 客户端：OpenAI 兼容协议 + RPM 限流 + 指数退避 + 严格 JSON。"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from billing_qa.core.crypto import decrypt_value
from billing_qa.core.exceptions import AppError
from billing_qa.core.logging import get_logger
from billing_qa.models.model_config import ModelConfig

logger = get_logger(__name__)


class LLMError(AppError):
    def __init__(self, message: str):
        super().__init__(message, code="LLM_ERROR", status_code=502)


@dataclass
class LLMResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelContext:
    """执行期模型快照。"""

    id: int | None
    code: str
    name: str
    role: str
    base_url: str
    api_key: str | None
    model_name: str
    temperature: float
    top_p: float | None
    max_tokens: int
    timeout_seconds: float
    rpm_limit: int
    max_retries: int
    response_format_json: bool
    extra_params: dict | None = None

    @classmethod
    def from_orm(cls, model: ModelConfig) -> ModelContext:
        return cls(
            id=model.id,
            code=model.code,
            name=model.name,
            role=model.role,
            base_url=model.base_url.rstrip("/"),
            api_key=decrypt_value(model.api_key_encrypted),
            model_name=model.model_name,
            temperature=model.effective_temperature,
            top_p=model.top_p,
            max_tokens=model.max_tokens,
            timeout_seconds=model.timeout_seconds,
            rpm_limit=model.rpm_limit,
            max_retries=model.max_retries,
            response_format_json=model.response_format_json,
            extra_params=model.extra_params,
        )


class _RpmLimiter:
    """滑动窗口 RPM 限流器（进程内，按模型维度共享）。"""

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] > 60:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.rpm:
                    self._timestamps.append(now)
                    return
                delay = 60 - (now - self._timestamps[0]) + 0.01
            await asyncio.sleep(max(delay, 0.01))


_LIMITERS: dict[tuple[str, str, int], _RpmLimiter] = {}


def _get_limiter(model: ModelContext) -> _RpmLimiter:
    key = (model.base_url, model.model_name, model.rpm_limit)
    limiter = _LIMITERS.get(key)
    if limiter is None:
        limiter = _RpmLimiter(model.rpm_limit)
        _LIMITERS[key] = limiter
    return limiter


class LLMClient:
    """单模型客户端，可复用于多轮/多裁判调用。"""

    def __init__(self, model: ModelContext):
        self.model = model
        self._client: httpx.AsyncClient | None = None
        self._limiter = _get_limiter(model)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def chat_url(self) -> str:
        if self.model.base_url.endswith("/chat/completions"):
            return self.model.base_url
        return f"{self.model.base_url}/chat/completions"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.model.timeout_seconds, connect=15.0)
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model.model_name,
            "messages": messages,
            "temperature": self.model.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.model.max_tokens,
        }
        if self.model.top_p is not None:
            payload["top_p"] = self.model.top_p
        use_json = self.model.response_format_json if json_mode is None else json_mode
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        if self.model.extra_params:
            payload.update(self.model.extra_params)
        if extra:
            payload.update(extra)

        headers = {"Content-Type": "application/json"}
        if self.model.api_key:
            headers["Authorization"] = f"Bearer {self.model.api_key}"

        last_error: Exception | None = None
        for attempt in range(self.model.max_retries + 1):
            await self._limiter.acquire()
            started = time.perf_counter()
            try:
                response = await self._http().post(self.chat_url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("LLM 网络异常(第 %d 次): %s", attempt + 1, exc)
            else:
                latency = round((time.perf_counter() - started) * 1000, 2)
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = LLMError(
                        f"模型服务返回 HTTP {response.status_code}: {response.text[:200]}"
                    )
                    logger.warning("LLM 可重试错误(第 %d 次): HTTP %s", attempt + 1, response.status_code)
                elif response.status_code >= 400:
                    raise LLMError(
                        f"模型服务返回 HTTP {response.status_code}: {response.text[:300]}"
                    )
                else:
                    return self._parse_response(response, latency)
            if attempt < self.model.max_retries:
                backoff = min(2**attempt, 8) * (1 + random.random() * 0.2)
                await asyncio.sleep(backoff)
        raise LLMError(f"模型调用失败（已重试 {self.model.max_retries} 次）: {last_error}")

    def _parse_response(self, response: httpx.Response, latency: float) -> LLMResult:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("模型响应不是合法 JSON") from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"模型响应缺少 choices: {str(data)[:300]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            content = choices[0].get("text") or ""
        usage = data.get("usage") or {}
        return LLMResult(
            content=str(content),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=latency,
            model_name=data.get("model") or self.model.model_name,
            raw=data,
        )

    async def ping(self) -> LLMResult:
        return await self.chat(
            [{"role": "user", "content": '请仅返回 JSON：{"ok": true}'}],
            json_mode=True,
            temperature=0,
            max_tokens=32,
        )
