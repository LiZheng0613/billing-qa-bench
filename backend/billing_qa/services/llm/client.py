"""LLM 客户端：OpenAI 兼容直连 / OpenCode Serve(SSE) 双接入。

- ``OPENAI_COMPATIBLE``：走 ``{base_url}/chat/completions``，带 RPM 限流、指数退避与 JSON 模式。
- ``OPENCODE_SERVE``：先在 serve 上创建 session，订阅 ``GET /api/event`` SSE，
  再发送 prompt，按事件流汇总文本与 usage。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from billing_qa.core.crypto import decrypt_value
from billing_qa.core.exceptions import AppError
from billing_qa.core.logging import get_logger
from billing_qa.models.enums import ModelConnectionType
from billing_qa.models.model_config import ModelConfig

logger = get_logger(__name__)


class LLMError(AppError):
    def __init__(self, message: str):
        super().__init__(message, code="LLM_ERROR", status_code=502)


class _LegacyOpenCodeApi(Exception):
    """当前 serve 不支持 v1 会话接口，需要回退到旧版 SSE 流程。"""


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
    connection_type: str = ModelConnectionType.OPENAI_COMPATIBLE.value
    serve_url: str | None = None
    serve_port: int | None = None
    serve_username: str | None = None
    serve_password: str | None = None
    opencode_agent: str | None = None
    opencode_model: str | None = None
    opencode_directory: str | None = None
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
            connection_type=model.connection_type or ModelConnectionType.OPENAI_COMPATIBLE.value,
            serve_url=model.serve_url.rstrip("/") if model.serve_url else None,
            serve_port=model.serve_port,
            serve_username=model.serve_username,
            serve_password=decrypt_value(model.serve_password_encrypted),
            opencode_agent=model.opencode_agent,
            opencode_model=model.opencode_model,
            opencode_directory=model.opencode_directory,
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


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """解析一行 SSE，返回 JSON 事件体；非 data 行返回 None。"""
    text = line.strip()
    if not text or text.startswith(":") or not text.startswith("data:"):
        return None
    payload = text[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _ensure_ok(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    raise LLMError(f"{action}失败 HTTP {response.status_code}: {response.text[:300]}")


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
    def is_opencode(self) -> bool:
        return (
            (self.model.connection_type or "").upper()
            == ModelConnectionType.OPENCODE_SERVE.value
        )

    @property
    def chat_url(self) -> str:
        if self.model.base_url.endswith("/chat/completions"):
            return self.model.base_url
        return f"{self.model.base_url}/chat/completions"

    @property
    def opencode_base_url(self) -> str:
        """OpenCode Serve 根地址（地址 + 端口）。"""
        base = (self.model.serve_url or self.model.base_url or "").rstrip("/")
        if not base:
            raise LLMError("OpenCode Serve 未配置地址")
        if self.model.serve_port:
            netloc = urlsplit(base).netloc
            # 已是 host:port 形式时不再重复拼接（兼容 IPv6）
            if ":" not in netloc.rsplit("]", 1)[-1]:
                base = f"{base}:{self.model.serve_port}"
        return base

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.model.timeout_seconds, connect=15.0)
            )
        return self._client

    def _opencode_auth(self) -> httpx.BasicAuth | None:
        if not self.model.serve_password:
            return None
        return httpx.BasicAuth(
            self.model.serve_username or "opencode", self.model.serve_password
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMResult:
        if self.is_opencode:
            return await self._chat_opencode(
                messages,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
                extra=extra,
            )
        return await self._chat_openai(
            messages,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
        )

    # ------------------------------------------------------------------ OpenAI 兼容
    async def _chat_openai(
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

    # ------------------------------------------------------------------ OpenCode Serve
    async def _chat_opencode(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMResult:
        # OpenCode 的采样参数由 serve 侧模型配置决定，这里只负责拼接提示与收集事件。
        system_text, user_text = self._split_messages(messages)
        prompt = self._flatten_messages(messages)
        use_json = self.model.response_format_json if json_mode is None else json_mode
        if use_json:
            suffix = (
                "\n\n【输出要求】只返回单个合法 JSON 对象，"
                "不要使用 Markdown 代码块，也不要输出解释性文字。"
            )
            prompt += suffix
            user_text += suffix

        last_error: Exception | None = None
        legacy = False
        for attempt in range(self.model.max_retries + 1):
            await self._limiter.acquire()
            started = time.perf_counter()
            try:
                if not legacy:
                    try:
                        return await self._opencode_send_message(
                            system_text, user_text, started
                        )
                    except _LegacyOpenCodeApi:
                        legacy = True
                        logger.info("OpenCode Serve 不支持 v1 会话接口，回退旧版 SSE 流程")
                return await self._opencode_once(prompt, use_json, started)
            except (httpx.HTTPError, LLMError) as exc:
                last_error = exc
                logger.warning("OpenCode 调用失败(第 %d 次): %s", attempt + 1, exc)
                if attempt < self.model.max_retries:
                    backoff = min(2**attempt, 8) * (1 + random.random() * 0.2)
                    await asyncio.sleep(backoff)
        raise LLMError(f"OpenCode 调用失败（已重试 {self.model.max_retries} 次）: {last_error}")

    def _split_messages(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        """拆分出 system 指令与对话正文。"""
        systems: list[str] = []
        parts: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if not content:
                continue
            if role == "system":
                systems.append(content)
            elif role == "assistant":
                parts.append(f"【历史回答】\n{content}")
            else:
                parts.append(content)
        return "\n\n".join(systems), "\n\n".join(parts)

    def _flatten_messages(self, messages: list[dict[str, str]]) -> str:
        parts: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if not content:
                continue
            if role == "system":
                parts.append(f"【系统指令】\n{content}")
            elif role == "assistant":
                parts.append(f"【历史回答】\n{content}")
            else:
                parts.append(content)
        return "\n\n".join(parts)

    def _session_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": f"billing-qa · {self.model.code}"}
        if self.model.opencode_agent:
            payload["agent"] = self.model.opencode_agent
        model_ref = self._opencode_model_ref()
        if model_ref:
            payload["model"] = model_ref
        if self.model.opencode_directory:
            payload["location"] = {"directory": self.model.opencode_directory}
        return payload

    def _session_payload_v1(self) -> dict[str, Any]:
        """v1 ``POST /session`` 入参（不接受 location，目录走 query）。"""
        payload: dict[str, Any] = {"title": f"billing-qa · {self.model.code}"}
        if self.model.opencode_agent:
            payload["agent"] = self.model.opencode_agent
        model_ref = self._opencode_model_ref()
        if model_ref:
            payload["model"] = model_ref
        return payload

    def _opencode_params(self) -> dict[str, str]:
        if self.model.opencode_directory:
            return {"directory": self.model.opencode_directory}
        return {}

    @staticmethod
    def _unwrap_data(payload: Any) -> dict[str, Any]:
        """兼容 ``{"data": {...}}`` 信封与直接返回对象两种响应。"""
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload if isinstance(payload, dict) else {}

    async def _opencode_send_message(
        self, system_text: str, user_text: str, started: float
    ) -> LLMResult:
        """v1 同步接口：建会话 → 发消息 → 直接拿回复文本。"""
        base = self.opencode_base_url
        timeout = httpx.Timeout(max(self.model.timeout_seconds, 10.0), connect=15.0)
        params = self._opencode_params()
        async with httpx.AsyncClient(timeout=timeout, auth=self._opencode_auth()) as client:
            created = await client.post(
                f"{base}/session", params=params, json=self._session_payload_v1()
            )
            if created.status_code in (404, 405):
                raise _LegacyOpenCodeApi()
            _ensure_ok(created, "创建 OpenCode 会话")
            session = self._unwrap_data(created.json())
            session_id = str(session.get("id") or "")
            if not session_id:
                raise LLMError(f"OpenCode 未返回 sessionID: {created.text[:300]}")

            try:
                payload: dict[str, Any] = {
                    "parts": [{"type": "text", "text": user_text}]
                }
                model_ref = self._opencode_model_ref()
                if model_ref:
                    payload["model"] = {
                        "providerID": model_ref["providerID"],
                        "modelID": model_ref["id"],
                    }
                if self.model.opencode_agent:
                    payload["agent"] = self.model.opencode_agent
                if system_text:
                    payload["system"] = system_text
                response = await client.post(
                    f"{base}/session/{session_id}/message",
                    params=params,
                    json=payload,
                )
                _ensure_ok(response, "发送 OpenCode 提示")
                data = self._unwrap_data(response.json())
            finally:
                try:
                    await client.delete(f"{base}/session/{session_id}", params=params)
                except httpx.HTTPError:
                    logger.debug("清理 OpenCode session 失败: %s", session_id)

        info = data.get("info") or {}
        if isinstance(info, dict) and info.get("error"):
            error = info["error"]
            message = (
                error.get("data", {}).get("message")
                if isinstance(error, dict)
                else None
            ) or str(error)
            raise LLMError(f"OpenCode 会话执行失败: {message}")
        parts = data.get("parts") or []
        texts = [
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
            and part.get("type") == "text"
            and part.get("text")
        ]
        content = "\n".join(texts).strip()
        if not content:
            raise LLMError(f"OpenCode 未返回文本内容: {str(data)[:300]}")

        tokens = info.get("tokens") or {}
        provider_id = str(info.get("providerID") or "")
        model_id = str(info.get("modelID") or "")
        model_name = (
            f"{provider_id}/{model_id}"
            if provider_id and model_id
            else self.model.opencode_model or self.model.model_name
        )
        return LLMResult(
            content=content,
            prompt_tokens=int(tokens.get("input") or 0),
            completion_tokens=int(tokens.get("output") or 0),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            model_name=model_name,
            raw=data,
        )

    def _opencode_model_ref(self) -> dict[str, str] | None:
        raw = (self.model.opencode_model or "").strip()
        if not raw:
            return None
        if "/" not in raw:
            raise LLMError("opencode_model 需为 provider/model 格式，例如 anthropic/claude-sonnet-4")
        provider, model_id = raw.split("/", 1)
        if not provider or not model_id:
            raise LLMError("opencode_model 需为 provider/model 格式，例如 anthropic/claude-sonnet-4")
        return {"providerID": provider, "id": model_id}

    async def _opencode_once(
        self, prompt: str, use_json: bool, started: float
    ) -> LLMResult:
        base = self.opencode_base_url
        timeout = httpx.Timeout(max(self.model.timeout_seconds, 10.0), connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, auth=self._opencode_auth()) as client:
            created = await client.post(f"{base}/api/session", json=self._session_payload())
            _ensure_ok(created, "创建 OpenCode 会话")
            session_id = created.json().get("id")
            if not session_id:
                raise LLMError(f"OpenCode 未返回 sessionID: {created.text[:300]}")

            try:
                content, prompt_tokens, completion_tokens, model_name = (
                    await self._collect_session_reply(client, base, session_id, prompt, use_json)
                )
            finally:
                # 裁判调用是一次性的，尽量清理 session，避免 serve 侧堆积。
                try:
                    await client.delete(f"{base}/api/session/{session_id}")
                except httpx.HTTPError:
                    logger.debug("清理 OpenCode session 失败: %s", session_id)

        if not content.strip():
            raise LLMError("OpenCode 未返回文本内容")
        return LLMResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            model_name=model_name or self.model.opencode_model or self.model.model_name,
        )

    async def _collect_session_reply(
        self,
        client: httpx.AsyncClient,
        base: str,
        session_id: str,
        prompt: str,
        use_json: bool,
    ) -> tuple[str, int, int, str]:
        ended_texts: dict[str, str] = {}
        delta_texts: dict[str, list[str]] = {}
        prompt_tokens = 0
        completion_tokens = 0
        model_name = ""
        failed: str | None = None
        succeeded = False
        idle = False

        try:
            async with client.stream(
                "GET",
                f"{base}/api/event",
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(max(self.model.timeout_seconds, 10.0), connect=15.0),
            ) as stream:
                if stream.status_code >= 400:
                    body = (await stream.aread()).decode("utf-8", "replace")
                    raise LLMError(
                        f"订阅 OpenCode 事件流失败 HTTP {stream.status_code}: {body[:300]}"
                    )

                posted = await client.post(
                    f"{base}/api/session/{session_id}/prompt", json={"text": prompt}
                )
                _ensure_ok(posted, "发送 OpenCode 提示")

                async with asyncio.timeout(self.model.timeout_seconds):
                    async for line in stream.aiter_lines():
                        event = _parse_sse_line(line)
                        if not event:
                            continue
                        data = event.get("data") or {}
                        if not isinstance(data, dict) or data.get("sessionID") != session_id:
                            continue
                        event_type = str(event.get("type") or "")

                        if event_type == "session.text.delta":
                            message_id = str(data.get("assistantMessageID") or "")
                            delta = str(data.get("delta") or "")
                            delta_texts.setdefault(message_id, []).append(delta)
                        elif event_type == "session.text.ended":
                            message_id = str(data.get("assistantMessageID") or "")
                            ended_texts[message_id] = str(data.get("text") or "")
                            delta_texts.pop(message_id, None)
                        elif event_type in ("session.usage.updated", "session.usage.recorded"):
                            tokens = data.get("tokens") or {}
                            prompt_tokens = int(tokens.get("input") or prompt_tokens or 0)
                            completion_tokens = int(
                                tokens.get("output") or completion_tokens or 0
                            )
                        elif event_type == "session.execution.failed":
                            error = data.get("error") or {}
                            failed = str(
                                error.get("message") or error.get("type") or "OpenCode 执行失败"
                            )
                            break
                        elif event_type == "session.execution.succeeded":
                            succeeded = True
                        elif event_type == "session.idle":
                            idle = True
                            break

                        if succeeded and ended_texts:
                            break
        except TimeoutError:
            logger.warning("OpenCode 事件流等待超时: session=%s", session_id)

        if failed:
            raise LLMError(f"OpenCode 会话执行失败: {failed}")

        content = "\n".join(text for text in ended_texts.values() if text)
        if not content:
            content = "\n".join(
                "".join(chunks) for chunks in delta_texts.values() if chunks
            )
        if not content:
            (
                content,
                prompt_tokens,
                completion_tokens,
                model_name,
            ) = await self._fetch_last_assistant(client, base, session_id)
        if use_json:
            content = content.strip()
        if idle or succeeded:
            logger.debug("OpenCode session 完成: %s", session_id)
        return content, prompt_tokens, completion_tokens, model_name

    async def _fetch_last_assistant(
        self, client: httpx.AsyncClient, base: str, session_id: str
    ) -> tuple[str, int, int, str]:
        """SSE 没有拿到文本时的兜底：直接读取 session 消息。"""
        try:
            response = await client.get(
                f"{base}/api/session/{session_id}/message", params={"limit": 50}
            )
        except httpx.HTTPError:
            return "", 0, 0, ""
        if response.status_code >= 400:
            return "", 0, 0, ""
        items = (response.json() or {}).get("data") or []
        for item in reversed(items):
            if item.get("type") != "assistant":
                continue
            texts = [
                str(part.get("text") or "")
                for part in item.get("content") or []
                if part.get("type") == "text"
            ]
            content = "\n".join(text for text in texts if text)
            if content:
                tokens = item.get("tokens") or {}
                return (
                    content,
                    int(tokens.get("input") or 0),
                    int(tokens.get("output") or 0),
                    "",
                )
        return "", 0, 0, ""

    async def _opencode_health(self) -> None:
        base = self.opencode_base_url
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0), auth=self._opencode_auth()
        ) as client:
            errors: list[str] = []
            for path in ("/global/health", "/api/info"):
                try:
                    response = await client.get(f"{base}{path}")
                except httpx.HTTPError as exc:
                    errors.append(f"{path}: {exc}")
                    continue
                if response.status_code < 400:
                    return
                errors.append(f"{path}: HTTP {response.status_code}")
        raise LLMError(f"无法连接 OpenCode Serve {base}: {'; '.join(errors)}")

    async def ping(self) -> LLMResult:
        if self.is_opencode:
            await self._opencode_health()
        return await self.chat(
            [{"role": "user", "content": '请仅返回 JSON：{"ok": true}'}],
            json_mode=True,
            temperature=0,
            max_tokens=32,
        )
