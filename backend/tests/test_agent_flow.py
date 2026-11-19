"""真实 Agent 对接流：Basic 鉴权 / 前置取 conversationId / 首轮 sessionid 回填 / 时间戳。"""

from __future__ import annotations

import base64
import re
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select, text

from billing_qa.core.crypto import encrypt_value
from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.gold_query import FactResult
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.schemas.case import validate_request_variables
from billing_qa.schemas.environment import PreHookConfig
from billing_qa.services.adapter.target_api import (
    EnvContext,
    basic_auth_header,
)
from billing_qa.services.engine.executor import CaseExecutor

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$")
STAMP = f"pytest-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------- #
# 单元：Basic 鉴权头与保留字
# ---------------------------------------------------------------------- #
def test_basic_auth_header_encoding() -> None:
    token = base64.b64encode(b"demo:s3cret").decode("ascii")
    assert basic_auth_header("demo", "s3cret") == f"Basic {token}"
    assert basic_auth_header("demo", None) == (
        "Basic " + base64.b64encode(b"demo:").decode("ascii")
    )
    assert basic_auth_header("", "s3cret") is None
    assert basic_auth_header(None, None) is None


def test_timestamp_iso_is_reserved() -> None:
    with pytest.raises(ValueError):
        validate_request_variables({"timestamp_iso": "2026-11-17T10:00:00.000"})
    # 其余变量不受影响
    assert validate_request_variables({"userid": "13800138000"}) == {
        "userid": "13800138000"
    }


def test_pre_hook_extract_validation() -> None:
    config = PreHookConfig(
        enabled=True,
        path="/api/v1/conversation",
        extract={"conversationId": "$.data.conversationId"},
    )
    assert config.extract == {"conversationId": "$.data.conversationId"}
    with pytest.raises(ValidationError):
        PreHookConfig(enabled=True, extract={"bad-key": "$.data.x"})
    with pytest.raises(ValidationError):
        PreHookConfig(enabled=True, extract={"conversationId": ""})


def test_env_context_decrypts_basic_auth() -> None:
    env = Environment(
        code=f"ENV-{STAMP}",
        name="basic 解密",
        base_url="http://127.0.0.1:8002",
        basic_auth={
            "username": "demo",
            "password_encrypted": encrypt_value("s3cret"),
        },
    )
    ctx = EnvContext.from_orm(env)
    assert ctx.basic_username == "demo"
    assert ctx.basic_password == "s3cret"
    assert basic_auth_header(ctx.basic_username, ctx.basic_password) is not None


