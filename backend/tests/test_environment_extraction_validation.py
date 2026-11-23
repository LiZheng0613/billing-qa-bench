"""环境 API 保存期校验：条件规则对象非法时 422，合法时正常保存并原样返回。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from billing_qa.db.session import SessionLocal
from billing_qa.main import app

VALID_EXTRACTORS = {
    "final_answer": {
        "rules": [
            {
                "id": "if-last-chart",
                "path": "$..[?(@.type=='chart')]",
                "occurrence": "last",
                "take": ["$.message", "$..message"],
                "take_parent": ["$.message"],
            },
            {"path": "$..context", "occurrence": "last"},
        ],
        "fallback_content": True,
    },
    "session_id": "$.data.sessionid",
}


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


@pytest.mark.asyncio
async def test_create_environment_rejects_invalid_spec(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:8]
    created: list[int] = []
    try:
        # 非法规则：JSONPath 语法错误 -> 422，错误信息定位到具体规则
        response = await client.post(
            "/environments",
            json={
                "code": f"PYTEST-EXTRACT-BAD-{stamp}",
                "name": "非法条件规则环境",
                "base_url": "http://127.0.0.1:9",
                "extractors": {
                    "final_answer": {"rules": [{"path": "not valid {"}]}
                },
            },
        )
        assert response.status_code == 422
        assert "final_answer" in response.text

        # 非法规则：相对路径缺 $ 前缀 -> 422
        response = await client.post(
            "/environments",
            json={
                "code": f"PYTEST-EXTRACT-BAD2-{stamp}",
                "name": "非法相对路径环境",
                "base_url": "http://127.0.0.1:9",
                "extractors": {
                    "final_answer": {"rules": [{"path": "$.a", "take": ["message"]}]}
                },
            },
        )
        assert response.status_code == 422

        # 字符串形态语法非法：与历史一致，保存不阻断 -> 200
        response = await client.post(
            "/environments",
            json={
                "code": f"PYTEST-EXTRACT-STR-{stamp}",
                "name": "历史字符串环境",
                "base_url": "http://127.0.0.1:9",
                "extractors": {"final_answer": "not valid {"},
            },
        )
        assert response.status_code == 200, response.text
        created.append(response.json()["id"])

        # 合法条件规则：200，且对象形态原样返回（不被字符串化）
        response = await client.post(
            "/environments",
            json={
                "code": f"PYTEST-EXTRACT-OK-{stamp}",
                "name": "条件规则环境",
                "base_url": "http://127.0.0.1:9",
                "extractors": VALID_EXTRACTORS,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        created.append(body["id"])
        assert isinstance(body["extractors"]["final_answer"], dict)
        assert body["extractors"]["final_answer"]["rules"][0]["id"] == "if-last-chart"
        assert body["extractors"]["session_id"] == "$.data.sessionid"

        # 更新同样走校验（PUT）
        env_id = created[-1]
        response = await client.put(
            f"/environments/{env_id}",
            json={
                "name": "条件规则环境",
                "base_url": "http://127.0.0.1:9",
                "extractors": {
                    "final_answer": {"rules": [{"path": "$.a", "occurrence": "middle"}]}
                },
            },
        )
        assert response.status_code == 422
    finally:
        for env_id in created:
            await client.delete(f"/environments/{env_id}")
