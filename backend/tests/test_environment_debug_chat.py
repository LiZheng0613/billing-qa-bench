"""执行环境探活调试：选用例直发消息，不写执行记录。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import func, select, text

from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.execution import ExecutionRecord


async def _db_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用，跳过 API 集成测试")
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test/api/v1", timeout=60
        ) as http:
            response = await http.post(
                "/auth/login", json={"username": "admin", "password": "admin123"}
            )
            assert response.status_code == 200, response.text
            http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
            yield http


async def _start_target(received: list[dict]) -> tuple[asyncio.AbstractServer, int]:
    """启动一个最小 HTTP 目标，记录收到的请求体并返回固定答案。"""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw_head = await reader.readuntil(b"\r\n\r\n")
            head, _, rest = raw_head.partition(b"\r\n\r\n")
            content_length = 0
            for line in head.decode("latin-1").split("\r\n")[1:]:
                key, _, value = line.partition(":")
                if key.strip().lower() == "content-length":
                    content_length = int(value.strip())
            body = rest
            while len(body) < content_length:
                body += await reader.read(content_length - len(body))
            received.append(json.loads(body.decode("utf-8")) if body else {})
            payload = json.dumps(
                {
                    "data": {
                        "answer": "调试回复：账单没问题",
                        "session_id": "sess-from-target",
                        "intent": "BILL",
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_debug_chat_sends_message_without_persisting(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:8]
    received: list[dict] = []
    server, port = await _start_target(received)
    env_id: int | None = None
    case_id: int | None = None
    try:
        env = await client.post(
            "/environments",
            json={
                "code": f"PYTEST-DEBUG-ENV-{stamp}",
                "name": "探活调试环境",
                "base_url": f"http://127.0.0.1:{port}",
                "api_path": "/chat",
                "session_mode": "CLIENT_GENERATED",
                "body_template": {
                    "query": "{{query}}",
                    "conversationId": "{{session_id}}",
                    "user_id": "{{user_id}}",
                },
            },
        )
        assert env.status_code == 200, env.text
        env_id = env.json()["id"]

        case = await client.post(
            "/cases",
            json={
                "code": f"PYTEST-DEBUG-CASE-{stamp}",
                "name": "探活调试用例",
                "case_type": "SINGLE",
                "query": "帮我查一下 13800138000 的账单",
                "environment_id": env_id,
                "request_variables": {"user_id": "u-debug"},
            },
        )
        assert case.status_code == 200, case.text
        case_id = case.json()["id"]

        async with SessionLocal() as db:
            before = (
                await db.execute(
                    select(func.count())
                    .select_from(ExecutionRecord)
                    .where(ExecutionRecord.case_id == case_id)
                )
            ).scalar_one()

        response = await client.post(
            f"/environments/{env_id}/debug-chat",
            json={"case_id": case_id, "query": "调试一下 13800138000 的账单"},
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["ok"] is True
        assert body["persisted"] is False
        assert body["query"] == "调试一下 13800138000 的账单"
        assert body["final_answer"] == "调试回复：账单没问题"
        assert body["intent"] == "BILL"
        assert body["session_mode_resolved"] == "CLIENT_GENERATED"
        assert body["session_id"].startswith("preview-")

        # 请求报文按用例变量渲染，并真实发送到目标环境
        assert body["request_preview"]["body"]["query"] == "调试一下 13800138000 的账单"
        assert body["request_preview"]["body"]["user_id"] == "u-debug"
        assert body["request_preview"]["body"]["conversationId"].startswith("preview-")
        assert received and received[0]["user_id"] == "u-debug"
        assert received[0]["query"] == "调试一下 13800138000 的账单"

        async with SessionLocal() as db:
            after = (
                await db.execute(
                    select(func.count())
                    .select_from(ExecutionRecord)
                    .where(ExecutionRecord.case_id == case_id)
                )
            ).scalar_one()
        assert after == before
    finally:
        server.close()
        await server.wait_closed()
        if case_id is not None:
            await client.delete(f"/cases/{case_id}")
        if env_id is not None:
            await client.delete(f"/environments/{env_id}")
