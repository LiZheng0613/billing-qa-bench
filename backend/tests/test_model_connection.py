"""模型双接入方式（OpenAI 兼容 / OpenCode Serve）的单元测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from starlette.requests import Request

from billing_qa.api.v1.models import _compose_serve_base
from billing_qa.main import app
from billing_qa.models.enums import ModelConnectionType
from billing_qa.models.model_config import ModelConfig
from billing_qa.schemas.model_config import ModelConfigCreate
from billing_qa.services.llm.client import (
    LLMClient,
    ModelContext,
    _parse_sse_line,
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
