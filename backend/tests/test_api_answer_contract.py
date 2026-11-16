"""答案契约字段的 REST 往返测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from billing_qa.db.session import SessionLocal
from billing_qa.main import app

pytestmark = pytest.mark.asyncio


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


async def test_contract_case_roundtrip(client: httpx.AsyncClient) -> None:
    code = f"CONTRACT-{uuid.uuid4().hex[:8]}"
    payload = {
        "code": code,
        "name": "契约模式用例",
        "case_type": "SINGLE",
        "query": "本期账单金额是多少？",
        "grading_mode": "ANSWER_CONTRACT",
        "input_type": "CLEAR",
        "as_of": "2026-10",
        "answer_envelope": {
            "must": [
                {"id": "amount", "kind": "scalar", "value": 123.45, "unit": "CNY"},
                {"id": "due", "kind": "date", "value": "2026-10-31"},
            ],
            "must_not": [{"id": "banned", "kind": "text", "value": "系统错误"}],
            "extras_policy": "lenient",
        },
    }
    created = await client.post("/cases", json=payload)
    assert created.status_code == 200, created.text
    data = created.json()
    case_id = data["id"]
    try:
        assert data["grading_mode"] == "ANSWER_CONTRACT"
        assert data["as_of"] == "2026-10"
        assert data["answer_envelope"]["must"][0]["value"] == 123.45

        updated = await client.put(f"/cases/{case_id}", json={"as_of": "2026-11"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["as_of"] == "2026-11"

        listed = await client.get("/cases", params={"keyword": code})
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert any(item["code"] == code for item in items)

        snapshot = (await client.get(f"/cases/{case_id}/revisions")).json()
        assert snapshot, "保存后必须生成版本快照"
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_contract_case_requires_as_of(client: httpx.AsyncClient) -> None:
    payload = {
        "code": f"CONTRACT-BAD-{uuid.uuid4().hex[:8]}",
        "name": "缺少冻结时间的契约用例",
        "case_type": "SINGLE",
        "query": "本期账单金额是多少？",
        "grading_mode": "ANSWER_CONTRACT",
        "as_of": None,
        "answer_envelope": {
            "must": [{"id": "amount", "kind": "scalar", "value": 1.0, "unit": "CNY"}]
        },
    }
    response = await client.post("/cases", json=payload)
    assert response.status_code == 422, response.text
