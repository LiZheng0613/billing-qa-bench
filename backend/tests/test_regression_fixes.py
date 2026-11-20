"""缺陷修复回归：动作级 RBAC、账号锁定恢复、删除引用保护与 SANDBOX 统计口径。

依赖本机 PostgreSQL / Redis；不可用时自动跳过。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.enums import TaskScheduleType, TaskStatus, UserStatus
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.user import SysRole, SysUser
from billing_qa.services.auth.service import authenticate, create_user

pytestmark = pytest.mark.asyncio


async def _db_ready() -> bool:
    from sqlalchemy import text

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
            transport=transport, base_url="http://test/api/v1", timeout=120
        ) as http:
            response = await http.post(
                "/auth/login", json={"username": "admin", "password": "admin123"}
            )
            assert response.status_code == 200, response.text
            http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
            yield http


async def _create_user_with_role(username: str, password: str, role_code: str) -> int:
    async with SessionLocal() as db:
        role = (
            await db.execute(select(SysRole).where(SysRole.code == role_code))
        ).scalar_one_or_none()
        assert role is not None, f"内置角色 {role_code} 未初始化"
        user, _ = await create_user(
            db,
            username=username,
            display_name=username,
            email=None,
            phone=None,
            password=password,
            role_ids=[int(role.id)],
            actor=None,
        )
        await db.commit()
        return int(user.id)


async def _login_token(
    client: httpx.AsyncClient, username: str, password: str
) -> str:
    response = await client.post(
        "/auth/login",
        json={"username": username, "password": password},
        headers={"Authorization": ""},  # 覆盖管理员默认头
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _cleanup_user(user_id: int) -> None:
    from billing_qa.models.user import sys_user_role

    async with SessionLocal() as db:
        await db.execute(delete(sys_user_role).where(sys_user_role.c.user_id == user_id))
        await db.execute(delete(SysUser).where(SysUser.id == user_id))
        await db.commit()


# --------------------------------------------------------------------- RBAC
async def test_viewer_read_allowed_write_forbidden(client: httpx.AsyncClient) -> None:
    """VIEWER 可读模块，但所有写端点与导出端点都返回 403。"""
    stamp = uuid.uuid4().hex[:6]
    username = f"viewer_{stamp}"
    user_id = await _create_user_with_role(username, "Viewer#12345", "VIEWER")
    token = await _login_token(client, username, "Viewer#12345")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        assert (await client.get("/cases", headers=headers)).status_code == 200
        assert (await client.get("/tasks", headers=headers)).status_code == 200

        # 写端点 403（动作级权限）
        assert (
            await client.post(
                "/cases",
                json={
                    "code": f"RBAC-{stamp}",
                    "name": "越权用例",
                    "case_type": "SINGLE",
                    "priority": "P2",
                    "query": "q",
                    "expected_answer": "a",
                },
                headers=headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                "/tasks",
                json={"name": "越权任务", "case_scope": "ALL_ACTIVE", "case_ids": []},
                headers=headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                "/prompts",
                json={
                    "code": f"RBAC-TPL-{stamp}",
                    "name": "越权模板",
                    "template_type": "USER_SIMULATOR",
                    "content": "x",
                    "changelog": "x",
                },
                headers=headers,
            )
        ).status_code == 403

        # 导出是 GET，模块级只校验读权限，动作级必须拦住 case:export
        assert (await client.get("/cases/export", headers=headers)).status_code == 403
    finally:
        await _cleanup_user(user_id)


async def test_case_operator_cannot_write_capabilities(client: httpx.AsyncClient) -> None:
    """CASE_OPERATOR 只有 capability:execute：可读取数资产，但不能新增数据源。"""
    stamp = uuid.uuid4().hex[:6]
    username = f"caseop_{stamp}"
    user_id = await _create_user_with_role(username, "Caseop#12345", "CASE_OPERATOR")
    token = await _login_token(client, username, "Caseop#12345")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        assert (
            await client.get("/data-sources", headers=headers)
        ).status_code == 200
        # 模块写集合包含 capability:execute（探活需要），但新增数据源
        # 是真正的写动作，必须由 capability:write 拦截。
        assert (
            await client.post(
                "/data-sources",
                json={
                    "code": f"RBAC-DS-{stamp}",
                    "name": "越权数据源",
                    "kind": "CYPHER_TOOL",
                    "endpoint_url": "http://127.0.0.1:9",
                    "timeout_seconds": 1,
                },
                headers=headers,
            )
        ).status_code == 403
    finally:
        await _cleanup_user(user_id)


async def test_me_returns_must_change_password(client: httpx.AsyncClient) -> None:
    """/auth/me 的必须改密标记来自数据库，而不是固定 False。"""
    stamp = uuid.uuid4().hex[:6]
    username = f"mustcp_{stamp}"
    user_id = await _create_user_with_role(username, "Mustcp#12345", "VIEWER")
    token = await _login_token(client, username, "Mustcp#12345")
    try:
        me = (
            await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        ).json()
        assert me["must_change_password"] is True
    finally:
        await _cleanup_user(user_id)


# --------------------------------------------------------------------- 账号锁定恢复
async def test_locked_account_recovers_after_expiry(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:6]
    username = f"locked_{stamp}"
    password = "Locked#12345"
    user_id = await _create_user_with_role(username, password, "VIEWER")
    try:
        # 锁定窗口内：登录被拒
        async with SessionLocal() as db:
            user = await db.get(SysUser, user_id)
            user.status = UserStatus.LOCKED.value
            user.failed_login_count = 5
            user.locked_until = datetime.now() + timedelta(minutes=10)
            await db.commit()
        with pytest.raises(Exception) as exc_info:
            async with SessionLocal() as db:
                await authenticate(db, username, "wrong-password")
        assert "锁定" in str(exc_info.value)

        # 锁定过期后：自动恢复并允许正常登录
        async with SessionLocal() as db:
            user = await db.get(SysUser, user_id)
            user.locked_until = datetime.now() - timedelta(minutes=1)
            await db.commit()
        async with SessionLocal() as db:
            result = await authenticate(db, username, password)
            assert result.username == username
        async with SessionLocal() as db:
            user = await db.get(SysUser, user_id)
            assert user.status == UserStatus.ACTIVE.value
            assert user.failed_login_count == 0
            assert user.locked_until is None
    finally:
        await _cleanup_user(user_id)


# --------------------------------------------------------------------- 删除引用保护
async def test_delete_environment_blocked_by_references(
    client: httpx.AsyncClient,
) -> None:
    stamp = uuid.uuid4().hex[:6]
    env = (
        await client.post(
            "/environments",
            json={
                "code": f"RBAC-ENV-{stamp}",
                "name": "引用保护环境",
                "base_url": "http://127.0.0.1:9",
                "variables": [],
            },
        )
    ).json()
    case = (
        await client.post(
            "/cases",
            json={
                "code": f"RBAC-ENV-CASE-{stamp}",
                "name": "引用环境用例",
                "case_type": "SINGLE",
                "priority": "P2",
                "environment_id": env["id"],
                "query": "q",
                "expected_answer": "a",
            },
        )
    ).json()
    try:
        blocked = await client.delete(f"/environments/{env['id']}")
        assert blocked.status_code == 409
        assert "引用" in blocked.json()["message"]
    finally:
        await client.delete(f"/cases/{case['id']}")
        removed = await client.delete(f"/environments/{env['id']}")
        assert removed.status_code == 200


async def test_delete_model_blocked_by_case_reference(
    client: httpx.AsyncClient,
) -> None:
    stamp = uuid.uuid4().hex[:6]
    model = (
        await client.post(
            "/models",
            json={
                "code": f"RBAC-MODEL-{stamp}",
                "name": "引用保护模型",
                "role": "JUDGE",
                "connection_type": "OPENAI_COMPATIBLE",
                "base_url": "http://127.0.0.1:9/v1",
                "model_name": "mock-judge",
                "is_active": False,
            },
        )
    ).json()
    case = (
        await client.post(
            "/cases",
            json={
                "code": f"RBAC-MODEL-CASE-{stamp}",
                "name": "引用裁判模型用例",
                "case_type": "SINGLE",
                "priority": "P2",
                "query": "q",
                "expected_answer": "a",
                "judge_model_ids": [model["id"]],
            },
        )
    ).json()
    try:
        blocked = await client.delete(f"/models/{model['id']}")
        assert blocked.status_code == 409
        assert "引用" in blocked.json()["message"]
    finally:
        await client.delete(f"/cases/{case['id']}")
        removed = await client.delete(f"/models/{model['id']}")
        assert removed.status_code == 200


async def test_delete_active_simulator_model_blocked(
    client: httpx.AsyncClient,
) -> None:
    """直接插入启用的模拟器模型（绕过唯一性校验），删除必须被拒绝。"""
    from billing_qa.models.model_config import ModelConfig

    async with SessionLocal() as db:
        model = ModelConfig(
            code=f"RBAC-SIM-{uuid.uuid4().hex[:6]}",
            name="启用中的模拟器模型",
            role="SIMULATOR",
            connection_type="OPENAI_COMPATIBLE",
            base_url="http://127.0.0.1:9/v1",
            model_name="mock-sim",
            is_active=True,
        )
        db.add(model)
        await db.commit()
        model_id = int(model.id)
    try:
        blocked = await client.delete(f"/models/{model_id}")
        assert blocked.status_code == 409
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(ModelConfig, model_id))
            await db.commit()


# --------------------------------------------------------------------- SANDBOX 口径
async def test_pass_rate_excludes_sandbox(client: httpx.AsyncClient) -> None:
    cases = (await client.get("/cases", params={"page_size": 1})).json()
    if not cases["items"]:
        pytest.skip("没有可用用例")
    case_id = cases["items"][0]["id"]
    case_version = cases["items"][0]["current_version"]

    baseline = (await client.get("/executions/pass-rate", params={"hours": 1})).json()

    async with SessionLocal() as db:
        sandbox = ExecutionRecord(
            trace_uid=uuid.uuid4().hex,
            case_id=case_id,
            case_version=case_version,
            case_snapshot={},
            source="SANDBOX",
            status="COMPLETED",
            pass_flag=True,
            started_at=datetime.now(),
        )
        debug = ExecutionRecord(
            trace_uid=uuid.uuid4().hex,
            case_id=case_id,
            case_version=case_version,
            case_snapshot={},
            source="DEBUG",
            status="COMPLETED",
            pass_flag=True,
            started_at=datetime.now(),
        )
        db.add_all([sandbox, debug])
        await db.commit()
        sandbox_id, debug_id = sandbox.id, debug.id
    try:
        after = (await client.get("/executions/pass-rate", params={"hours": 1})).json()
        # DEBUG 计入，SANDBOX 不计入
        assert after["measured"] == baseline["measured"] + 1
        assert after["passed"] == baseline["passed"] + 1
        assert after["total"] == baseline["total"] + 1
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ExecutionRecord).where(
                    ExecutionRecord.id.in_([sandbox_id, debug_id])
                )
            )
            await db.commit()


# --------------------------------------------------------------------- WS 鉴权口径
async def test_resolve_token_user_matches_rest_auth(client: httpx.AsyncClient) -> None:
    """WS 连接复用 REST 的用户校验：禁用 / 顶号后的旧 Token 被拒绝。"""
    from fastapi import HTTPException

    from billing_qa.core.security import decode_access_token, resolve_token_user

    stamp = uuid.uuid4().hex[:6]
    username = f"wsuser_{stamp}"
    user_id = await _create_user_with_role(username, "Ws#12345", "VIEWER")
    try:
        token = await _login_token(client, username, "Ws#12345")
        payload = decode_access_token(token)
        resolved = await resolve_token_user(payload)
        assert resolved.id == user_id

        # 禁用账号 + 顶号（token_version 递增）后，同 Token 必须被拒绝：
        # REST 每个请求都校验，WS 建连时同样校验，杜绝长连接绕过。
        async with SessionLocal() as db:
            db_user = await db.get(SysUser, user_id)
            assert db_user is not None
            db_user.status = UserStatus.DISABLED.value
            db_user.token_version += 1
            await db.commit()

        with pytest.raises(HTTPException) as exc_info:
            await resolve_token_user(payload)
        assert exc_info.value.status_code == 401
    finally:
        await _cleanup_user(user_id)


# --------------------------------------------------------------------- 解锁语义
async def test_unlock_cannot_reenable_disabled_account(
    client: httpx.AsyncClient,
) -> None:
    """解锁只恢复「锁定」；已禁用账号不能被解锁接口复活。"""
    stamp = uuid.uuid4().hex[:6]
    username = f"unlock_{stamp}"
    user_id = await _create_user_with_role(username, "Unlock#12345", "VIEWER")
    try:
        # 管理员禁用账号
        response = await client.patch(
            f"/users/{user_id}", json={"status": "DISABLED"}
        )
        assert response.status_code == 200, response.text

        # 解锁接口应拒绝：禁用是明确处置，需走「编辑账号状态」恢复
        response = await client.post(f"/users/{user_id}/unlock")
        assert response.status_code == 409, response.text
        assert "message" in response.json()

        async with SessionLocal() as db:
            db_user = await db.get(SysUser, user_id)
            assert db_user is not None
            assert db_user.status == UserStatus.DISABLED.value

        # 正向路径：锁定账号可解锁恢复
        async with SessionLocal() as db:
            db_user = await db.get(SysUser, user_id)
            assert db_user is not None
            db_user.status = UserStatus.LOCKED.value
            db_user.locked_until = datetime.now() + timedelta(minutes=10)
            db_user.failed_login_count = 5
            await db.commit()

        response = await client.post(f"/users/{user_id}/unlock")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ACTIVE"
    finally:
        await _cleanup_user(user_id)


# --------------------------------------------------------------------- 任务状态机
async def test_retry_failed_rejected_by_lock_keeps_status(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动被拒（他实例持锁）时任务状态不被遗留为 RUNNING。

    旧实现「先落库 RUNNING 再 start」：start 返回 False 时 DB 已翻成
    RUNNING 却无人执行，靠恢复扫描兜底。现在状态翻转统一由 worker 执行。
    """
    cases = (await client.get("/cases", params={"page_size": 1})).json()
    if not cases["items"]:
        pytest.skip("没有可用用例")
    case_id = cases["items"][0]["id"]
    case_version = cases["items"][0]["current_version"]

    async def fake_start(*_: object, **__: object) -> bool:
        return False

    monkeypatch.setattr(
        "billing_qa.services.engine.scheduler.scheduler.start", fake_start
    )
    async with SessionLocal() as db:
        task = BatchTask(
            code=f"PYTEST-RETRY-{uuid.uuid4().hex[:8]}",
            name="pytest 重跑被拒",
            status=TaskStatus.COMPLETED,
            control_state="FINISHED",
            case_ids=[case_id],
            total_cases=1,
            finished_at=datetime.now(),
        )
        db.add(task)
        await db.flush()
        db.add(
            ExecutionRecord(
                trace_uid=uuid.uuid4().hex,
                task_id=task.id,
                case_id=case_id,
                case_version=case_version,
                case_snapshot={},
                source="BATCH",
                status="FAIL",
                attempt_no=1,
                pass_flag=False,
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
        )
        await db.commit()
        task_id = task.id
    try:
        response = await client.post(f"/tasks/{task_id}/retry-failed")
        assert response.status_code == 409, response.text
        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            assert task is not None
            assert task.status == TaskStatus.COMPLETED  # 未被遗留为 RUNNING
            assert task.control_state == "FINISHED"
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(ExecutionRecord).where(ExecutionRecord.task_id == task_id)
            )
            await db.execute(delete(BatchTask).where(BatchTask.id == task_id))
            await db.commit()


