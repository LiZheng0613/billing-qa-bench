"""被测问账系统接口适配器：请求渲染、Pre-Hook 鉴权、SSE/非流式、白盒提取。"""

from __future__ import annotations

import base64
import os
import shutil
import ssl
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from billing_qa.core.crypto import decrypt_value
from billing_qa.core.exceptions import TargetApiError
from billing_qa.core.logging import get_logger
from billing_qa.models.environment import Environment
from billing_qa.services.adapter.extractor import (
    DEFAULT_EXTRACTORS,
    extract_all,
    extract_final_answer,
    extract_value,
)
from billing_qa.services.adapter.renderer import format_history, render_structure
from billing_qa.services.adapter.sse import consume_sse

logger = get_logger(__name__)

DEFAULT_BODY_TEMPLATE: dict[str, Any] = {
    "query": "{{query}}",
    "session_id": "{{session_id}}",
    "history": "{{history}}",
}


@dataclass
class TargetResponse:
    """一次被测接口调用的标准化结果。"""

    final_answer: str
    latency_ms: float
    ttft_ms: float | None = None
    session_id: str | None = None
    intent: str | None = None
    generated_sql: str | None = None
    api_params: dict | None = None
    raw_db_data: Any = None
    tool_calls: list | None = None
    status_code: int | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    response_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnvContext:
    """执行期环境快照（已解密变量），避免在异步任务中持有 ORM 会话。"""

    id: int | None
    base_url: str
    health_path: str
    api_method: str
    api_path: str
    headers_template: dict[str, str] | None
    body_template: dict | None
    is_stream: bool
    extractors: dict[str, str] | None
    pre_hook: dict | None
    timeout_seconds: float
    verify_ssl: bool
    variables: dict[str, str] = field(default_factory=dict)
    session_mode: str = "SERVER_RETURNED"
    basic_username: str | None = None
    basic_password: str | None = None
    mtls_client_cert: str | None = None
    mtls_client_key: str | None = None
    mtls_key_password: str | None = None
    mtls_ca_cert: str | None = None

    @classmethod
    def from_orm(cls, env: Environment) -> EnvContext:
        variables: dict[str, str] = {}
        for item in env.variables:
            variables[item.key] = decrypt_value(item.value_encrypted) or ""
        basic = env.basic_auth or {}
        mtls = env.mtls or {}
        return cls(
            id=env.id,
            base_url=env.base_url.rstrip("/"),
            health_path=env.health_path or "/health",
            api_method=(env.api_method or "POST").upper(),
            api_path=env.api_path or "",
            headers_template=env.headers_template,
            body_template=env.body_template,
            is_stream=bool(env.is_stream),
            extractors=env.extractors or dict(DEFAULT_EXTRACTORS),
            pre_hook=env.pre_hook,
            timeout_seconds=env.timeout_seconds or 60.0,
            verify_ssl=env.verify_ssl,
            variables=variables,
            session_mode=env.session_mode or "SERVER_RETURNED",
            basic_username=(str(basic.get("username") or "") or None),
            basic_password=(decrypt_value(basic.get("password_encrypted")) or None),
            mtls_client_cert=(decrypt_value(mtls.get("client_cert_encrypted")) or None),
            mtls_client_key=(decrypt_value(mtls.get("client_key_encrypted")) or None),
            mtls_key_password=(decrypt_value(mtls.get("key_password_encrypted")) or None),
            mtls_ca_cert=(decrypt_value(mtls.get("ca_cert_encrypted")) or None),
        )

    @property
    def mtls_enabled(self) -> bool:
        """客户端证书与私钥齐备时才启用双向认证。"""
        return bool(self.mtls_client_cert and self.mtls_client_key)

    @property
    def chat_url(self) -> str:
        path = self.api_path or ""
        if path.startswith("http"):
            return path
        if path and not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"


