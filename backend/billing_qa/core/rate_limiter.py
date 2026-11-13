"""QPS 令牌限流（任务级）。"""

from __future__ import annotations

import asyncio
import time


class QpsLimiter:
    """均匀发放令牌，保证任务整体 QPS 不超过配置值。"""

    def __init__(self, qps: float):
        self.interval = 1.0 / max(qps, 0.001)
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._next_at)
            self._next_at = scheduled + self.interval
            wait = scheduled - now
        if wait > 0:
            await asyncio.sleep(wait)
