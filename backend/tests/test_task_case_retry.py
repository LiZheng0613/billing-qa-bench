"""批次内单用例重试：调度器执行 + API 校验。

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
from billing_qa.services.engine import scheduler as scheduler_module
from billing_qa.services.engine.executor import CaseExecutor
from billing_qa.services.engine.metrics import compute_task_metrics
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


async def _load_demo_case(code: str) -> tuple[int, int, dict] | None:
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


async def _create_task_with_execution(
    case_id: int,
    version: int,
    snapshot: dict[str, Any],
    *,
    latest_status: str = "ERROR",
) -> tuple[int, int]:
    """构造「已结束批次 + 一条历史执行」的最小数据。"""
    async with SessionLocal() as db:
        task = BatchTask(
            code=f"PYTEST-CASE-RETRY-{uuid.uuid4().hex[:8]}",
            name="pytest 单用例重试",
            status="COMPLETED",
            control_state="FINISHED",
            case_ids=[case_id],
            total_cases=1,
            finished_cases=1,
            concurrency=1,
            qps_limit=50,
            retry_on_env_error=False,
            max_env_retries=0,
            created_by="pytest",
            finished_at=now(),
        )
        db.add(task)
        await db.flush()
        record = ExecutionRecord(
            trace_uid=str(uuid.uuid4()),
            task_id=task.id,
            case_id=case_id,
            case_version=version,
            case_snapshot=snapshot,
            source="BATCH",
            status=latest_status,
            attempt_no=1,
            pass_flag=latest_status == "PASS",
            total_score=90 if latest_status == "PASS" else None,
            started_at=now(),
            finished_at=now(),
            error_kind="ENV" if latest_status == "ERROR" else None,
            error_message="模拟历史异常" if latest_status == "ERROR" else None,
        )
        db.add(record)
        await db.commit()
        return int(task.id), int(record.id)


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
    raise TimeoutError(f"任务 {task_id} 的单用例重试未在 {timeout}s 内结束")


async def _wait_execution_count(task_id: int, count: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with SessionLocal() as db:
            total = (
                await db.execute(
                    select(ExecutionRecord).where(ExecutionRecord.task_id == task_id)
                )
            ).scalars().all()
        if len(total) >= count and not scheduler.is_running(task_id):
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(f"任务 {task_id} 未在 {timeout}s 内产生 {count} 条执行记录")


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL / Mock 服务不可用，跳过单用例重试 API 测试")
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


async def test_scheduler_retry_case_reruns_single_case() -> None:
    """单用例重试应生成 RETRY 记录（attempt_no+1），并把任务重新收口为 COMPLETED。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例 DEMO-SINGLE-001 缺失")
    case_id, version, snapshot = loaded
    task_id, _ = await _create_task_with_execution(case_id, version, snapshot)
    try:
        assert await scheduler.retry_case(task_id, case_id) is True
        # 重试执行期间任务应处于运行态，阻止重复提交
        assert scheduler.is_running(task_id) is True
        await _wait_not_running(task_id)

        async with SessionLocal() as db:
            records = (
                await db.execute(
                    select(ExecutionRecord)
                    .where(ExecutionRecord.task_id == task_id)
                    .order_by(ExecutionRecord.id)
                )
            ).scalars().all()
            assert len(records) == 2
            retried = records[-1]
            assert retried.source == "RETRY"
            assert retried.attempt_no == 2
            assert retried.status == "PASS"

            task = await db.get(BatchTask, task_id)
            assert task is not None
            assert task.status == "COMPLETED"
            assert task.control_state == "FINISHED"
            assert task.finished_at is not None
            metrics = await compute_task_metrics(db, task)
            assert metrics.status_distribution.PASS == 1
            assert metrics.status_distribution.ERROR == 0
    finally:
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_retry_case_blocks_concurrent_submission(monkeypatch: Any) -> None:
    """同一任务同时只允许一个单用例重试 worker。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    task_id, _ = await _create_task_with_execution(case_id, version, snapshot)

    gate = asyncio.Event()

    async def slow_execute(self: CaseExecutor, **kwargs: Any) -> int:
        await gate.wait()
        return 0

    monkeypatch.setattr(scheduler_module.CaseExecutor, "execute", slow_execute)
    try:
        assert await scheduler.retry_case(task_id, case_id) is True
        assert await scheduler.retry_case(task_id, case_id) is False
    finally:
        gate.set()
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_retry_case_api_rejects_non_retryable_latest(
    client: httpx.AsyncClient,
) -> None:
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    task_id, _ = await _create_task_with_execution(
        case_id, version, snapshot, latest_status="PASS"
    )
    try:
        response = await client.post(f"/tasks/{task_id}/cases/{case_id}/retry")
        assert response.status_code == 409
        assert "可重试状态" in response.json()["message"]
    finally:
        await _cleanup(task_id)


async def test_retry_case_api_rejects_case_not_in_batch(
    client: httpx.AsyncClient,
) -> None:
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    task_id, _ = await _create_task_with_execution(case_id, version, snapshot)
    try:
        response = await client.post(f"/tasks/{task_id}/cases/{case_id + 999999}/retry")
        assert response.status_code == 404
        assert "不在当前批次" in response.json()["message"]
    finally:
        await _cleanup(task_id)


async def test_retry_case_api_retries_error_case(client: httpx.AsyncClient) -> None:
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    task_id, _ = await _create_task_with_execution(case_id, version, snapshot)
    try:
        response = await client.post(f"/tasks/{task_id}/cases/{case_id}/retry")
        assert response.status_code == 200, response.text
        assert "已提交单用例重试" in response.json()["message"]
        await _wait_execution_count(task_id, 2)
        async with SessionLocal() as db:
            records = (
                await db.execute(
                    select(ExecutionRecord)
                    .where(ExecutionRecord.task_id == task_id)
                    .order_by(ExecutionRecord.id)
                )
            ).scalars().all()
            assert records[-1].source == "RETRY"
            assert records[-1].attempt_no == 2
            assert records[-1].status == "PASS"
    finally:
        await _wait_not_running(task_id)
        await _cleanup(task_id)
