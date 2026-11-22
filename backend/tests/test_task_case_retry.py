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


async def _create_task_with_error_executions(
    cases: list[tuple[int, int, dict[str, Any]]],
    *,
    with_records: bool = True,
    statuses: list[str] | None = None,
) -> int:
    """构造「已结束批次 + 每个用例一条历史执行」的最小数据。

    ``with_records=False`` 时只建批次不写历史执行，用于模拟「还没跑过任何用例」。
    ``statuses`` 可按用例顺序指定历史状态，默认全部为 ERROR。
    """
    async with SessionLocal() as db:
        task = BatchTask(
            code=f"PYTEST-CASE-RETRY-QUEUE-{uuid.uuid4().hex[:8]}",
            name="pytest 单用例重试排队",
            status="COMPLETED",
            control_state="FINISHED",
            case_ids=[case_id for case_id, _, _ in cases],
            total_cases=len(cases),
            finished_cases=len(cases),
            concurrency=1,
            qps_limit=50,
            retry_on_env_error=False,
            max_env_retries=0,
            created_by="pytest",
            finished_at=now(),
        )
        db.add(task)
        await db.flush()
        recorded = cases if with_records else []
        for index, (case_id, version, snapshot) in enumerate(recorded):
            status = statuses[index] if statuses is not None else "ERROR"
            db.add(
                ExecutionRecord(
                    trace_uid=str(uuid.uuid4()),
                    task_id=task.id,
                    case_id=case_id,
                    case_version=version,
                    case_snapshot=snapshot,
                    source="BATCH",
                    status=status,
                    attempt_no=1,
                    pass_flag=status == "PASS",
                    total_score=90 if status == "PASS" else None,
                    started_at=now(),
                    finished_at=now(),
                    error_kind="ENV" if status == "ERROR" else None,
                    error_message="模拟历史异常" if status == "ERROR" else None,
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
    """同一条用例在重试中不能重复提交（整批执行仍需等待重试队列结束）。"""
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


async def test_retry_case_queues_another_case_while_running(monkeypatch: Any) -> None:
    """一个用例重试执行中时，另一个可重试用例仍可提交并排队执行。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded_a = await _load_demo_case("DEMO-SINGLE-001")
    loaded_b = await _load_demo_case("DEMO-SINGLE-FAIL")
    if loaded_a is None or loaded_b is None:
        pytest.skip("演示用例缺失")
    case_a, version_a, snapshot_a = loaded_a
    case_b, version_b, snapshot_b = loaded_b
    task_id = await _create_task_with_error_executions(
        [
            (case_a, version_a, snapshot_a),
            (case_b, version_b, snapshot_b),
        ]
    )

    gate = asyncio.Event()
    started = asyncio.Event()
    executed: list[int] = []

    async def slow_execute(self: CaseExecutor, **kwargs: Any) -> int:
        executed.append(int(kwargs["case_id"]))
        started.set()
        await gate.wait()
        return 0

    monkeypatch.setattr(scheduler_module.CaseExecutor, "execute", slow_execute)
    try:
        assert await scheduler.retry_case(task_id, case_a) is True
        await asyncio.wait_for(started.wait(), timeout=10)
        # 第一个用例仍在执行时，另一个用例应能提交并进入队列
        assert await scheduler.retry_case(task_id, case_b) is True
        assert scheduler.retrying_case_ids(task_id) == sorted([case_a, case_b])

        gate.set()
        await _wait_not_running(task_id)

        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            assert task is not None
            assert task.status == "COMPLETED"
        assert executed == [case_a, case_b]
    finally:
        gate.set()
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_retry_case_rejected_while_batch_running(
    client: httpx.AsyncClient, monkeypatch: Any
) -> None:
    """整批执行期间仍禁止单用例重试，保持原有的互斥语义。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded = await _load_demo_case("DEMO-SINGLE-001")
    if loaded is None:
        pytest.skip("演示用例缺失")
    case_id, version, snapshot = loaded
    task_id = await _create_task_with_error_executions(
        [(case_id, version, snapshot)], with_records=False
    )

    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_execute(self: CaseExecutor, **kwargs: Any) -> int:
        started.set()
        await gate.wait()
        return 0

    monkeypatch.setattr(scheduler_module.CaseExecutor, "execute", slow_execute)
    try:
        assert await scheduler.start(task_id) is True
        await asyncio.wait_for(started.wait(), timeout=10)
        assert scheduler.is_batch_running(task_id) is True
        assert await scheduler.retry_case(task_id, case_id) is False

        response = await client.post(f"/tasks/{task_id}/cases/{case_id}/retry")
        assert response.status_code == 409
        assert "任务正在运行" in response.json()["message"]
    finally:
        gate.set()
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_abort_discards_queued_case_retries(monkeypatch: Any) -> None:
    """终止指令应丢弃尚未开始的排队用例，并保持终止语义。"""
    if not await _dependencies_ready():
        pytest.skip("PostgreSQL 或 Mock 服务不可用")
    loaded_a = await _load_demo_case("DEMO-SINGLE-001")
    loaded_b = await _load_demo_case("DEMO-SINGLE-FAIL")
    if loaded_a is None or loaded_b is None:
        pytest.skip("演示用例缺失")
    case_a, version_a, snapshot_a = loaded_a
    case_b, version_b, snapshot_b = loaded_b
    task_id = await _create_task_with_error_executions(
        [
            (case_a, version_a, snapshot_a),
            (case_b, version_b, snapshot_b),
        ]
    )

    gate = asyncio.Event()
    started = asyncio.Event()
    executed: list[int] = []

    async def slow_execute(self: CaseExecutor, **kwargs: Any) -> int:
        executed.append(int(kwargs["case_id"]))
        started.set()
        await gate.wait()
        return 0

    monkeypatch.setattr(scheduler_module.CaseExecutor, "execute", slow_execute)
    try:
        assert await scheduler.retry_case(task_id, case_a) is True
        await asyncio.wait_for(started.wait(), timeout=10)
        assert await scheduler.retry_case(task_id, case_b) is True
        await scheduler.abort(task_id)

        gate.set()
        await _wait_not_running(task_id)

        async with SessionLocal() as db:
            task = await db.get(BatchTask, task_id)
            assert task is not None
            assert task.status == "ABORTED"
            assert task.control_state == "ABORTED"
        # 终止后不再启动排队中的第二条用例
        assert executed == [case_a]
        assert scheduler.retrying_case_ids(task_id) == []
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


async def test_retry_case_api_queues_another_case_while_running(
    client: httpx.AsyncClient, monkeypatch: Any
) -> None:
    """经由接口提交时，一个用例重试中另一个用例仍可提交，且不重复受理。"""
    loaded_a = await _load_demo_case("DEMO-SINGLE-001")
    loaded_b = await _load_demo_case("DEMO-SINGLE-FAIL")
    if loaded_a is None or loaded_b is None:
        pytest.skip("演示用例缺失")
    case_a, version_a, snapshot_a = loaded_a
    case_b, version_b, snapshot_b = loaded_b
    task_id = await _create_task_with_error_executions(
        [
            (case_a, version_a, snapshot_a),
            (case_b, version_b, snapshot_b),
        ]
    )

    gate = asyncio.Event()
    started = asyncio.Event()
    executed: list[int] = []

    async def slow_execute(self: CaseExecutor, **kwargs: Any) -> int:
        executed.append(int(kwargs["case_id"]))
        started.set()
        await gate.wait()
        return 0

    monkeypatch.setattr(scheduler_module.CaseExecutor, "execute", slow_execute)
    try:
        first = await client.post(f"/tasks/{task_id}/cases/{case_a}/retry")
        assert first.status_code == 200, first.text
        await asyncio.wait_for(started.wait(), timeout=10)

        # 同一条用例执行中，重复提交应被明确拒绝
        duplicate = await client.post(f"/tasks/{task_id}/cases/{case_a}/retry")
        assert duplicate.status_code == 409
        assert "已提交重试" in duplicate.json()["message"]

        # 另一个可重试用例仍可提交，并进入同一队列
        second = await client.post(f"/tasks/{task_id}/cases/{case_b}/retry")
        assert second.status_code == 200, second.text
        assert scheduler.retrying_case_ids(task_id) == sorted([case_a, case_b])

        detail = await client.get(f"/tasks/{task_id}")
        assert detail.status_code == 200
        assert sorted(detail.json()["retrying_cases"]) == sorted([case_a, case_b])

        gate.set()
        await _wait_not_running(task_id)
        assert executed == [case_a, case_b]

        # 队列结束后任务详情不再带「重试中」标记
        detail = await client.get(f"/tasks/{task_id}")
        assert detail.json()["retrying_cases"] == []
        assert detail.json()["status"] == "COMPLETED"
    finally:
        gate.set()
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_retry_candidates_groups_by_status(client: httpx.AsyncClient) -> None:
    """按状态汇总可重试用例，已通过等不可重试状态不进入候选。"""
    loaded_pass = await _load_demo_case("DEMO-SINGLE-001")
    loaded_fail = await _load_demo_case("DEMO-SINGLE-FAIL")
    if loaded_pass is None or loaded_fail is None:
        pytest.skip("演示用例缺失")
    pass_case, pass_version, pass_snapshot = loaded_pass
    fail_case, fail_version, fail_snapshot = loaded_fail
    task_id = await _create_task_with_error_executions(
        [
            (pass_case, pass_version, pass_snapshot),
            (fail_case, fail_version, fail_snapshot),
        ],
        statuses=["PASS", "ERROR"],
    )
    try:
        response = await client.get(f"/tasks/{task_id}/retry-candidates")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 1
        assert data["retrying_cases"] == []
        assert [(group["status"], group["case_ids"]) for group in data["groups"]] == [
            ("ERROR", [fail_case])
        ]
    finally:
        await _cleanup(task_id)


async def test_retry_cases_api_queues_selection(
    client: httpx.AsyncClient, monkeypatch: Any
) -> None:
    """批量接口一次提交多条用例，去重后按顺序排队执行。"""
    loaded_a = await _load_demo_case("DEMO-SINGLE-001")
    loaded_b = await _load_demo_case("DEMO-SINGLE-FAIL")
    if loaded_a is None or loaded_b is None:
        pytest.skip("演示用例缺失")
    case_a, version_a, snapshot_a = loaded_a
    case_b, version_b, snapshot_b = loaded_b
    task_id = await _create_task_with_error_executions(
        [
            (case_a, version_a, snapshot_a),
            (case_b, version_b, snapshot_b),
        ]
    )

    gate = asyncio.Event()
    started = asyncio.Event()
    executed: list[int] = []

    async def slow_execute(self: CaseExecutor, **kwargs: Any) -> int:
        executed.append(int(kwargs["case_id"]))
        started.set()
        await gate.wait()
        return 0

    monkeypatch.setattr(scheduler_module.CaseExecutor, "execute", slow_execute)
    try:
        response = await client.post(
            f"/tasks/{task_id}/cases/retry",
            json={"case_ids": [case_a, case_b, case_a]},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["accepted"] == [case_a, case_b]
        assert data["skipped"] == []
        assert data["retrying_cases"] == sorted([case_a, case_b])
        assert "已提交 2 条用例重试" in data["message"]
        await asyncio.wait_for(started.wait(), timeout=10)

        detail = await client.get(f"/tasks/{task_id}")
        assert sorted(detail.json()["retrying_cases"]) == sorted([case_a, case_b])

        gate.set()
        await _wait_not_running(task_id)
        assert executed == [case_a, case_b]

        detail = await client.get(f"/tasks/{task_id}")
        assert detail.json()["retrying_cases"] == []
        assert detail.json()["status"] == "COMPLETED"
    finally:
        gate.set()
        await _wait_not_running(task_id)
        await _cleanup(task_id)


async def test_retry_cases_api_skips_non_retryable(
    client: httpx.AsyncClient,
) -> None:
    """批量提交时不符合重试条件的用例只跳过，不影响其他用例受理。"""
    loaded_error = await _load_demo_case("DEMO-SINGLE-FAIL")
    loaded_pass = await _load_demo_case("DEMO-SINGLE-001")
    if loaded_error is None or loaded_pass is None:
        pytest.skip("演示用例缺失")
    error_case, error_version, error_snapshot = loaded_error
    pass_case, pass_version, pass_snapshot = loaded_pass
    task_id = await _create_task_with_error_executions(
        [
            (error_case, error_version, error_snapshot),
            (pass_case, pass_version, pass_snapshot),
        ],
        statuses=["ERROR", "PASS"],
    )
    try:
        response = await client.post(
            f"/tasks/{task_id}/cases/retry",
            json={"case_ids": [error_case, pass_case]},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["accepted"] == [error_case]
        assert len(data["skipped"]) == 1
        assert data["skipped"][0]["case_id"] == pass_case
        assert "可重试状态" in data["skipped"][0]["reason"]
        assert "跳过 1 条" in data["message"]
        await _wait_execution_count(task_id, 3)

        # 整批都是不可重试用例时应明确拒绝，而不是静默成功。
        rejected = await client.post(
            f"/tasks/{task_id}/cases/retry", json={"case_ids": [pass_case]}
        )
        assert rejected.status_code == 409
        assert "可重试状态" in rejected.json()["message"]
    finally:
        await _wait_not_running(task_id)
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
