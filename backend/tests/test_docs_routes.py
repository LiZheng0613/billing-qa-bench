"""接口文档路由回归测试：Swagger 必须挂在 /api/v1 下，否则反向代理无法访问。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from billing_qa.core.config import settings
from billing_qa.main import app

# 不使用 with 上下文，避免触发 lifespan（连接 Redis / 数据库）。
client = TestClient(app)


def test_swagger_ui_available_under_api_prefix() -> None:
    response = client.get(f"{settings.api_prefix}/docs")
    assert response.status_code == 200
    assert f"{settings.api_prefix}/openapi.json" in response.text


def test_openapi_schema_available_under_api_prefix() -> None:
    response = client.get(f"{settings.api_prefix}/openapi.json")
    assert response.status_code == 200
    assert response.json()["paths"]


def test_legacy_docs_path_redirects_to_api_prefix() -> None:
    response = client.get("/docs", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == f"{settings.api_prefix}/docs"
