"""任务控制面：Redis 状态标志位（RUNNING / ABORTING / ABORTED），带进程内降级。"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

import redis.asyncio as aioredis

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger

logger = get_logger(__name__)

# 只续租自己持有的锁（值等于 token 才 PEXPIRE）
_RENEW_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

# 只释放自己持有的锁（值等于 token 才 DEL），避免误删其他实例的锁
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class ControlStore:
    """Redis 优先；连不上时自动降级为进程内字典，保证单机可运行。"""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._local: dict[str, str] = {}
        self._local_hash: dict[str, dict[str, str]] = {}
        # 进程内运行锁：name -> (token, 过期时刻 monotonic 秒)
        self._local_locks: dict[str, tuple[str, float]] = {}
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

    # ------------------------------------------------------------------ #
    # 分布式运行锁：多实例部署下同一任务只允许一个实例执行。
    # Redis 不可用时降级为进程内锁（带 TTL 语义），单进程行为不变。
    # ------------------------------------------------------------------ #
    def lock_key(self, task_id: int) -> str:
        """任务运行锁的键名。"""
        return f"{settings.redis_key_prefix}lock:task:{task_id}:run"

    async def acquire_lock(self, name: str, token: str, ttl_seconds: float) -> bool:
        """获取运行锁；已被其他持有者占用时返回 False。"""
        if self._redis is not None:
            try:
                return bool(
                    await self._redis.set(
                        name, token, nx=True, px=max(1, int(ttl_seconds * 1000))
                    )
                )
            except Exception as exc:
                logger.warning("获取 Redis 运行锁失败，降级为进程内锁: %s", exc)
        return self._acquire_local_lock(name, token, ttl_seconds)

    async def renew_lock(self, name: str, token: str, ttl_seconds: float) -> bool:
        """续租运行锁；持有者不匹配（已易主/已过期）时返回 False。"""
        if self._redis is not None:
            try:
                renewed = await self._redis.eval(
                    _RENEW_LOCK_SCRIPT, 1, name, token, max(1, int(ttl_seconds * 1000))
                )
                return bool(renewed)
            except Exception as exc:
                logger.warning("续租 Redis 运行锁失败，降级为进程内锁: %s", exc)
        return self._renew_local_lock(name, token, ttl_seconds)

    async def release_lock(self, name: str, token: str) -> None:
        """释放运行锁；只删除自己持有的锁，避免误删其他实例的锁。"""
        if self._redis is not None:
            try:
                await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, name, token)
                return
            except Exception as exc:
                logger.warning("释放 Redis 运行锁失败，降级为进程内锁: %s", exc)
        self._release_local_lock(name, token)

    @staticmethod
    def _local_ttl(ttl_seconds: float) -> float:
        return max(0.0, ttl_seconds)

    def _acquire_local_lock(self, name: str, token: str, ttl_seconds: float) -> bool:
        current = time.monotonic()
        # 顺带清理已过期的本地锁
        self._local_locks = {
            key: value for key, value in self._local_locks.items() if value[1] > current
        }
        if name in self._local_locks:
            return False
        self._local_locks[name] = (token, current + self._local_ttl(ttl_seconds))
        return True

    def _renew_local_lock(self, name: str, token: str, ttl_seconds: float) -> bool:
        held = self._local_locks.get(name)
        current = time.monotonic()
        if held is None or held[1] <= current or held[0] != token:
            return False
        self._local_locks[name] = (token, current + self._local_ttl(ttl_seconds))
        return True

    def _release_local_lock(self, name: str, token: str) -> None:
        held = self._local_locks.get(name)
        if held is not None and held[0] == token:
            self._local_locks.pop(name, None)


control_store = ControlStore()
