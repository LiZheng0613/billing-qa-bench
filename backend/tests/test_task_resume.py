"""终止后续做：只补跑未完成用例，已有最终判定的用例保持原结果。

依赖本机 PostgreSQL、Redis 与内置 Mock 服务；不可用时自动跳过。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select, text

from billing_qa.core.control_store import control_store
from billing_qa.db.base import now
from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase as CaseModel
from billing_qa.models.test_case import TestCaseRevision as CaseRevision
from billing_qa.services.engine.scheduler import scheduler

pytestmark = pytest.mark.asyncio

MOCK_LLM = "http://127.0.0.1:8001/health"
MOCK_TARGET = "http://127.0.0.1:8002/health"


async def _dependencies_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        async with httpx.AsyncClient(timeout=2) as client:
            for url in (MOCK_LLM, MOCK_TARGET):
                if (await client.get(url)).status_code != 200:
                    return False
        return True
    except Exception:
        return False


async def _load_demo_case(code: str) -> tuple[int, int, dict[str, Any]] | None:
    async with SessionLocal() as db:
        case = (
            await db.execute(select(CaseModel).where(CaseModel.code == code))
        ).scalar_one_or_none()
        if case is None:
            return None
        revision = (
            await db.execute(
                select(CaseRevision).where(
                    CaseRevision.case_id == case.id,
                    CaseRevision.version == case.current_version,
                )
            )
        ).scalar_one_or_none()
        if revision is None:
            return None
        return case.id, case.current_version, revision.snapshot


async def _create_task(
    case_ids: list[int],
    *,
    status: str = "ABORTED",
    records: list[tuple[int, int, dict[str, Any], str, int]] | None = None,
) -> int:
    """构造「已终止批次 + 可选历史执行」的最小数据。"""
    async with SessionLocal() as db:
        task = BatchTask(
            code=f"PYTEST-RESUME-{uuid.uuid4().hex[:8]}",
            name="pytest 终止续做",
            status=status,
            control_state="ABORTED" if status == "ABORTED" else "FINISHED",
            case_ids=case_ids,
            total_cases=len(case_ids),
            concurrency=1,
            qps_limit=50,
            retry_on_env_error=False,
            max_env_retries=0,
            retry_on_error=False,
            max_error_retries=0,
            created_by="pytest",
            finished_at=now(),
        )
        db.add(task)
        await db.flush()
        for case_id, version, snapshot, record_status, attempt_no in records or []:
            db.add(
                ExecutionRecord(
                    trace_uid=str(uuid.uuid4()),
                    task_id=task.id,
                    case_id=case_id,
                    case_version=version,
                    case_snapshot=snapshot,
                    source="BATCH",
                    status=record_status,
                    attempt_no=attempt_no,
                    pass_flag=record_status == "PASS",
                    total_score=90 if record_status == "PASS" else None,
                    started_at=now(),
                    finished_at=now(),
                )
            )
        await db.commit()
        return int(task.id)


async def _cleanup(task_id: int | None) -> None:
    if task_id is None:
        return
    async with SessionLocal() as db:
        await db.execute(delete(ExecutionRecord).where(ExecutionRecord.task_id == task_id))
        await db.execute(delete(BatchTask).where(BatchTask.id == task_id))
        await db.commit()
    await control_store.clear(task_id)


async def _wait_not_running(task_id: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not scheduler.is_running(task_id):
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"任务 {task_id} 的续做未在 {timeout}s 内结束")


async def _records(task_id: int) -> list[ExecutionRecord]:
    async with SessionLocal() as db:
        return list(
            (
                await db.execute(
                    select(ExecutionRecord)
                    .where(ExecutionRecord.task_id == task_id)
                    .order_by(ExecutionRecord.id)
                )
            )
            .scalars()
            .all()
        )


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL / Mock 服务不可用，跳过续做 API 测试")
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


async def test_resume_only_reruns_unfinished_cases() -> None:
    """续做只补已终止用例；已通过的用例与被终止用例的尝试编号都要保持正确。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    done = await _load_demo_case("DEMO-SINGLE-FAIL")
    aborted = await _load_demo_case("DEMO-SINGLE-001")
    if done is None or aborted is None:
        pytest.skip("演示用例缺失")
    done_id, done_version, done_snapshot = done
    aborted_id, aborted_version, aborted_snapshot = aborted
    task_id = await _create_task(
        [done_id, aborted_id],
        records=[
            (done_id, done_version, done_snapshot, "PASS", 1),
            (aborted_id, aborted_version, aborted_snapshot, "ABORTED", 1),
        ],
    )
    try:
        assert await scheduler.start(task_id, resume=True) is True
        await _wait_not_running(task_id)

        records = await _records(task_id)
        assert len(records) == 3
        resumed = [record for record in records if record.source == "RESUME"]
        assert len(resumed) == 1
        assert resumed[0].case_id == aborted_id
        assert resumed[0].attempt_no == 2
        assert resumed[0].status == "PASS"
        # 已通过用例没有被重跑
        assert len([record for record in records if record.case_id == done_id]) == 1

        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            assert task is not None
            assert task.status == "COMPLETED"
            assert task.control_state == "FINISHED"
    finally:
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_resume_runs_cases_that_never_started() -> None:
    """终止时还没有任何执行记录，也能整体续做（历史「失败重跑」在此场景会报错）。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, _, _ = loaded
    task_id = await _create_task([case_id])
    try:
        assert await scheduler.start(task_id, resume=True) is True
        await _wait_not_running(task_id)

        records = await _records(task_id)
        assert len(records) == 1
        assert records[0].source == "RESUME"
        assert records[0].attempt_no == 1
        assert records[0].status == "PASS"
    finally:
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_resume_api_allows_task_without_failed_records(
    client: httpx.AsyncClient,
) -> None:
    """没有失败记录、仅有未完成用例时，续做接口也应受理。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    done = await _load_demo_case("DEMO-SINGLE-FAIL")
    todo = await _load_demo_case("DEMO-SINGLE-001")
    if done is None or todo is None:
        pytest.skip("演示用例缺失")
    done_id, done_version, done_snapshot = done
    todo_id, _, _ = todo
    task_id = await _create_task(
        [done_id, todo_id],
        records=[(done_id, done_version, done_snapshot, "PASS", 1)],
    )
    try:
        response = await client.post(f"/tasks/{task_id}/resume")
        assert response.status_code == 200, response.text
        assert "1 条未完成用例" in response.json()["message"]
        await _wait_not_running(task_id)

        records = await _records(task_id)
        assert len([record for record in records if record.case_id == todo_id]) == 1
        assert records[-1].case_id == todo_id
        assert records[-1].source == "RESUME"
    finally:
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_resume_api_rejects_finished_cases(client: httpx.AsyncClient) -> None:
    """所有用例都已有最终结果时，续做应明确拒绝并引导使用失败重跑。"""
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    task_id = await _create_task(
        [case_id],
        records=[(case_id, version, snapshot, "PASS", 1)],
    )
    try:
        response = await client.post(f"/tasks/{task_id}/resume")
        assert response.status_code == 409
        assert "无需续做" in response.json()["message"]
    finally:
        await _cleanup(task_id)


