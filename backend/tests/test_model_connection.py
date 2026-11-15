"""模型双接入方式（OpenAI 兼容 / OpenCode Serve）的单元测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from starlette.requests import Request

from billing_qa.api.v1.models import _compose_serve_base, opencode_catalog
from billing_qa.core.crypto import encrypt_value
from billing_qa.core.exceptions import ValidationError
from billing_qa.main import app
from billing_qa.models.enums import ModelConnectionType
from billing_qa.models.model_config import ModelConfig
from billing_qa.schemas.model_config import ModelConfigCreate, OpenCodeCatalogRequest
from billing_qa.services.llm.client import (
    LLMClient,
    ModelContext,
    _parse_sse_line,
)
from billing_qa.services.llm.opencode_catalog import (
    CatalogAgent,
    CatalogModel,
    OpenCodeCatalog,
    parse_agents,
    parse_models,
)


def test_compose_serve_base_appends_port() -> None:
    assert _compose_serve_base("http://127.0.0.1", 4096) == "http://127.0.0.1:4096"
    assert _compose_serve_base("http://127.0.0.1/", 4096) == "http://127.0.0.1:4096"
    # 地址里已经带端口时不得重复拼接
    assert _compose_serve_base("http://127.0.0.1:5000", 4096) == "http://127.0.0.1:5000"
    assert _compose_serve_base("https://opencode.example.com", None) == (
        "https://opencode.example.com"
    )


def test_opencode_create_requires_serve_url() -> None:
    with pytest.raises(PydanticValidationError):
        ModelConfigCreate(
            code="OC-1",
            name="OpenCode 裁判",
            role="JUDGE",
            connection_type=ModelConnectionType.OPENCODE_SERVE,
            serve_url=None,
        )


def test_opencode_create_accepts_serve_config() -> None:
    payload = ModelConfigCreate(
        code="OC-1",
        name="OpenCode 裁判",
        role="JUDGE",
        connection_type=ModelConnectionType.OPENCODE_SERVE,
        serve_url="http://127.0.0.1/",
        serve_port=4096,
        opencode_agent="plan",
        opencode_model="anthropic/claude-sonnet-4",
    )
    assert payload.serve_url == "http://127.0.0.1"
    assert payload.serve_port == 4096
    assert payload.base_url is None


def test_openai_create_requires_base_url_and_model_name() -> None:
    with pytest.raises(PydanticValidationError):
        ModelConfigCreate(
            code="OA-1",
            name="OpenAI 裁判",
            role="JUDGE",
            connection_type=ModelConnectionType.OPENAI_COMPATIBLE,
            base_url=None,
            model_name="gpt-4o-mini",
        )
    with pytest.raises(PydanticValidationError):
        ModelConfigCreate(
            code="OA-1",
            name="OpenAI 裁判",
            role="JUDGE",
            connection_type=ModelConnectionType.OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:8001/v1",
            model_name=None,
        )


def test_parse_sse_line() -> None:
    assert _parse_sse_line(": keep-alive") is None
    assert _parse_sse_line("event: message") is None
    assert _parse_sse_line("data: not-json") is None
    assert _parse_sse_line("") is None
    event = _parse_sse_line('data: {"type":"session.text.delta","data":{"delta":"hi"}}')
    assert event is not None
    assert event["type"] == "session.text.delta"
    assert event["data"]["delta"] == "hi"


def test_model_context_opencode_fields() -> None:
    model = ModelConfig(
        id=7,
        code="OC-JUDGE",
        name="OpenCode 裁判",
        role="JUDGE",
        connection_type=ModelConnectionType.OPENCODE_SERVE.value,
        provider="opencode-serve",
        base_url="http://127.0.0.1:4096",
        model_name="anthropic/claude-sonnet-4",
        serve_url="http://127.0.0.1",
        serve_port=4096,
        serve_username="opencode",
        serve_password_encrypted=None,
        opencode_agent="plan",
        opencode_model="anthropic/claude-sonnet-4",
        opencode_directory="D:/workspace/qa",
        timeout_seconds=30,
        rpm_limit=10,
        max_retries=1,
        response_format_json=True,
    )
    context = ModelContext.from_orm(model)
    assert context.connection_type == ModelConnectionType.OPENCODE_SERVE.value
    assert context.serve_url == "http://127.0.0.1"
    assert context.serve_port == 4096
    assert context.opencode_agent == "plan"

    client = LLMClient(context)
    assert client.is_opencode is True
    assert client.opencode_base_url == "http://127.0.0.1:4096"
    assert client._session_payload() == {
        "title": "billing-qa · OC-JUDGE",
        "agent": "plan",
        "model": {"providerID": "anthropic", "id": "claude-sonnet-4"},
        "location": {"directory": "D:/workspace/qa"},
    }


def test_opencode_model_ref_requires_provider() -> None:
    model = ModelConfig(
        id=8,
        code="OC-BAD",
        name="OpenCode",
        role="JUDGE",
        connection_type=ModelConnectionType.OPENCODE_SERVE.value,
        base_url="http://127.0.0.1:4096",
        model_name="judge",
        serve_url="http://127.0.0.1",
        serve_port=4096,
        opencode_model="claude-sonnet-4",
        timeout_seconds=30,
        rpm_limit=10,
        max_retries=0,
        response_format_json=True,
    )
    client = LLMClient(ModelContext.from_orm(model))
    with pytest.raises(Exception, match="provider/model"):
        client._session_payload()


@pytest.mark.asyncio
async def test_validation_error_handler_returns_422_json() -> None:
    """模型校验失败必须返回 422，而不是被 ValueError 序列化问题改写成 500。"""
    error = ValueError("OpenCode Serve 接入需填写 serve_url")
    exc = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("body",),
                "msg": f"Value error, {error}",
                "input": {"code": "OC-BAD"},
                "ctx": {"error": error},
            }
        ]
    )
    request = Request(
        {"type": "http", "method": "POST", "path": "/api/v1/models", "headers": []}
    )
    handler = app.exception_handlers[RequestValidationError]
    response = await handler(request, exc)

    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["path"] == "/api/v1/models"
    assert "serve_url" in payload["detail"][0]["ctx"]["error"]


def test_parse_opencode_models_payload() -> None:
    payload = {
        "location": {"directory": "/tmp"},
        "data": [
            {
                "id": "gpt-4o",
                "modelID": "gpt-4o",
                "providerID": "openai",
                "name": "GPT-4o",
                "status": "active",
                "enabled": True,
                "limit": {"context": 128000, "output": 16384},
            },
            {
                "id": "claude-sonnet-4",
                "modelID": "claude-sonnet-4",
                "providerID": "anthropic",
                "name": "Claude Sonnet 4",
                "status": "beta",
                "enabled": True,
                "limit": {"context": 200000, "output": 64000},
            },
            # 同一 value 去重
            {"id": "gpt-4o", "providerID": "openai", "name": "duplicate"},
            # 缺 provider 的脏数据跳过
            {"id": "orphan", "name": "orphan"},
        ],
    }
    models = parse_models(payload)

    assert [model.value for model in models] == [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
    ]
    claude = models[0]
    assert claude.provider_id == "anthropic"
    assert claude.model_id == "claude-sonnet-4"
    assert claude.name == "Claude Sonnet 4"
    assert claude.context_limit == 200000
    assert claude.output_limit == 64000
    assert claude.status == "beta"


def test_parse_opencode_models_rejects_bad_payload() -> None:
    from billing_qa.services.llm.opencode_catalog import OpenCodeCatalogError

    with pytest.raises(OpenCodeCatalogError):
        parse_models({"data": "not-a-list"})


def test_parse_opencode_agents_sorted_and_deduped() -> None:
    agents = parse_agents(
        {
            "data": [
                {"id": "plan", "name": "Plan", "mode": "primary", "hidden": False},
                {"id": "build", "name": "Build", "mode": "primary", "hidden": False},
                {"id": "build", "name": "Build duplicate"},
                {"id": "secret", "name": "Secret", "mode": "subagent", "hidden": True},
                {"name": "no-id"},
            ]
        }
    )
    assert [agent.id for agent in agents] == ["build", "plan", "secret"]
    assert agents[0].name == "Build"
    assert agents[2].hidden is True
    # 格式异常时不应阻断模型选择
    assert parse_agents({"data": None}) == []


@pytest.mark.asyncio
async def test_opencode_catalog_reuses_saved_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = ModelConfig(
        id=99,
        code="OC-EDIT",
        name="OpenCode 裁判",
        role="JUDGE",
        connection_type=ModelConnectionType.OPENCODE_SERVE.value,
        base_url="http://127.0.0.1:8199",
        model_name="mock/judge-a",
        serve_url="http://127.0.0.1",
        serve_port=8199,
        serve_username="opencode",
        serve_password_encrypted=encrypt_value("s3cret"),
        opencode_directory="D:/tmp/qa",
        timeout_seconds=30,
        rpm_limit=10,
        max_retries=1,
        response_format_json=True,
    )

    captured: dict[str, object] = {}

    async def fake_fetch(serve_url: str, **kwargs: object) -> OpenCodeCatalog:
        captured["serve_url"] = serve_url
        captured.update(kwargs)
        return OpenCodeCatalog(
            serve_url=serve_url,
            models=[CatalogModel("mock", "judge", "Mock Judge")],
            agents=[CatalogAgent("build", "Build")],
        )

    monkeypatch.setattr("billing_qa.api.v1.models.fetch_opencode_catalog", fake_fetch)

    class StubDb:
        async def get(self, _model: object, _model_id: int) -> ModelConfig:
            return stored

    result = await opencode_catalog(
        OpenCodeCatalogRequest(model_id=99), db=StubDb()  # type: ignore[arg-type]
    )

    # 地址 + 端口由后端拼接，密码复用已保存的密文
    assert captured["serve_url"] == "http://127.0.0.1:8199"
    assert captured["username"] == "opencode"
    assert captured["password"] == "s3cret"
    assert captured["directory"] == "D:/tmp/qa"
    assert result.serve_url == "http://127.0.0.1:8199"
    assert result.models[0].value == "mock/judge"
    assert result.models[0].name == "Mock Judge"
    assert result.agents[0].id == "build"


@pytest.mark.asyncio
async def test_opencode_catalog_requires_serve_url() -> None:
    with pytest.raises(ValidationError):
        await opencode_catalog(OpenCodeCatalogRequest(), db=object())  # type: ignore[arg-type]
