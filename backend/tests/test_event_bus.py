"""事件总线：任务列表全量订阅与按任务订阅互不干扰。"""

from __future__ import annotations

from billing_qa.core.event_bus import EventBus


async def test_global_subscriber_receives_all_tasks() -> None:
    bus = EventBus()
    global_queue = await bus.subscribe_all()
    task_queue = await bus.subscribe(1)
    try:
        await bus.publish(1, {"task_id": 1, "status": "RUNNING"})
        await bus.publish(2, {"task_id": 2, "status": "COMPLETED"})

        assert await global_queue.get() == {"task_id": 1, "status": "RUNNING"}
        assert await global_queue.get() == {"task_id": 2, "status": "COMPLETED"}
        # 单任务订阅只收到自己任务的事件
        assert await task_queue.get() == {"task_id": 1, "status": "RUNNING"}
        assert task_queue.empty()
    finally:
        await bus.unsubscribe(1, task_queue)
        await bus.unsubscribe_all(global_queue)
