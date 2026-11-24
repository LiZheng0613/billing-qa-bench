"""Mock 取数执行器（8003）契约测试。

覆盖 ``POST /execute`` 的正常取数、参数校验、写操作拦截、故障注入与
``/meta`` / ``/logs`` 元信息，保证演示链路与 ``CypherToolExecutor`` 协议一致。
"""

from __future__ import annotations

import httpx
import pytest

from mock_servers.capability_api import app

QUERY_WITH_MARKER = "MATCH (s:Subscriber {phone:$phone}) RETURN s // capability:__KEY__"


def _payload(key: str = "bill_current", **params: object) -> dict[str, object]:
    merged: dict[str, object] = {"phone": "13800138000"}
    merged.update(params)
    return {"query": QUERY_WITH_MARKER.replace("__KEY__", key), "params": merged}


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as http:
        yield http


async def test_health_and_meta(client: httpx.AsyncClient) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "mock-cypher-tool"

    meta = (await client.get("/meta")).json()
    assert meta["read_only"] is True
    assert meta["tool_version"] == "mock-cypher-v0.1"
    # 与数据源和能力种子保持同一份能力清单
    assert "bill_current" in meta["capabilities"]
    assert len(meta["capabilities"]) == 14


@pytest.mark.parametrize(
    ("key", "column"),
    [
        ("bill_current", "bill_total"),
        ("bill_items", "amount"),
        ("balance_account", "available_balance"),
        ("points_info", "available_points"),
        ("package_contract", "contract_until"),
    ],
)
async def test_execute_returns_expected_columns(
    client: httpx.AsyncClient, key: str, column: str
) -> None:
    response = await client.post("/execute", json=_payload(key))
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "OK"
    data = body["data"]
    assert data["row_count"] >= 1
    assert column in data["columns"]
    assert data["rows"][0]["phone"] == "13800138000"


async def test_execute_rejects_unknown_capability(client: httpx.AsyncClient) -> None:
    response = await client.post("/execute", json=_payload("no_such_capability"))
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "no_such_capability" in body["message"]


async def test_execute_requires_capability_marker(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/execute", json={"query": "MATCH (s) RETURN s", "params": {"phone": "13800138000"}}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


async def test_execute_requires_phone(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/execute",
        json={"query": QUERY_WITH_MARKER.replace("__KEY__", "bill_current"), "params": {}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "PARAM_MISSING"


async def test_execute_blocks_write_queries(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/execute",
        json={
            "query": "MATCH (s) SET s.balance = 0 // capability:balance_account",
            "params": {"phone": "13800138000"},
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "QUERY_FORBIDDEN"


@pytest.mark.parametrize(
    ("marker", "status", "code"),
    [("TIMEOUT", 504, "TIMEOUT"), ("INTERNAL", 502, "INTERNAL_ERROR")],
)
async def test_execute_supports_error_injection(
    client: httpx.AsyncClient, marker: str, status: int, code: str
) -> None:
    response = await client.post("/execute", json=_payload("bill_current", __error=marker))
    assert response.status_code == status
    assert response.json()["code"] == code


async def test_execute_returns_empty_rows_for_unknown_subscriber(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/execute", json=_payload("bill_current", phone="13900000000"))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["rows"] == []
    assert data["row_count"] == 0


async def test_logs_records_recent_calls(client: httpx.AsyncClient) -> None:
    payload = _payload("invoice_info")
    payload["session_id"] = "sess-demo"
    await client.post("/execute", json=payload)
    logs = (await client.get("/logs", params={"session_id": "sess-demo"})).json()
    assert logs["items"]
    assert logs["items"][-1]["status"] == "SUCCESS"
    assert logs["items"][-1]["session_id"] == "sess-demo"
