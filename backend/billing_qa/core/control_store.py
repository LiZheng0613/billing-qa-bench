"""任务控制面：Redis 状态标志位（RUNNING / ABORTING / ABORTED），带进程内降级。"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import redis.asyncio as aioredis

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger

logger = get_logger(__name__)


class ControlStore:
    """Redis 优先；连不上时自动降级为进程内字典，保证单机可运行。"""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._local: dict[str, str] = {}
        self._local_hash: dict[str, dict[str, str]] = {}
        self._connected = False
        self._checked = False

    @property
    def using_redis(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self._checked:
            return
        self._checked = True
        try:
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            await client.ping()
            self._redis = client
            self._connected = True
            logger.info("Redis 控制面已连接: %s", settings.redis_url)
        except Exception as exc:
            logger.warning("Redis 不可用，降级为进程内控制面: %s", exc)
            self._redis = None
            self._connected = False

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._connected = False
            self._checked = False

    def _key(self, task_id: int, suffix: str) -> str:
        return f"{settings.redis_key_prefix}task:{task_id}:{suffix}"

    async def set_state(self, task_id: int, state: str) -> None:
        key = self._key(task_id, "control")
        self._local[key] = state
        if self._redis is not None:
            try:
                await self._redis.set(key, state, ex=7 * 24 * 3600)
            except Exception as exc:
                logger.warning("写入 Redis 控制位失败: %s", exc)

    async def get_state(self, task_id: int) -> str | None:
        key = self._key(task_id, "control")
        if self._redis is not None:
            try:
                value = await self._redis.get(key)
                if value is not None:
                    return value
            except Exception as exc:
                logger.warning("读取 Redis 控制位失败: %s", exc)
        return self._local.get(key)

    async def set_progress(self, task_id: int, payload: dict[str, Any]) -> None:
        key = self._key(task_id, "progress")
        data = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                for k, v in payload.items()}
        self._local_hash[key] = data
        if self._redis is not None:
            try:
                await self._redis.hset(key, mapping=data)
                await self._redis.expire(key, 7 * 24 * 3600)
            except Exception as exc:
                logger.warning("写入 Redis 进度失败: %s", exc)

    async def get_progress(self, task_id: int) -> dict[str, str]:
        key = self._key(task_id, "progress")
        if self._redis is not None:
            try:
                data = await self._redis.hgetall(key)
                if data:
                    return data
            except Exception as exc:
                logger.warning("读取 Redis 进度失败: %s", exc)
        return self._local_hash.get(key, {})

    async def clear(self, task_id: int) -> None:
        key = self._key(task_id, "control")
        self._local.pop(key, None)
        self._local_hash.pop(self._key(task_id, "progress"), None)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.delete(key, self._key(task_id, "progress"))


control_store = ControlStore()
