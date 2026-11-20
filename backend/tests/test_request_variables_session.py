"""用例级请求变量与多轮会话绑定：单元测试 + 预览接口集成测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select, text

from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.gold_query import FactResult
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.schemas.case import validate_request_variables
from billing_qa.services.adapter.session import (
    CLIENT_GENERATED,
    SERVER_RETURNED,
    init_session_id,
    resolve_session_mode,
    session_updates_from_response,
)
from billing_qa.services.engine.executor import CaseExecutor


# ---------------------------------------------------------------------- #
# 会话模式解析
# ---------------------------------------------------------------------- #
def test_resolve_session_mode_from_environment() -> None:
    # 会话方式只由环境决定，用例侧不再提供覆盖入口
    assert resolve_session_mode(CLIENT_GENERATED) == CLIENT_GENERATED
    assert resolve_session_mode(SERVER_RETURNED) == SERVER_RETURNED
    assert resolve_session_mode(None) == SERVER_RETURNED
    assert resolve_session_mode("BOGUS") == SERVER_RETURNED


def test_init_session_id_per_mode() -> None:
    assert init_session_id(SERVER_RETURNED, 7) is None
    generated = init_session_id(CLIENT_GENERATED, 7)
    assert generated is not None and generated.startswith("exec-7-")
    assert init_session_id(CLIENT_GENERATED, 7) != generated  # 每次执行独立


def test_session_updates_only_server_returned() -> None:
    assert session_updates_from_response(SERVER_RETURNED, "old", "new") == "new"
    assert session_updates_from_response(SERVER_RETURNED, "old", None) == "old"
    assert session_updates_from_response(CLIENT_GENERATED, "gen", "server") == "gen"


# ---------------------------------------------------------------------- #
# 请求变量校验
# ---------------------------------------------------------------------- #
def test_request_variable_validation_ok() -> None:
    value = {"user_id": "13800138000", "ext": {"vip": True}, "level": 3}
    assert validate_request_variables(value) == {
        "user_id": "13800138000",
        "ext": {"vip": True},
        "level": "3",
    }
    assert validate_request_variables(None) is None


def test_request_variable_numbers_are_stored_as_strings() -> None:
    value = {
        "phone": 13800138000123456789,
        "amount": 268.5,
        "profile": {"level": 3, "tags": [1, 2]},
    }
    assert validate_request_variables(value) == {
        "phone": "13800138000123456789",
        "amount": "268.5",
        "profile": {"level": "3", "tags": ["1", "2"]},
    }


def test_request_variable_validation_rejects_bad_key() -> None:
    with pytest.raises(ValueError):
        validate_request_variables({"1bad": "x"})
    with pytest.raises(ValueError):
        validate_request_variables({"bad-key": "x"})


def test_request_variable_validation_rejects_reserved() -> None:
    for key in ("query", "history", "history_text", "turn"):
        with pytest.raises(ValueError):
            validate_request_variables({key: "x"})


def test_request_variable_validation_rejects_oversize() -> None:
    with pytest.raises(ValueError):
        validate_request_variables({"blob": "x" * (17 * 1024)})


# ---------------------------------------------------------------------- #
# API 集成：报文预览
# ---------------------------------------------------------------------- #
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
async def test_preview_renders_case_variables(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:8]
    env_code = f"PYTEST-ENV-{stamp}"
    case_code = f"PYTEST-CASE-{stamp}"
    env = await client.post(
        "/environments",
        json={
            "code": env_code,
            "name": "预览测试环境",
            "base_url": "http://127.0.0.1:8002",
            "api_path": "/api/v1/chat",
            "session_mode": CLIENT_GENERATED,
            "headers_template": {"X-User-Id": "{{user_id}}"},
            "body_template": {
                "query": "{{query}}",
                "conversationId": "{{session_id}}",
                "user_id": "{{user_id}}",
                "channel": "{{channel}}",
            },
        },
    )
    assert env.status_code == 200, env.text
    env_id = env.json()["id"]
    assert env.json()["session_mode"] == CLIENT_GENERATED

    case = await client.post(
        "/cases",
        json={
            "code": case_code,
            "name": "预览测试用例",
            "case_type": "SINGLE",
            "query": "帮我查账单",
            "environment_id": env_id,
            "request_variables": {"user_id": "13800138000", "channel": "app"},
        },
    )
    assert case.status_code == 200, case.text
    case_id = case.json()["id"]
    assert case.json()["request_variables"] == {
        "user_id": "13800138000",
        "channel": "app",
    }

    try:
        preview = await client.post(f"/environments/{env_id}/preview", json={"case_id": case_id})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["session_mode_resolved"] == CLIENT_GENERATED
        assert body["body"]["query"] == "帮我查账单"
        assert body["body"]["user_id"] == "13800138000"
        assert body["body"]["channel"] == "app"
        assert body["body"]["conversationId"].startswith("preview-")
        assert body["headers"]["X-User-Id"] == "13800138000"
        assert body["origins"]["query"] == "RUNTIME"
        assert body["origins"]["session_id"] == "SESSION"
        assert body["origins"]["user_id"] == "CASE"
        assert body["warnings"] == []
    finally:
        await client.delete(f"/cases/{case_id}")
        await client.delete(f"/environments/{env_id}")


@pytest.mark.asyncio
async def test_reserved_request_variable_rejected(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:8]
    response = await client.post(
        "/cases",
        json={
            "code": f"PYTEST-RESERVED-{stamp}",
            "name": "保留字用例",
            "case_type": "SINGLE",
            "query": "查账单",
            "request_variables": {"query": "覆盖运行时"},
        },
    )
    assert response.status_code == 422, response.text

# ---------------------------------------------------------------------- #
# 执行器贯通：请求变量注入 headers/body + 客户端会话贯穿
# ---------------------------------------------------------------------- #
async def _mock_ready() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            return (await client.get("http://127.0.0.1:8002/health")).status_code == 200
    except Exception:
        return False


@pytest.mark.asyncio
async def test_executor_injects_case_variables_and_client_session() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用，跳过执行器集成测试")
    if not await _mock_ready():
        pytest.skip("Mock 被测系统不可用，跳过执行器集成测试")

    stamp = uuid.uuid4().hex[:8]
    async with SessionLocal() as db:
        demo = (
            await db.execute(select(TestCase).where(TestCase.code == "DEMO-SINGLE-001"))
        ).scalar_one_or_none()
        if demo is None:
            pytest.skip("演示用例 DEMO-SINGLE-001 未初始化")
        revision = (
            await db.execute(
                select(TestCaseRevision).where(
                    TestCaseRevision.case_id == demo.id,
                    TestCaseRevision.version == demo.current_version,
                )
            )
        ).scalar_one_or_none()
        if revision is None or not revision.snapshot:
            pytest.skip("演示用例缺少版本快照")
        snapshot = dict(revision.snapshot)

        env = Environment(
            code=f"PYTEST-SMOKE-ENV-{stamp}",
            name="请求变量冒烟环境",
            base_url="http://127.0.0.1:8002",
            health_path="/health",
            api_method="POST",
            api_path="/api/v1/chat",
            is_stream=False,
            session_mode=CLIENT_GENERATED,
            headers_template={"X-User-Id": "{{user_id}}"},
            body_template={
                "query": "{{query}}",
                "conversationId": "{{session_id}}",
                "user_id": "{{user_id}}",
                "channel": "{{channel}}",
                "history": "{{history}}",
            },
            extractors=None,
            timeout_seconds=20,
            verify_ssl=True,
        )
        db.add(env)
        await db.flush()
        case = TestCase(
            code=f"PYTEST-SMOKE-CASE-{stamp}",
            name="请求变量冒烟用例",
            case_type="SINGLE",
            status="ACTIVE",
            priority="P2",
            environment_id=env.id,
            query="帮我查一下 13800138000 本月账单总额",
            request_variables={"user_id": "13800138000", "channel": "smoke"},
        )
        db.add(case)
        await db.commit()
        case_id, env_id = int(case.id), int(env.id)

    snapshot.update(
        {
            "environment_id": env_id,
            "case_type": "SINGLE",
            "query": "帮我查一下 13800138000 本月账单总额",
            "request_variables": {"user_id": "13800138000", "channel": "smoke"},
            "grading_mode": "LEGACY",
            "expected_rules": None,
            "turns": None,
            "session_expected": None,
            "hidden_context": None,
        }
    )

    execution_id: int | None = None
    try:
        execution_id = await CaseExecutor().execute(
            case_id=case_id, case_version=1, snapshot=snapshot, source="DEBUG"
        )
        async with SessionLocal() as db:
            turns = (
                await db.execute(
                    select(TurnTrace)
                    .where(TurnTrace.execution_id == execution_id)
                    .order_by(TurnTrace.turn_index)
                )
            ).scalars().all()
            assert turns, "执行器应至少落库一轮对话轨迹"
            turn = turns[0]
            assert turn.session_id is not None
            assert turn.session_id.startswith(f"exec-{execution_id}-")

            payload = turn.request_payload or {}
            body = payload.get("body") or {}
            headers = payload.get("headers") or {}
            assert body.get("user_id") == "13800138000"
            assert body.get("channel") == "smoke", "用例变量应覆盖同名环境变量池取值"
            assert body.get("conversationId") == turn.session_id
            assert headers.get("X-User-Id") == "13800138000"
    finally:
        async with SessionLocal() as db:
            if execution_id is not None:
                await db.execute(
                    delete(FactResult).where(FactResult.execution_id == execution_id)
                )
                await db.execute(
                    delete(JudgeResult).where(JudgeResult.execution_id == execution_id)
                )
                await db.execute(
                    delete(TurnTrace).where(TurnTrace.execution_id == execution_id)
                )
                await db.execute(
                    delete(ExecutionRecord).where(ExecutionRecord.id == execution_id)
                )
            await db.execute(delete(TestCase).where(TestCase.id == case_id))
            await db.execute(delete(Environment).where(Environment.id == env_id))
            await db.commit()
