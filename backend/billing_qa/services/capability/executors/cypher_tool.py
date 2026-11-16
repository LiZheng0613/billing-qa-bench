"""客户 Cypher 执行器工具客户端。

契约 v0.1（已冻结）：
- ``POST /execute`` 执行参数化 Cypher
- ``GET  /logs``    按 session_id 增量拉取工具调用日志
- ``GET  /health``  探活
- ``GET  /meta``    版本与限额元信息

平台侧只做只读白名单校验，工具侧再做一次只读校验，双层防护。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from billing_qa.core.logging import get_logger
from billing_qa.services.capability.executors.base import (
    BaseCapabilityExecutor,
    CapabilityExecutorError,
    ExecutionResult,
    ToolLogEntry,
)

logger = get_logger(__name__)

#: 工具契约错误码（九种）
ERROR_CODES: dict[str, dict[str, Any]] = {
    "INVALID_REQUEST": {"status": 422, "retryable": False, "message": "请求格式不合法"},
    "PARAM_MISSING": {"status": 422, "retryable": False, "message": "缺少必填参数"},
    "QUERY_SYNTAX": {"status": 422, "retryable": False, "message": "Cypher 语法错误"},
    "QUERY_FORBIDDEN": {"status": 403, "retryable": False, "message": "查询被只读策略拒绝"},
    "TIMEOUT": {"status": 504, "retryable": True, "message": "查询超时"},
    "TOOL_UNAVAILABLE": {"status": 502, "retryable": True, "message": "执行器不可用"},
    "ROW_LIMIT_EXCEEDED": {"status": 422, "retryable": False, "message": "返回行数超过上限"},
    "SESSION_NOT_FOUND": {"status": 404, "retryable": False, "message": "会话不存在"},
    "INTERNAL_ERROR": {"status": 502, "retryable": True, "message": "执行器内部错误"},
}

#: 只读白名单：出现写语句直接拒绝（平台侧第一道防线）
_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|CALL\s+\{[^}]*\}\s*IN\s+TRANSACTIONS)\b",
    re.IGNORECASE,
)
_ALLOWED_STARTS = ("MATCH", "OPTIONAL MATCH", "RETURN", "WITH", "UNWIND", "CALL")
_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")


def _strip_literals(query: str) -> str:
    """剔除字符串字面量与反引号标识符，避免误伤正常取值。"""
    return _LITERAL.sub(" ", query)


def ensure_read_only(query: str) -> None:
    """拒绝任何写操作，防止取证通道被滥用。"""
    text = (query or "").strip()
    if not text:
        raise CapabilityExecutorError("查询语句为空", code="INVALID_REQUEST")
    if _WRITE_KEYWORDS.search(_strip_literals(text)):
        raise CapabilityExecutorError(
            "查询包含写操作关键字，已按只读策略拒绝", code="QUERY_FORBIDDEN", status_code=403
        )
    if not text.upper().startswith(_ALLOWED_STARTS):
        raise CapabilityExecutorError(
            "查询必须以 MATCH / RETURN / WITH / UNWIND / CALL 开头",
            code="QUERY_FORBIDDEN",
            status_code=403,
        )


class CypherToolExecutor(BaseCapabilityExecutor):
    """通过 HTTP 调用客户已有的 Cypher 执行器工具。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        max_rows: int = 200,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.headers = headers or {}
        self.transport = transport
        self.verify_ssl = verify_ssl

    async def execute(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        ensure_read_only(query)
        payload = {
            "session_id": session_id,
            "query": query,
            "params": params or {},
            "max_rows": max_rows or self.max_rows,
            "timeout_ms": int((timeout_seconds or self.timeout_seconds) * 1000),
        }
        data = await self._request("POST", "/execute", json=payload)
        return _to_result(data)

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def meta(self) -> dict[str, Any]:
        return await self._request("GET", "/meta")

    async def logs(
        self, *, session_id: str | None = None, since_seq: int = 0, limit: int = 200
    ) -> list[ToolLogEntry]:
        data = await self._request(
            "GET",
            "/logs",
            params={"session_id": session_id, "since_seq": since_seq, "limit": limit},
        )
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        entries: list[ToolLogEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entries.append(
                ToolLogEntry(
                    seq=int(item.get("seq") or 0),
                    session_id=item.get("session_id"),
                    kind=str(item.get("kind") or "CYPHER"),
                    query=item.get("query"),
                    params=item.get("params"),
                    status=str(item.get("status") or "SUCCESS"),
                    rows=int(item.get("rows") or 0),
                    elapsed_ms=item.get("elapsed_ms"),
                    error=item.get("error"),
                    created_at=item.get("created_at"),
                )
            )
        return entries

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        timeout = httpx.Timeout(self.timeout_seconds)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=timeout,
                transport=self.transport,
                verify=self.verify_ssl,
            ) as client:
                response = await client.request(method, path, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise CapabilityExecutorError(
                "调用 Cypher 执行器超时", code="TIMEOUT", status_code=504, retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise CapabilityExecutorError(
                f"调用 Cypher 执行器失败: {exc}",
                code="TOOL_UNAVAILABLE",
                status_code=502,
                retryable=True,
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise CapabilityExecutorError(
                f"执行器返回非 JSON（HTTP {response.status_code}）",
                code="TOOL_UNAVAILABLE",
                status_code=502,
                retryable=True,
            ) from exc

        code = str(body.get("code") or "OK").upper() if isinstance(body, dict) else "OK"
        if isinstance(body, dict) and body.get("success") is True and code in {"0", ""}:
            code = "OK"
        if code not in {"OK", "0", "SUCCESS"}:
            meta = ERROR_CODES.get(code)
            raise CapabilityExecutorError(
                str(body.get("message") or (meta or {}).get("message") or "执行器返回错误"),
                code=code,
                status_code=int((meta or {}).get("status") or 502),
                retryable=bool((meta or {}).get("retryable")),
                detail=body.get("detail") if isinstance(body, dict) else None,
            )
        if response.status_code >= 400:
            raise CapabilityExecutorError(
                f"执行器 HTTP {response.status_code}",
                code="TOOL_UNAVAILABLE",
                status_code=502,
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        if isinstance(body, dict) and "data" in body:
            return body.get("data") or {}
        return body


def _to_result(data: Any) -> ExecutionResult:
    if not isinstance(data, dict):
        return ExecutionResult(raw={})
    columns_raw = data.get("columns") or []
    columns: list[str] = []
    for item in columns_raw:
        if isinstance(item, dict):
            columns.append(str(item.get("name") or item.get("column") or ""))
        else:
            columns.append(str(item))
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    return ExecutionResult(
        columns=[item for item in columns if item],
        rows=[row for row in rows if isinstance(row, dict)],
        row_count=int(data.get("row_count") or len(rows)),
        truncated=bool(data.get("truncated")),
        elapsed_ms=data.get("elapsed_ms"),
        query_id=data.get("query_id"),
        executor_version=data.get("executor_version"),
        raw=data,
    )