async def test_resume_api_rejects_running_task(
    client: httpx.AsyncClient, monkeypatch: Any
) -> None:
    """任务正在运行时不允许重复发起续做。"""
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, _, _ = loaded
    task_id = await _create_task([case_id])
    try:
        monkeypatch.setattr(scheduler, "is_running", lambda _: True)
        response = await client.post(f"/tasks/{task_id}/resume")
        assert response.status_code == 409
        assert "正在运行" in response.json()["message"]
    finally:
        await _cleanup(task_id)


async def test_task_detail_reports_unfinished_cases(
    client: httpx.AsyncClient,
) -> None:
    """任务详情返回未完成用例数，前端据此决定是否展示「继续执行」。"""
    done = await _load_demo_case("DEMO-SINGLE-FAIL")
    todo = await _load_demo_case("DEMO-SINGLE-001")
    if done is None or todo is None:
        pytest.skip("演示用例缺失")
    done_id, done_version, done_snapshot = done
    todo_id, _, _ = todo
    task_id = await _create_task(
        [done_id, todo_id],
        records=[(done_id, done_version, done_snapshot, "PASS", 1)],
    )
    try:
        response = await client.get(f"/tasks/{task_id}")
        assert response.status_code == 200, response.text
        assert response.json()["unfinished_cases"] == 1

        listing = await client.get("/tasks", params={"keyword": "PYTEST-RESUME-"})
        target = next(
            (item for item in listing.json()["items"] if item["id"] == task_id),
            None,
        )
        if target is not None:
            assert target["unfinished_cases"] == 1
    finally:
        await _cleanup(task_id)
