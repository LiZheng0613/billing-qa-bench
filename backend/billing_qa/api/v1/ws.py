"""任务实时进度 WebSocket。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from billing_qa.core.event_bus import event_bus
from billing_qa.core.logging import get_logger
from billing_qa.core.security import decode_access_token, resolve_token_user
from billing_qa.db.session import SessionLocal
from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskProgressEvent
from billing_qa.services.engine.metrics import compute_task_metrics
from billing_qa.services.engine.scheduler import scheduler

logger = get_logger(__name__)
router = APIRouter(tags=["实时推送"])


async def _initial_event(task_id: int) -> dict | None:
    async with SessionLocal() as db:
        # 每次建立 WS 连接都会执行初始快照：只取事件与指标计算所需的列，
        # 避免整行加载 summary/case_ids 等大 JSON（ALL_ACTIVE 任务可达数十 KB）。
        task = (
            await db.execute(
                select(
                    BatchTask.id,
                    BatchTask.status,
                    BatchTask.control_state,
                    BatchTask.error_message,
                    BatchTask.total_cases,
                    BatchTask.case_ids,
                    BatchTask.started_at,
                    BatchTask.created_at,
                    BatchTask.finished_at,
                ).where(BatchTask.id == task_id)
            )
        ).one_or_none()
        if task is None:
            return None
        metrics = await compute_task_metrics(db, task)
        event = TaskProgressEvent(
            type="snapshot",
            task_id=task.id,
            status=task.status,
            control_state=task.control_state,
            progress=metrics.progress,
            finished_cases=metrics.finished_cases,
            total_cases=metrics.total_cases,
            pass_count=metrics.status_distribution.PASS,
            fail_count=metrics.status_distribution.FAIL
            + metrics.status_distribution.REPETITION_ABORT,
            error_count=metrics.status_distribution.ERROR,
            max_turns_count=metrics.status_distribution.MAX_TURNS_EXCEEDED,
            metrics=metrics,
            retrying_cases=scheduler.retrying_case_ids(task.id),
            message=task.error_message,
            timestamp=datetime.now(),
        )
        return event.model_dump(mode="json")


@router.websocket("/ws/tasks")
async def task_list_socket(
    websocket: WebSocket, token: str | None = Query(default=None)
) -> None:
    """批量任务列表实时事件流：一条连接接收所有任务的状态/进度。"""
    try:
        # 与 REST 同一套校验：不只解码 JWT，还要确认账号仍可用且
        # token_version 未被顶号 / 禁用 / 改角色刷新，防止长连接绕过。
        await resolve_token_user(decode_access_token(token or ""))
    except HTTPException:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    queue = await event_bus.subscribe_all()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except TimeoutError:
                await websocket.send_json(
                    {"type": "ping", "timestamp": datetime.now().isoformat()}
                )
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.debug("任务列表 WS 客户端断开")
    except Exception as exc:
        logger.debug("任务列表 WS 会话结束: %s", exc)
    finally:
        await event_bus.unsubscribe_all(queue)


@router.websocket("/ws/tasks/{task_id}")
async def task_progress_socket(
    websocket: WebSocket,
    task_id: int,
    token: str | None = Query(default=None),
) -> None:
    try:
        # 同 REST 的完整校验（账号状态 / token_version），防长连接绕过。
        await resolve_token_user(decode_access_token(token or ""))
    except HTTPException:
        await websocket.close(code=4401)
        return

    initial = await _initial_event(task_id)
    if initial is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    queue = await event_bus.subscribe(task_id)
    try:
        await websocket.send_json(initial)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except TimeoutError:
                await websocket.send_json(
                    {"type": "ping", "task_id": task_id, "timestamp": datetime.now().isoformat()}
                )
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.debug("WS 客户端断开 task=%s", task_id)
    except Exception as exc:
        logger.debug("WS 会话结束 task=%s: %s", task_id, exc)
    finally:
        await event_bus.unsubscribe(task_id, queue)
