"""进程内事件总线：任务执行进度推送到 WebSocket。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from billing_qa.core.logging import get_logger

logger = get_logger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, task_id: int) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[task_id].add(queue)
        return queue

    async def unsubscribe(self, task_id: int, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(task_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(task_id, None)

    async def publish(self, task_id: int, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(task_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("WS 队列已满，丢弃一条事件 task=%s", task_id)

    def subscriber_count(self, task_id: int) -> int:
        return len(self._subscribers.get(task_id, ()))


event_bus = EventBus()
