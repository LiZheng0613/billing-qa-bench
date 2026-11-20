"""分布式运行锁原语：获取互斥、token 续租 / 释放防误删、进程内降级。"""

from __future__ import annotations

import time
import uuid

import pytest

from billing_qa.core.control_store import ControlStore, control_store

pytestmark = pytest.mark.asyncio


async def test_redis_lock_lifecycle() -> None:
    """Redis 可用时：获取互斥、错误 token 不能续租/释放、正确 token 可释放。"""
    await control_store.connect()
    if not control_store.using_redis:
        pytest.skip("Redis 不可用，跳过 Redis 锁测试")
    name = f"batchtest:pytest:lock:{uuid.uuid4().hex[:8]}"
    try:
        assert await control_store.acquire_lock(name, "token-a", 60)
        # 已被 token-a 持有，其他 token 无法获取
        assert not await control_store.acquire_lock(name, "token-b", 60)
        # 错误 token 无法续租
        assert not await control_store.renew_lock(name, "token-b", 60)
        # 错误 token 不会误删锁
        await control_store.release_lock(name, "token-b")
        assert not await control_store.acquire_lock(name, "token-b", 60)
        # 持有者可以续租
        assert await control_store.renew_lock(name, "token-a", 60)
        # 持有者释放后其他 token 可以获取
        await control_store.release_lock(name, "token-a")
        assert await control_store.acquire_lock(name, "token-b", 60)
    finally:
        await control_store.release_lock(name, "token-b")


async def test_local_lock_fallback() -> None:
    """Redis 不可用时降级为进程内锁：语义与 Redis 一致（含 TTL 过期接管）。"""
    store = ControlStore()  # 独立实例，_redis=None → 进程内模式
    name = "pytest:local:lock"
    assert await store.acquire_lock(name, "token-a", 30)
    assert not await store.acquire_lock(name, "token-b", 30)
    assert not await store.renew_lock(name, "token-b", 30)
    await store.release_lock(name, "token-b")
    assert not await store.acquire_lock(name, "token-b", 30)
    assert await store.renew_lock(name, "token-a", 30)
    await store.release_lock(name, "token-a")
    assert await store.acquire_lock(name, "token-b", 30)

    # TTL 过期后允许其他持有者接管（对应实例崩溃后的锁自愈）
    store._local_locks[name] = ("token-b", time.monotonic() - 1)
    assert not await store.renew_lock(name, "token-b", 30)
    assert await store.acquire_lock(name, "token-c", 30)
    await store.release_lock(name, "token-c")
