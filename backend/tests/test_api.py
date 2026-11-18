"""REST API 集成测试（依赖本机 PostgreSQL / Redis 与内置 Mock 服务）。

若依赖不可用则自动跳过，保证在纯离线环境也能跑单元测试。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from io import BytesIO

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, text

from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.task import BatchTask

pytestmark = pytest.mark.asyncio

MOCK_TARGET = "http://127.0.0.1:8002"


async def _db_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _mock_target_ready() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(f"{MOCK_TARGET}/health")
        return response.status_code == 200
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


async def _wait_task(client: httpx.AsyncClient, task_id: int, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).json()
        if task["status"] in {"COMPLETED", "ABORTED", "FAILED"}:
            return task
        await __import__("asyncio").sleep(0.8)
    raise TimeoutError(f"任务 {task_id} 未在 {timeout}s 内结束")


async def test_login_and_profile(client: httpx.AsyncClient) -> None:
    me = (await client.get("/auth/me")).json()
    assert me["username"] == "admin"
    unauthorized = await client.get("/auth/me", headers={"Authorization": "Bearer invalid"})
    assert unauthorized.status_code == 401


async def test_auth_requires_token() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
    ) as http:
        response = await http.get("/cases")
    assert response.status_code == 401


async def test_environment_list_and_variables(client: httpx.AsyncClient) -> None:
    payload = (await client.get("/environments", params={"page_size": 200})).json()
    assert payload["total"] >= 2
    env = next(item for item in payload["items"] if item["code"] == "QA-MOCK")
    assert env["body_template"]["query"] == "{{query}}"

    variables = (await client.get(f"/environments/{env['id']}/variables")).json()
    secret = next(item for item in variables if item["is_secret"])
    assert "*" in secret["value_masked"]

    # 演示用例默认绑定按真实 Agent 报文配置的非流式环境
    agent_env = next(item for item in payload["items"] if item["code"] == "QA-MOCK-JSON")
    assert agent_env["api_path"] == "/api/v1/agent/chat"
    assert agent_env["basic_auth"]["password_set"] is True
    assert agent_env["pre_hook"]["enabled"] is True
    usage = (await client.get(f"/environments/{agent_env['id']}/usage")).json()
    assert usage["case_count"] >= 1
    assert usage["task_count"] >= 0


async def test_environment_health_check(client: httpx.AsyncClient) -> None:
    if not await _mock_target_ready():
        pytest.skip("Mock 被测系统未启动")
    payload = (await client.get("/environments")).json()
    env = next(item for item in payload["items"] if item["code"] == "QA-MOCK")
    result = (await client.post(f"/environments/{env['id']}/health-check")).json()
    assert result["ok"] is True
    assert result["status_code"] == 200


async def test_prompts_versions_and_usage(client: httpx.AsyncClient) -> None:
    payload = (await client.get("/prompts", params={"page_size": 200})).json()
    codes = {item["code"] for item in payload["items"]}
    assert {"SIM_DEFAULT_BILLING_USER", "JUDGE_DEFAULT_ZERO_TOLERANCE"} <= codes

    simulator = next(item for item in payload["items"] if item["code"] == "SIM_DEFAULT_BILLING_USER")
    detail = (await client.get(f"/prompts/{simulator['id']}")).json()
    assert detail["current_version"] >= 1
    assert detail["versions"][0]["variables"]

    usage = (await client.get(f"/prompts/{simulator['id']}/usage")).json()
    assert usage["ref_count"] >= 1
    assert usage["can_delete"] is False

    # 版本发布后，响应中的版本列表必须立即包含新版本（selectin 缓存回归）
    stamp = uuid.uuid4().hex[:8]
    created = await client.post(
        "/prompts",
        json={
            "code": f"PYTEST-TPL-{stamp}",
            "name": "pytest 临时模板",
            "template_type": "USER_SIMULATOR",
            "content": "请围绕 {{user_goal}} 提出第 {{current_turn}} 轮问题。",
            "changelog": "pytest 初始版本",
        },
    )
    assert created.status_code == 200, created.text
    probe = created.json()
    assert len(probe["versions"]) == 1

    published = await client.post(
        f"/prompts/{probe['id']}/versions",
        json={"content": "请围绕 {{user_goal}} 提出第 {{current_turn}} 轮问题，并输出 JSON。", "changelog": "pytest v2"},
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["current_version"] == 2
    assert [item["version"] for item in body["versions"]] == [2, 1]

    removed = await client.delete(f"/prompts/{probe['id']}")
    assert removed.status_code == 200


async def test_case_revision_flow(client: httpx.AsyncClient) -> None:
    stamp = uuid.uuid4().hex[:8]
    code = f"PYTEST-{stamp}"
    created = await client.post(
        "/cases",
        json={
            "code": code,
            "name": "pytest 用例",
            "case_type": "SINGLE",
            "priority": "P2",
            "query": "订单 A2024001 的含税金额是多少？",
            "expected_answer": "¥12,345.67，共 3 笔",
            "expected_rules": {
                "amounts": [{"label": "含税金额", "value": 12345.67}],
                "counts": [{"label": "笔数", "value": 3}],
            },
        },
    )
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        updated = await client.put(
            f"/cases/{case_id}",
            json={"name": "pytest 用例(改名)", "max_turns": 6, "change_summary": "改名"},
        )
        assert updated.status_code == 200
        assert updated.json()["current_version"] == 2

        diff = (
            await client.get(
                f"/cases/{case_id}/revisions/diff",
                params={"from_version": 1, "to_version": 2},
            )
        ).json()
        assert any(change["path"] == "name" for change in diff["changes"])

        revisions = (await client.get(f"/cases/{case_id}/revisions")).json()
        assert revisions[0]["version"] == 2
        assert revisions[0]["change_type"] == "UPDATE"

        rollback = await client.post(f"/cases/{case_id}/rollback", json={"version": 1})
        assert rollback.status_code == 200
        body = rollback.json()
        assert body["current_version"] == 3
        assert body["name"] == "pytest 用例"
    finally:
        deleted = await client.delete(f"/cases/{case_id}")
        assert deleted.status_code == 200


async def test_case_returns_template_names(client: httpx.AsyncClient) -> None:
    """用例响应需同时携带模板 code 与 name，界面展示名称而非编码。"""
    prompts = (await client.get("/prompts", params={"page_size": 200})).json()["items"]
    simulator = next(item for item in prompts if item["code"] == "SIM_DEFAULT_BILLING_USER")
    judge = next(item for item in prompts if item["code"] == "JUDGE_DEFAULT_ZERO_TOLERANCE")

    stamp = uuid.uuid4().hex[:8]
    code = f"PYTEST-{stamp}"
    created = await client.post(
        "/cases",
        json={
            "code": code,
            "name": "pytest 模板名称回归",
            "case_type": "SINGLE",
            "priority": "P2",
            "query": "订单 A2024001 的含税金额是多少？",
            "expected_answer": "¥12,345.67，共 3 笔",
            "simulator_template_id": simulator["id"],
            "judge_template_id": judge["id"],
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    try:
        assert body["simulator_template_code"] == simulator["code"]
        assert body["simulator_template_name"] == simulator["name"]
        assert body["judge_template_code"] == judge["code"]
        assert body["judge_template_name"] == judge["name"]

        listed = (await client.get("/cases", params={"keyword": code})).json()
        assert listed["items"], "列表应能按编号检索到新建用例"
        item = listed["items"][0]
        assert item["simulator_template_name"] == simulator["name"]
        assert item["judge_template_name"] == judge["name"]
    finally:
        deleted = await client.delete(f"/cases/{body['id']}")
        assert deleted.status_code == 200


async def test_task_lifecycle_with_metrics(client: httpx.AsyncClient) -> None:
    if not await _mock_target_ready():
        pytest.skip("Mock 被测系统未启动")
    cases = (await client.get("/cases", params={"page_size": 200})).json()
    target = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    task = await client.post(
        "/tasks",
        json={
            "name": f"pytest 任务 {uuid.uuid4().hex[:6]}",
            "case_ids": [target["id"]],
            "concurrency": 1,
            "qps_limit": 10,
            "retry_on_env_error": True,
            "max_env_retries": 2,
            "retry_backoff_seconds": 1.5,
        },
    )
    assert task.status_code == 200, task.text
    task_id = task.json()["id"]
    assert task.json()["retry_on_env_error"] is True
    assert task.json()["max_env_retries"] == 2
    assert task.json()["retry_backoff_seconds"] == 1.5
    finished = await _wait_task(client, task_id)
    assert finished["status"] == "COMPLETED"

    metrics = (await client.get(f"/tasks/{task_id}/metrics")).json()
    assert metrics["finished_cases"] == 1
    assert metrics["total_tokens"] > 0
    assert metrics["progress"] == 100.0
    assert metrics["env_retry_count"] == 0
    assert metrics["avg_score"] > 0

    executions = (await client.get(f"/tasks/{task_id}/executions")).json()
    assert len(executions["items"]) == 1
    assert executions["items"][0]["attempt_no"] == 1
    assert executions["items"][0]["error_kind"] is None
    latest = (
        await client.get(
            f"/tasks/{task_id}/executions", params={"latest_per_case": "true"}
        )
    ).json()
    assert latest["total"] == 1
    assert latest["items"][0]["attempt_count"] == 1

    by_keyword = (
        await client.get(
            f"/tasks/{task_id}/executions",
            params={"keyword": target["code"], "latest_per_case": "true"},
        )
    ).json()
    assert by_keyword["total"] == 1
    assert by_keyword["items"][0]["case_code"] == target["code"]
    missing_keyword = (
        await client.get(f"/tasks/{task_id}/executions", params={"keyword": "不存在的用例关键词"})
    ).json()
    assert missing_keyword["total"] == 0
    hit_status = latest["items"][0]["status"]
    by_status = (
        await client.get(
            f"/tasks/{task_id}/executions",
            params={"status": hit_status, "latest_per_case": "true"},
        )
    ).json()
    assert by_status["total"] == 1
    miss_status = "ABORTED" if hit_status != "ABORTED" else "PASS"
    no_status = (
        await client.get(
            f"/tasks/{task_id}/executions",
            params={"status": miss_status, "latest_per_case": "true"},
        )
    ).json()
    assert no_status["total"] == 0

    score = latest["items"][0]["total_score"]
    assert score is not None
    assert metrics["avg_score"] == round(score, 2)
    by_range = (
        await client.get(
            f"/tasks/{task_id}/executions",
            params={"min_score": score, "max_score": score, "latest_per_case": "true"},
        )
    ).json()
    assert by_range["total"] == 1
    impossible = (
        {"min_score": score + 1} if score < 100 else {"max_score": max(score - 1, 0)}
    )
    outside = (
        await client.get(
            f"/tasks/{task_id}/executions",
            params={**impossible, "latest_per_case": "true"},
        )
    ).json()
    assert outside["total"] == 0
    sorted_desc = (
        await client.get(
            f"/tasks/{task_id}/executions",
            params={"sort": "score_desc", "latest_per_case": "true"},
        )
    ).json()
    assert sorted_desc["total"] == latest["total"]
    invalid_range = await client.get(
        f"/tasks/{task_id}/executions", params={"min_score": 90, "max_score": 60}
    )
    assert invalid_range.status_code == 422

    exported = await client.get(
        f"/tasks/{task_id}/export", params={"latest_per_case": "true"}
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(BytesIO(exported.content))
    assert workbook.sheetnames == ["执行概览", "用例结果"]
    export_sheet = workbook["用例结果"]
    export_headers = [cell.value for cell in export_sheet[1]]
    assert "用例编号" in export_headers
    assert "最终答案" in export_headers
    assert export_sheet.max_row == len(latest["items"]) + 1
    assert (
        export_sheet.cell(
            row=2, column=export_headers.index("用例编号") + 1
        ).value
        == target["code"]
    )
    exported_all = await client.get(
        f"/tasks/{task_id}/export", params={"latest_per_case": "false"}
    )
    assert exported_all.status_code == 200, exported_all.text
    all_sheet = load_workbook(BytesIO(exported_all.content))["用例结果"]
    assert all_sheet.max_row == executions["total"] + 1

    detail = (await client.get(f"/executions/{executions['items'][0]['id']}")).json()
    assert detail["turns"] and detail["case_snapshot"]
    assert detail["case_version"] == target["current_version"]

    raw = (await client.get(f"/executions/{detail['id']}/raw")).json()
    assert raw["turns"][0]["request"]["url"]

    history = (await client.get(f"/cases/{target['id']}/executions")).json()
    assert history["total"] >= 1
    trend = (await client.get(f"/cases/{target['id']}/trend")).json()
    assert len(trend["points"]) >= 1


async def test_execution_pass_rate_window(client: httpx.AsyncClient) -> None:
    """大盘最近执行通过率以时间窗内（默认 24 小时）的已判定执行为基准。"""
    payload = (await client.get("/executions/pass-rate")).json()
    assert payload["window_hours"] == 24
    assert payload["total"] >= 1
    assert 0 <= payload["passed"] <= payload["measured"] <= payload["total"]
    if payload["measured"]:
        assert payload["pass_rate"] is not None
        assert 0 <= payload["pass_rate"] <= 100
    else:
        assert payload["pass_rate"] is None

    custom = (
        await client.get("/executions/pass-rate", params={"hours": 48})
    ).json()
    assert custom["window_hours"] == 48
    assert custom["total"] >= payload["total"]

    invalid = await client.get("/executions/pass-rate", params={"hours": 0})
    assert invalid.status_code == 422


async def test_task_result_export_missing_task(client: httpx.AsyncClient) -> None:
    response = await client.get("/tasks/999999/export")
    assert response.status_code == 404


async def test_task_create_with_all_active_scope(client: httpx.AsyncClient) -> None:
    """ALL_ACTIVE 由后端跨分页解析全部启用用例，避免前端只看当前页漏选。"""
    cases = (
        await client.get("/cases", params={"status": "ACTIVE", "page_size": 200})
    ).json()
    expected = sorted(item["id"] for item in cases["items"])
    assert expected, "演示数据应包含启用用例"

    response = await client.post(
        "/tasks",
        json={
            "name": f"pytest 全量范围 {uuid.uuid4().hex[:6]}",
            "case_scope": "ALL_ACTIVE",
            "auto_start": False,
            "concurrency": 2,
            "qps_limit": 5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["total_cases"] == len(expected)
    assert sorted(body["case_ids"]) == expected


async def test_task_list_keyword_and_schedule_filters(client: httpx.AsyncClient) -> None:
    """任务管理页依赖关键词、状态与执行方式组合筛选。"""
    cases = (await client.get("/cases", params={"page_size": 200})).json()
    target = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    keyword = f"搜索验证 {uuid.uuid4().hex[:6]}"
    moment = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    created = await client.post(
        "/tasks",
        json={
            "name": keyword,
            "case_ids": [target["id"]],
            "schedule_type": "SCHEDULED",
            "scheduled_at": moment.isoformat(),
            "auto_start": False,
            "concurrency": 1,
            "qps_limit": 2,
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    assert created.json()["status"] == "SCHEDULED"
    assert created.json()["next_run_at"] is not None
    try:
        page = (await client.get("/tasks", params={"keyword": keyword})).json()
        assert page["total"] == 1
        assert page["items"][0]["id"] == task_id

        filtered = (
            await client.get(
                "/tasks",
                params={
                    "keyword": keyword,
                    "status": "SCHEDULED",
                    "schedule_type": "SCHEDULED",
                },
            )
        ).json()
        assert filtered["total"] == 1

        mismatch = (
            await client.get("/tasks", params={"keyword": keyword, "schedule_type": "WEEKLY"})
        ).json()
        assert mismatch["total"] == 0

        code_hit = (await client.get("/tasks", params={"keyword": created.json()["code"]})).json()
        assert code_hit["total"] == 1
        assert code_hit["items"][0]["id"] == task_id
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(BatchTask).where(BatchTask.id == task_id))
            await db.commit()


async def test_task_create_requires_case_selection(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/tasks",
        json={
            "name": "pytest 空用例任务",
            "case_scope": "SELECTED",
            "case_ids": [],
            "auto_start": False,
        },
    )
    assert response.status_code == 422
    assert "至少选择一条用例" in response.json()["message"]


async def test_task_repeat_rounds_create_and_validation(client: httpx.AsyncClient) -> None:
    """连续多轮：所有执行方式都支持设置轮次。"""
    cases = (await client.get("/cases", params={"page_size": 200})).json()
    target = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    moment = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    created = await client.post(
        "/tasks",
        json={
            "name": f"pytest 连续三轮 {uuid.uuid4().hex[:6]}",
            "case_ids": [target["id"]],
            "schedule_type": "SCHEDULED",
            "scheduled_at": moment.isoformat(),
            "repeat_rounds": 3,
            "auto_start": False,
            "concurrency": 1,
            "qps_limit": 2,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    task_id = body["id"]
    immediate_id: int | None = None
    try:
        assert body["repeat_rounds"] == 3
        assert body["round_index"] == 1
        assert body["schedule_type"] == "SCHEDULED"

        # 立即执行同样支持连续轮次
        immediate = await client.post(
            "/tasks",
            json={
                "name": f"pytest 立即多轮 {uuid.uuid4().hex[:6]}",
                "case_ids": [target["id"]],
                "schedule_type": "IMMEDIATE",
                "repeat_rounds": 2,
                "auto_start": False,
            },
        )
        assert immediate.status_code == 200, immediate.text
        immediate_id = immediate.json()["id"]
        assert immediate.json()["repeat_rounds"] == 2

        # 每天定时同样支持连续轮次
        daily = await client.post(
            "/tasks",
            json={
                "name": f"pytest 每日多轮 {uuid.uuid4().hex[:6]}",
                "case_ids": [target["id"]],
                "schedule_type": "DAILY",
                "schedule_time": "09:00",
                "repeat_rounds": 4,
                "auto_start": False,
            },
        )
        assert daily.status_code == 200, daily.text
        daily_id = daily.json()["id"]
        try:
            assert daily.json()["repeat_rounds"] == 4
        finally:
            async with SessionLocal() as db:
                await db.execute(delete(BatchTask).where(BatchTask.id == daily_id))
                await db.commit()

        detail = (await client.get(f"/tasks/{task_id}")).json()
        assert detail["repeat_rounds"] == 3
        assert detail["round_index"] == 1
    finally:
        async with SessionLocal() as db:
            ids = [task_id] + ([immediate_id] if immediate_id else [])
            await db.execute(delete(BatchTask).where(BatchTask.id.in_(ids)))
            await db.commit()


async def test_stop_plan_for_all_schedule_modes(client: httpx.AsyncClient) -> None:
    """指定时间、连续多轮、每天定时都支持停止计划。"""
    cases = (await client.get("/cases", params={"page_size": 200})).json()
    target = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    moment = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    created_ids: list[int] = []
    try:
        payloads = [
            {
                "name": f"pytest 停止指定时间 {uuid.uuid4().hex[:6]}",
                "case_ids": [target["id"]],
                "schedule_type": "SCHEDULED",
                "scheduled_at": moment.isoformat(),
                "auto_start": False,
            },
            {
                "name": f"pytest 停止连续多轮 {uuid.uuid4().hex[:6]}",
                "case_ids": [target["id"]],
                "schedule_type": "IMMEDIATE",
                "repeat_rounds": 3,
                "auto_start": False,
            },
            {
                "name": f"pytest 停止每天定时 {uuid.uuid4().hex[:6]}",
                "case_ids": [target["id"]],
                "schedule_type": "DAILY",
                "schedule_time": "09:00",
                "repeat_rounds": 2,
                "auto_start": False,
            },
        ]
        for payload in payloads:
            created = await client.post("/tasks", json=payload)
            assert created.status_code == 200, created.text
            task_id = created.json()["id"]
            created_ids.append(task_id)
            stopped = await client.post(f"/tasks/{task_id}/stop-plan")
            assert stopped.status_code == 200, stopped.text
            body = stopped.json()
            assert body["schedule_active"] is False
            assert body["next_run_at"] is None
            assert body["status"] == "ABORTED"
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(BatchTask).where(BatchTask.id.in_(created_ids)))
            await db.commit()


async def test_stop_plan_rejects_plain_task(client: httpx.AsyncClient) -> None:
    cases = (await client.get("/cases", params={"page_size": 200})).json()
    target = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    created = await client.post(
        "/tasks",
        json={
            "name": f"pytest 普通任务 {uuid.uuid4().hex[:6]}",
            "case_ids": [target["id"]],
            "schedule_type": "IMMEDIATE",
            "auto_start": False,
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    try:
        response = await client.post(f"/tasks/{task_id}/stop-plan")
        assert response.status_code == 409
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(BatchTask).where(BatchTask.id == task_id))
            await db.commit()


async def test_stop_plan_terminates_inflight_round(client: httpx.AsyncClient) -> None:
    """连续多轮计划执行中停止：根任务标记停止，在跑/待跑轮次被终止。"""
    cases = (await client.get("/cases", params={"page_size": 200})).json()
    target = next(item for item in cases["items"] if item["code"] == "DEMO-SINGLE-001")
    created = await client.post(
        "/tasks",
        json={
            "name": f"pytest 停止执行中多轮 {uuid.uuid4().hex[:6]}",
            "case_ids": [target["id"]],
            "schedule_type": "IMMEDIATE",
            "repeat_rounds": 3,
            "auto_start": False,
        },
    )
    assert created.status_code == 200, created.text
    root_id = created.json()["id"]
    child_id: int | None = None
    try:
        async with SessionLocal() as db:
            root = await db.get(BatchTask, root_id)
            root.status = "COMPLETED"
            root.control_state = "FINISHED"
            root.schedule_active = True
            root.started_at = datetime.now()
            child = BatchTask(
                code=f"RUN-{uuid.uuid4().hex[:6]}",
                name="停止执行中多轮 · 第 2/3 轮",
                status="PENDING",
                control_state="RUNNING",
                case_ids=[target["id"]],
                total_cases=1,
                schedule_type="IMMEDIATE",
                schedule_active=False,
                schedule_source_id=root_id,
                repeat_rounds=3,
                round_index=2,
                round_prev_id=root_id,
            )
            db.add(child)
            await db.commit()
            child_id = int(child.id)

        stopped = await client.post(f"/tasks/{root_id}/stop-plan")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["schedule_active"] is False
        assert stopped.json()["control_state"] == "ABORTED"
        async with SessionLocal() as db:
            child = await db.get(BatchTask, child_id)
            assert child is not None
            assert child.status == "ABORTED"
    finally:
        async with SessionLocal() as db:
            ids = [root_id] + ([child_id] if child_id else [])
            await db.execute(delete(BatchTask).where(BatchTask.id.in_(ids)))
            await db.commit()


async def test_sandbox_template_type_guard(client: httpx.AsyncClient) -> None:
    prompts = (await client.get("/prompts", params={"page_size": 200})).json()
    simulator = next(item for item in prompts["items"] if item["code"] == "SIM_DEFAULT_BILLING_USER")
    response = await client.post(
        f"/prompts/{simulator['id']}/sandbox",
        json={"mode": "JUDGE", "variables": {"case_type": "SINGLE"}},
    )
    assert response.status_code in {400, 422}
