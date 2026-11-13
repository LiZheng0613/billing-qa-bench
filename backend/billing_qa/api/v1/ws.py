"""任务实时进度 WebSocket。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from billing_qa.core.event_bus import event_bus
from billing_qa.core.logging import get_logger
from billing_qa.core.security import decode_access_token
from billing_qa.db.session import SessionLocal
from billing_qa.models.task import BatchTask
from billing_qa.schemas.task import TaskProgressEvent
from billing_qa.services.engine.metrics import compute_task_metrics

logger = get_logger(__name__)
router = APIRouter(tags=["实时推送"])


async def _initial_event(task_id: int) -> dict | None:
    async with SessionLocal() as db:
        task = await db.get(BatchTask, task_id)
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
            message=task.error_message,
            timestamp=datetime.now(),
        )
        return event.model_dump(mode="json")


@router.websocket("/ws/tasks/{task_id}")
async def task_progress_socket(
    websocket: WebSocket,
    task_id: int,
    token: str | None = Query(default=None),
) -> None:
    try:
        decode_access_token(token or "")
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
