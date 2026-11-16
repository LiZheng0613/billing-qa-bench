"""Cypher 执行器工具 mock（契约 v0.1）。

仅实现四个正式端点：``/execute``、``/logs``、``/health``、``/meta``。
测试通过 ``httpx.ASGITransport`` 直接挂载，无需启动真实端口。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ERROR_CODES: dict[str, int] = {
    "INVALID_REQUEST": 422,
    "PARAM_MISSING": 422,
    "QUERY_SYNTAX": 422,
    "QUERY_FORBIDDEN": 403,
    "TIMEOUT": 504,
    "TOOL_UNAVAILABLE": 502,
    "ROW_LIMIT_EXCEEDED": 422,
    "SESSION_NOT_FOUND": 404,
    "INTERNAL_ERROR": 502,
}


@dataclass
class MutableState:
    seq: int = 0
    logs: list[dict[str, Any]] = field(default_factory=list)


STATE = MutableState()


def reset_state() -> None:
    STATE.seq = 0
    STATE.logs.clear()


class ExecuteRequest(BaseModel):
    session_id: str | None = None
    query: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    max_rows: int = 200
    timeout_ms: int = 10_000


def _ok(data: Any) -> dict[str, Any]:
    return {
        "code": "OK",
        "message": "",
        "data": data,
        "request_id": uuid.uuid4().hex,
    }


def _error(code: str, message: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=ERROR_CODES.get(code, 500),
        content={
            "code": code,
            "message": message or code,
            "data": None,
            "request_id": uuid.uuid4().hex,
        },
    )


def _append_log(**kwargs: Any) -> int:
    STATE.seq += 1
    STATE.logs.append({"seq": STATE.seq, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **kwargs})
    return STATE.seq


def create_app() -> FastAPI:
    app = FastAPI(title="Cypher Tool Mock", version="0.1")

    @app.post("/execute")
    async def execute(body: ExecuteRequest) -> Any:
        started = time.perf_counter()
        params = dict(body.params or {})
        forced = params.pop("__error", None)
        if forced:
            code = str(forced).upper()
            _append_log(
                session_id=body.session_id,
                kind="CYPHER",
                query=body.query,
                params=params,
                status="FAILED",
                error=code,
            )
            return _error(code if code in ERROR_CODES else "INTERNAL_ERROR")

        if "SYNTAX_BROKEN" in body.query:
            _append_log(
                session_id=body.session_id,
                kind="CYPHER",
                query=body.query,
                params=params,
                status="FAILED",
                error="QUERY_SYNTAX",
            )
            return _error("QUERY_SYNTAX", "mock: syntax broken")
        if "DROP " in body.query.upper():
            return _error("QUERY_FORBIDDEN", "mock: write query rejected")

        rows = params.pop("__rows", None)
        columns = params.pop("__columns", None)
        truncate = bool(params.pop("__truncate", False))
        if rows is None:
            rows = [{"value": 1}]
        if columns is None:
            columns = (
                [{"name": key, "type": "string"} for key in rows[0]]
                if rows and isinstance(rows[0], dict)
                else []
            )
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        seq = _append_log(
            session_id=body.session_id,
            kind="CYPHER",
            query=body.query,
            params=params,
            status="SUCCESS",
            rows=len(rows),
            elapsed_ms=elapsed,
        )
        return _ok(
            {
                "columns": columns,
                "rows": rows[: body.max_rows],
                "row_count": body.max_rows if truncate else len(rows),
                "truncated": truncate,
                "elapsed_ms": elapsed,
                "query_id": f"q{seq}",
                "executor_version": "mock-0.1",
                "session_id": body.session_id,
            }
        )

    @app.get("/logs")
    async def logs(
        session_id: str | None = Query(default=None),
        since_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> Any:
        items = [
            item
            for item in STATE.logs
            if item["seq"] > since_seq
            and (session_id is None or item.get("session_id") == session_id)
        ]
        return _ok({"items": items[:limit], "next_seq": items[-1]["seq"] if items else since_seq})

    @app.get("/health")
    async def health() -> Any:
        return _ok({"status": "UP", "version": "mock-0.1"})

    @app.get("/meta")
    async def meta() -> Any:
        return _ok(
            {
                "version": "mock-0.1",
                "kinds": ["CYPHER"],
                "limits": {"max_rows": 200, "timeout_ms": 10_000},
            }
        )

    return app


app = create_app()