# ---------------------------------------------------------------------- #
# 集成：预览接口（Basic 掩码、时间戳、前置变量来源）
# ---------------------------------------------------------------------- #
async def _db_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _mock_ready() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            return (await client.get("http://127.0.0.1:8002/health")).status_code == 200
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
async def test_preview_basic_auth_and_prehook_origin(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:8]
    env_code = f"PYTEST-AGENT-ENV-{stamp}"
    case_code = f"PYTEST-AGENT-CASE-{stamp}"
    env = await client.post(
        "/environments",
        json={
            "code": env_code,
            "name": "Agent 流预览环境",
            "base_url": "http://127.0.0.1:8002",
            "api_path": "/api/v1/agent/chat",
            "session_mode": "SERVER_RETURNED",
            "basic_auth": {"username": "demo", "password": "s3cret"},
            "pre_hook": {
                "enabled": True,
                "method": "POST",
                "path": "/api/v1/conversation",
                "body": {"userid": "{{userid}}", "custid": "{{custid}}"},
                "extract": {"conversationId": "$.data.conversationId"},
            },
            "body_template": {
                "conversationId": "{{conversationId}}",
                "message": {
                    "content": "{{query}}",
                    "type": "text",
                    "sessionid": "{{session_id}}",
                    "metadata": {
                        "timestampe": "{{timestamp_iso}}",
                        "requestcontext": "userid:{{userid}},custid:{{custid}},beid:101",
                    },
                },
            },
        },
    )
    assert env.status_code == 200, env.text
    env_body = env.json()
    env_id = env_body["id"]
    assert env_body["basic_auth"] == {
        "username": "demo",
        "password_set": True,
        "password": "s3cret",
    }

    case = await client.post(
        "/cases",
        json={
            "code": case_code,
            "name": "Agent 流预览用例",
            "case_type": "SINGLE",
            "query": "帮我查一下本月账单",
            "environment_id": env_id,
            "request_variables": {"userid": "13800138000", "custid": "C0001"},
            "session_mode": "SERVER_RETURNED",
        },
    )
    assert case.status_code == 200, case.text
    case_id = case.json()["id"]

    try:
        preview = await client.post(
            f"/environments/{env_id}/preview", json={"case_id": case_id}
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["origins"]["timestamp_iso"] == "RUNTIME"
        assert body["origins"]["conversationId"] == "PREHOOK"
        assert body["origins"]["session_id"] == "SESSION"
        assert body["origins"]["userid"] == "CASE"
        assert ISO_RE.match(body["body"]["message"]["metadata"]["timestampe"]), body
        assert (
            body["body"]["message"]["metadata"]["requestcontext"]
            == "userid:13800138000,custid:C0001,beid:101"
        )
        assert body["body"]["conversationId"] == "<前置接口注入:conversationId>"
        # Basic 鉴权头自动补齐并掩码
        assert body["headers"]["Authorization"] == "***"
        assert any("Basic 鉴权" in item for item in body["warnings"])

        # 编辑时留空密码表示沿用旧密码
        updated = await client.put(
            f"/environments/{env_id}",
            json={
                "basic_auth": {"username": "demo", "password": ""},
                "pre_hook": {
                    "enabled": True,
                    "method": "POST",
                    "path": "/api/v1/conversation",
                    "body": {"userid": "{{userid}}", "custid": "{{custid}}"},
                    "extract": {"conversationId": "$.data.conversationId"},
                },
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["basic_auth"] == {
            "username": "demo",
            "password_set": True,
            "password": "s3cret",
        }
    finally:
        await client.delete(f"/cases/{case_id}")
        await client.delete(f"/environments/{env_id}")


# ---------------------------------------------------------------------- #
# Mock 行为：Basic 鉴权缺失即 401
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mock_agent_endpoints_require_basic_auth() -> None:
    if not await _mock_ready():
        pytest.skip("Mock 被测系统不可用")
    headers = {"Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as http:
        conv = await http.post(
            "http://127.0.0.1:8002/api/v1/conversation",
            headers=headers,
            json={"userid": "13800138000"},
        )
        assert conv.status_code == 401, conv.text
        chat = await http.post(
            "http://127.0.0.1:8002/api/v1/agent/chat",
            headers=headers,
            json={
                "conversationId": "conv-x",
                "message": {"content": "查账单", "sessionid": ""},
            },
        )
        assert chat.status_code == 401, chat.text


# ---------------------------------------------------------------------- #
# 执行器贯通：前置 conversationId + Basic 鉴权 + 首轮空 sessionid 回填
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_executor_agent_flow_two_turns() -> None:
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
            code=f"PYTEST-AGENT-SMOKE-ENV-{stamp}",
            name="Agent 流冒烟环境",
            base_url="http://127.0.0.1:8002",
            health_path="/health",
            api_method="POST",
            api_path="/api/v1/agent/chat",
            is_stream=False,
            session_mode="SERVER_RETURNED",
            basic_auth={"username": "demo", "password_encrypted": encrypt_value("s3cret")},
            pre_hook={
                "enabled": True,
                "method": "POST",
                "path": "/api/v1/conversation",
                "body": {"userid": "{{userid}}", "custid": "{{custid}}"},
                "extract": {"conversationId": "$.data.conversationId"},
            },
            headers_template={"X-Client": "pytest-agent"},
            body_template={
                "conversationId": "{{conversationId}}",
                "message": {
                    "content": "{{query}}",
                    "type": "text",
                    "xxx": "固定值",
                    "agent": "opencode",
                    "model": "ds-xxx",
                    "sessionid": "{{session_id}}",
                    "metadata": {
                        "timestampe": "{{timestamp_iso}}",
                        "requestcontext": "userid:{{userid}},custid:{{custid}},beid:101",
                    },
                },
            },
            extractors={
                "final_answer": "$.data.answer",
                "session_id": "$.data.sessionid",
            },
            timeout_seconds=20,
            verify_ssl=True,
        )
        db.add(env)
        await db.flush()
        case = TestCase(
            code=f"PYTEST-AGENT-SMOKE-CASE-{stamp}",
            name="Agent 流两轮用例",
            case_type="STATIC_MULTI",
            status="ACTIVE",
            priority="P2",
            environment_id=env.id,
            turns=[
                {"query": "帮我查一下 13800138000 本月账单总额"},
                {"query": "那费用都包含哪些项目？"},
            ],
            request_variables={"userid": "13800138000", "custid": "C0001"},
        )
        db.add(case)
        await db.commit()
        case_id, env_id = int(case.id), int(env.id)

    snapshot.update(
        {
            "environment_id": env_id,
            "case_type": "STATIC_MULTI",
            "query": None,
            "request_variables": {"userid": "13800138000", "custid": "C0001"},
            "grading_mode": "LEGACY",
            "expected_rules": None,
            "turns": [
                {"query": "帮我查一下 13800138000 本月账单总额", "expected_rules": None},
                {"query": "那费用都包含哪些项目？", "expected_rules": None},
            ],
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
                (
                    await db.execute(
                        select(TurnTrace)
                        .where(TurnTrace.execution_id == execution_id)
                        .order_by(TurnTrace.turn_index)
                    )
                )
                .scalars()
                .all()
            )
            assert len(turns) == 2, f"应产生两轮轨迹，实际 {len(turns)}"

            first, second = turns
            first_body = (first.request_payload or {}).get("body") or {}
            second_body = (second.request_payload or {}).get("body") or {}
            first_message = first_body.get("message") or {}
            second_message = second_body.get("message") or {}

            # 前置接口注入的 conversationId 两轮一致
            assert first_body["conversationId"].startswith("conv-")
            assert second_body["conversationId"] == first_body["conversationId"]

            # 首轮 sessionid 为空字符串，响应回填后次轮携带同一值
            assert first_message["sessionid"] == ""
            assert second_message["sessionid"] == first.session_id
            assert second_message["sessionid"]

            # 用例变量注入 metadata.requestcontext
            assert (
                first_message["metadata"]["requestcontext"]
                == "userid:13800138000,custid:C0001,beid:101"
            )
            # 时间戳为 ISO 字符串且每轮刷新
            assert ISO_RE.match(first_message["metadata"]["timestampe"]), first_message
            assert ISO_RE.match(second_message["metadata"]["timestampe"]), second_message

            # 请求头透传
            assert (first.request_payload or {}).get("headers", {}).get("X-Client") == (
                "pytest-agent"
            )

            # Basic 鉴权实际到达 Mock
            for turn in turns:
                received = (
                    (turn.response_raw or {}).get("body", {}).get("data", {}).get("received")
                    or {}
                )
                assert received.get("basic_auth_user") == "demo"
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