async def test_update_schedule_without_active_keeps_stopped(
    client: httpx.AsyncClient,
) -> None:
    """只编辑时间的计划调整不静默重启已停计划（active 缺省保持现状）。"""
    async with SessionLocal() as db:
        plan = BatchTask(
            code=f"PYTEST-PLAN-{uuid.uuid4().hex[:8]}",
            name="pytest 已停计划",
            status=TaskStatus.ABORTED,
            control_state="ABORTED",
            case_ids=[],
            total_cases=0,
            schedule_type=TaskScheduleType.DAILY,
            schedule_time="09:00",
            next_run_at=None,
            schedule_active=False,
            finished_at=datetime.now(),
        )
        db.add(plan)
        await db.commit()
        plan_id = plan.id
    try:
        response = await client.put(
            f"/tasks/{plan_id}/schedule", json={"schedule_time": "10:30"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schedule_active"] is False
        assert body["next_run_at"] is None
        assert body["status"] == "ABORTED"

        # 显式激活仍可恢复
        response = await client.put(
            f"/tasks/{plan_id}/schedule", json={"active": True}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schedule_active"] is True
        assert body["next_run_at"] is not None
        assert body["status"] == "SCHEDULED"
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(BatchTask).where(BatchTask.id == plan_id))
            await db.commit()