class TargetApiAdapter:
    """被测系统对话接口适配器，单次执行复用一个实例。"""

    def __init__(self, env: EnvContext):
        self.env = env
        self._token_loaded = False
        self._mtls_dir: str | None = None
        self._mtls_files: tuple[str, str, str | None, str | None] | None = None

    # ------------------------------------------------------------------ #
    # HTTP 基础
    # ------------------------------------------------------------------ #
    def _prepare_mtls(self) -> tuple[str, str, str | None, str | None] | None:
        """把加密存储的 PEM 落为临时文件，供 httpx 建立 TLS 客户端证书链。

        Python 的 SSL 只接受文件路径，因此这里写入仅当前用户可读的临时目录，
        适配器生命周期结束后统一清理。
        """
        if not self.env.mtls_enabled:
            return None
        if self._mtls_files is not None:
            return self._mtls_files
        if self._mtls_dir is None:
            self._mtls_dir = tempfile.mkdtemp(prefix="billing_qa_mtls_")
        cert_path = os.path.join(self._mtls_dir, "client_cert.pem")
        key_path = os.path.join(self._mtls_dir, "client_key.pem")
        _write_secret(cert_path, self.env.mtls_client_cert or "")
        _write_secret(key_path, self.env.mtls_client_key or "")
        ca_path: str | None = None
        if self.env.mtls_ca_cert:
            ca_path = os.path.join(self._mtls_dir, "ca_cert.pem")
            _write_secret(ca_path, self.env.mtls_ca_cert)
        self._mtls_files = (cert_path, key_path, self.env.mtls_key_password, ca_path)
        return self._mtls_files

    def close(self) -> None:
        """清理 mTLS 临时证书文件。"""
        self._mtls_files = None
        if self._mtls_dir:
            shutil.rmtree(self._mtls_dir, ignore_errors=True)
            self._mtls_dir = None

    def _build_ssl_context(self, mtls: tuple[str, str, str | None, str | None]):
        """构建 TLS 上下文：加载客户端证书链，并按需替换服务端信任根。

        注意：httpx 0.28 同时传 ``verify=<字符串路径>`` 与 ``cert=`` 时，
        ``cert`` 会被静默忽略，因此这里统一用 SSLContext 显式配置。
        """
        cert_path, key_path, password, ca_path = mtls
        if ca_path:
            context = httpx.create_ssl_context(
                verify=ssl.create_default_context(cafile=ca_path)
            )
        else:
            context = httpx.create_ssl_context(verify=self.env.verify_ssl)
        try:
            context.load_cert_chain(cert_path, key_path, password or None)
        except (ssl.SSLError, OSError) as exc:
            raise TargetApiError(f"mTLS 客户端证书加载失败: {exc}") from exc
        return context

    def _client(self) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            connect=min(15.0, self.env.timeout_seconds),
            read=self.env.timeout_seconds,
            write=self.env.timeout_seconds,
            pool=self.env.timeout_seconds,
        )
        verify: Any = self.env.verify_ssl
        mtls = self._prepare_mtls()
        if mtls:
            verify = self._build_ssl_context(mtls)
        return httpx.AsyncClient(timeout=timeout, verify=verify)

    async def health_check(self) -> tuple[bool, int | None, float | None, str]:
        url = f"{self.env.base_url}{self.env.health_path or ''}"
        started = time.perf_counter()
        try:
            async with self._client() as client:
                response = await client.get(url)
            latency = round((time.perf_counter() - started) * 1000, 2)
            ok = response.status_code < 500
            return ok, response.status_code, latency, f"HTTP {response.status_code}"
        except Exception as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            return False, None, latency, f"{type(exc).__name__}: {exc}"

    async def _ensure_token(self, client: httpx.AsyncClient, extra_variables: dict[str, Any] | None = None) -> None:
        """执行前置接口钩子（每次执行一次），提取变量注入运行时变量池。

        前缀配置的 headers/body 可用环境变量池与用例级请求变量渲染；
        ``extract`` 配置为「变量名 -> JSONPath」映射，提取结果后续轮次复用。
        """
        if self._token_loaded:
            return
        self._token_loaded = True
        hook = self.env.pre_hook or {}
        if not hook.get("enabled"):
            return
        # 前置请求变量优先级：用例级 > 环境变量池
        variables: dict[str, Any] = dict(self.env.variables)
        variables.update(extra_variables or {})
        missing: list[str] = []
        headers = render_structure(hook.get("headers") or {}, variables, missing)
        body = render_structure(hook.get("body"), variables, missing)
        if missing:
            unique = list(dict.fromkeys(missing))
            raise TargetApiError(f"前置接口变量缺失: {', '.join(unique)}")
        final_headers = {str(k): str(v) for k, v in (headers or {}).items()}
        auth_header = basic_auth_header(self.env.basic_username, self.env.basic_password)
        if auth_header and not any(key.lower() == "authorization" for key in final_headers):
            final_headers["Authorization"] = auth_header
        path = hook.get("path") or ""
        url = path if str(path).startswith("http") else f"{self.env.base_url}{path}"
        method = str(hook.get("method") or "POST").upper()
        try:
            if method == "GET":
                response = await client.request(
                    method, url, headers=final_headers or None, params=body or None
                )
            else:
                response = await client.request(
                    method, url, headers=final_headers or None, json=body
                )
        except httpx.HTTPError as exc:
            # 前置接口网络不可达属于环境故障，可自动重试
            raise TargetApiError(f"前置接口调用失败: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise TargetApiError(
                f"前置接口失败 HTTP {response.status_code}: {response.text[:300]}",
                retryable=is_retryable_status(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            # 网关错误页 / 截断响应，视为环境异常
            raise TargetApiError("前置接口响应不是合法 JSON", retryable=True) from exc
        extractions = hook.get("extract") or {}
        if extractions:
            extracted: dict[str, Any] = {}
            for name, expression in extractions.items():
                value = extract_value(payload, str(expression))
                if value not in (None, ""):
                    extracted[str(name)] = value
            empty = [str(name) for name in extractions if str(name) not in extracted]
            if empty:
                raise TargetApiError(
                    f"前置接口未提取到字段: {', '.join(empty)}，请检查 extract 配置"
                )
            for name, value in extracted.items():
                self.env.variables[name] = str(value)
            return
        token = extract_value(payload, hook.get("token_jsonpath") or "$.data.token")
        if token in (None, ""):
            raise TargetApiError("前置接口未提取到 Token，请检查 token_jsonpath 配置")
        token_var = hook.get("token_var") or "auth_token"
        self.env.variables[token_var] = str(token)

    # ------------------------------------------------------------------ #
    # 对话调用
    # ------------------------------------------------------------------ #
    async def chat(
        self,
        query: str,
        session_id: str | None = None,
        history: list[dict[str, str]] | None = None,
        extra_variables: dict[str, Any] | None = None,
    ) -> TargetResponse:
        history = history or []
        async with self._client() as client:
            await self._ensure_token(client, extra_variables)
            # 变量合并优先级：运行时 > 用例级 > 环境变量池
            variables: dict[str, Any] = dict(self.env.variables)
            variables.update(extra_variables or {})
            variables.update(
                {
                    "query": query,
                    "session_id": session_id or "",
                    "history": format_history(history, style="messages"),
                    "history_text": format_history(history, style="text"),
                    "turn": len(history) // 2 + 1,
                    "timestamp_iso": now_iso(),
                }
            )
            header_missing: list[str] = []
            headers = render_structure(self.env.headers_template or {}, variables, header_missing)
            missing: list[str] = list(header_missing)
            body = render_structure(
                self.env.body_template or dict(DEFAULT_BODY_TEMPLATE), variables, missing
            )
            if missing:
                unique = list(dict.fromkeys(missing))
                raise TargetApiError(f"请求模板变量缺失: {', '.join(unique)}")

            final_headers: dict[str, str] = {"Content-Type": "application/json"}
            final_headers.update({str(k): str(v) for k, v in (headers or {}).items()})
            if self.env.is_stream:
                final_headers.setdefault("Accept", "text/event-stream")
            auth_header = basic_auth_header(self.env.basic_username, self.env.basic_password)
            if auth_header and not any(
                key.lower() == "authorization" for key in final_headers
            ):
                final_headers["Authorization"] = auth_header

            request_payload: dict[str, Any] = {
                "method": self.env.api_method,
                "url": self.env.chat_url,
                "headers": _sanitize_headers(final_headers),
                "body": body,
            }
            started = time.perf_counter()
            try:
                if self.env.api_method == "GET":
                    response = await client.request(
                        "GET", self.env.chat_url, headers=final_headers, params=body
                    )
                else:
                    response = await client.request(
                        self.env.api_method,
                        self.env.chat_url,
                        headers=final_headers,
                        json=body,
                    )
                if response.status_code >= 400:
                    text = await response.aread()
                    raise TargetApiError(
                        f"被测接口返回 HTTP {response.status_code}: "
                        f"{text.decode('utf-8', 'ignore')[:300]}",
                        retryable=is_retryable_status(response.status_code),
                    )
                content_type = response.headers.get("content-type", "")
                if self.env.is_stream or "text/event-stream" in content_type:
                    sse_result = await consume_sse(response, started)
                    latency = round((time.perf_counter() - started) * 1000, 2)
                    if not sse_result.frames and not sse_result.content:
                        # 流式接口返回空帧，通常是上游网关/服务异常，可重试
                        raise TargetApiError(
                            f"SSE 响应为空：status={response.status_code}，未解析到任何数据帧",
                            retryable=True,
                        )
                    payload: Any = {
                        **sse_result.merged_metadata,
                        "_content": sse_result.content,
                        "_frames": sse_result.frames[-50:],
                    }
                    final_answer = extract_final_answer(payload, self.env.extractors)
                    if not final_answer:
                        final_answer = sse_result.content
                    extracted = extract_all(payload, self.env.extractors)
                    return TargetResponse(
                        final_answer=final_answer or "",
                        latency_ms=latency,
                        ttft_ms=sse_result.ttft_ms if sse_result.ttft_ms is not None else latency,
                        session_id=extracted.get("session_id") or session_id,
                        intent=extracted.get("intent"),
                        generated_sql=extracted.get("generated_sql"),
                        api_params=extracted.get("api_params"),
                        raw_db_data=extracted.get("raw_db_data"),
                        tool_calls=extracted.get("tool_calls"),
                        status_code=response.status_code,
                        request_payload=request_payload,
                        response_payload={
                            "mode": "sse",
                            "frame_count": len(sse_result.frames),
                            "metadata": sse_result.merged_metadata,
                            "frames": sse_result.frames[-50:],
                            "content": sse_result.content,
                        },
                    )
                latency = round((time.perf_counter() - started) * 1000, 2)
                text = await response.aread()
                try:
                    payload = response.json()
                except ValueError:
                    decoded = text.decode("utf-8", "ignore")
                    if decoded.lstrip().lower().startswith(("<!doctype", "<html")):
                        raise TargetApiError(
                            f"被测接口返回 HTML 错误页（HTTP {response.status_code}），"
                            "通常是网关或上游服务异常",
                            retryable=True,
                        ) from None
                    payload = {"content": decoded}
                final_answer = extract_final_answer(payload, self.env.extractors)
                extracted = extract_all(payload, self.env.extractors)
                if not final_answer and isinstance(payload, dict):
                    final_answer = str(payload.get("content") or "")
                return TargetResponse(
                    final_answer=final_answer or "",
                    latency_ms=latency,
                    ttft_ms=latency,
                    session_id=extracted.get("session_id") or session_id,
                    intent=extracted.get("intent"),
                    generated_sql=extracted.get("generated_sql"),
                    api_params=extracted.get("api_params"),
                    raw_db_data=extracted.get("raw_db_data"),
                    tool_calls=extracted.get("tool_calls"),
                    status_code=response.status_code,
                    request_payload=request_payload,
                    response_payload={"mode": "json", "body": payload},
                )
            except httpx.TimeoutException as exc:
                raise TargetApiError(
                    f"被测接口超时（{self.env.timeout_seconds}s）", retryable=True
                ) from exc
            except httpx.HTTPError as exc:
                raise TargetApiError(f"被测接口网络异常: {exc}", retryable=True) from exc


def _write_secret(path: str, content: str) -> None:
    """以 0600 权限写入敏感文件（Windows 上等价权限由 ACL 继承，尽力而为）。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def is_retryable_status(status_code: int) -> bool:
    """HTTP 状态码是否属于环境类、可重试的故障。"""
    return status_code == 429 or status_code >= 500


def basic_auth_header(username: str | None, password: str | None) -> str | None:
    """生成 Basic 鉴权请求头：base64(账号:密码)。"""
    if not username:
        return None
    raw = f"{username}:{password or ''}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """记录原始报文时对敏感头做掩码。"""
    sensitive = {"authorization", "cookie", "x-api-key", "token", "x-token"}
    return {
        key: ("***" if key.lower() in sensitive else value)
        for key, value in headers.items()
    }


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")
