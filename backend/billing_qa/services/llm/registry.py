"""统一模型解析：所有 LLM 调用场景共用同一份模型注册数据。

用例编写、动态模拟器、LLM 裁判、提示词沙盒等入口都必须经这里按
``model_id`` 取模型，避免各功能各写一套「显式 id → 角色缺省」逻辑，
导致同一份注册配置在不同入口表现不一致（选中的模型没生效、
或未做角色校验把模拟器模型当成裁判用）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.exceptions import NotFoundError, ValidationError
from billing_qa.models.enums import ModelRole
from billing_qa.models.model_config import ModelConfig
from billing_qa.services.llm.client import ModelContext


def _role_value(role: ModelRole | str) -> str:
    return role.value if isinstance(role, ModelRole) else str(role)


# ------------------------------------------------------------------ #
# 执行期快照缓存（短 TTL）
# ------------------------------------------------------------------ #
# 模型注册是低频变更数据，但执行热路径每次都要「查库 + 解密 api_key」。
# 加短 TTL 进程内缓存后，稳态下每次执行不再重复这些查询与解密；
# 管理端变更由 invalidate_model_cache() 主动失效（api/v1/models.py
# 各写端点调用），TTL 兜底覆盖绕过 API 直接改库的场景。
_CONTEXT_CACHE_TTL_SECONDS = 5.0
_context_cache: dict[tuple[Any, ...], tuple[float, ModelContext]] = {}


def invalidate_model_cache() -> None:
    """模型注册变更后调用：立即清空执行期快照缓存。"""
    _context_cache.clear()


def _cache_hit(key: tuple[Any, ...]) -> ModelContext | None:
    hit = _context_cache.get(key)
    if hit is None or time.monotonic() - hit[0] >= _CONTEXT_CACHE_TTL_SECONDS:
        return None
    return hit[1]


def _cache_store(key: tuple[Any, ...], context: ModelContext) -> ModelContext:
    _context_cache[key] = (time.monotonic(), context)
    return context


def _copy(context: ModelContext) -> ModelContext:
    # 缓存中的实例是共享底本；对外一律返回副本，避免调用方修改串味。
    return replace(context)


async def get_model(db: AsyncSession, model_id: int) -> ModelConfig:
    """按注册 id 取模型（不校验角色/启用状态，供注册管理与连通性测试使用）。"""
    model = await db.get(ModelConfig, model_id)
    if model is None:
        raise NotFoundError(f"模型 id={model_id} 不存在")
    return model


async def resolve_model(
    db: AsyncSession,
    *,
    model_id: int | None = None,
    roles: Sequence[ModelRole | str] = (),
) -> ModelConfig:
    """按注册 id 或角色解析出本次 LLM 调用要用的模型。

    - 显式 ``model_id``：必须存在、未停用，且角色命中 ``roles``；失败直接
      报错，不静默回退，避免「选的是 A、实际跑的是 B」。
    - 未指定：按 ``roles`` 顺序取 id 最小且启用的模型，作为该场景的缺省值。
    """
    allowed = [_role_value(role) for role in roles]

    if model_id is not None:
        model = await get_model(db, model_id)
        if not model.is_active:
            raise ValidationError(f"模型 {model.name} 已停用，请换用启用的模型")
        if allowed and model.role not in allowed:
            raise ValidationError(
                f"模型 {model.name} 的角色是 {model.role}，当前场景需要 {' / '.join(allowed)}"
            )
        return model

    if not allowed:
        raise ValidationError("未指定模型，且当前场景没有可回退的角色")

    for role in allowed:
        model = (
            await db.execute(
                select(ModelConfig)
                .where(ModelConfig.role == role, ModelConfig.is_active.is_(True))
                .order_by(ModelConfig.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if model is not None:
            return model

    raise ValidationError(
        f"未配置可用的 {' / '.join(allowed)} 模型，请先在模型注册中新增并启用"
    )


async def resolve_context(
    db: AsyncSession,
    *,
    model_id: int | None = None,
    roles: Sequence[ModelRole | str] = (),
) -> ModelContext:
    """解析模型并转成执行期快照（短 TTL 缓存），供 LLMClient 直接使用。"""
    key = ("ctx", model_id, tuple(_role_value(role) for role in roles))
    hit = _cache_hit(key)
    if hit is not None:
        return _copy(hit)
    context = ModelContext.from_orm(
        await resolve_model(db, model_id=model_id, roles=roles)
    )
    _cache_store(key, context)
    return _copy(context)


async def resolve_unique_active(db: AsyncSession, role: ModelRole | str) -> ModelConfig:
    """取该角色「唯一启用」的模型。

    用例编写与动态模拟器不再按次/按用例选模型，统一由系统配置决定：
    - 0 个启用：视为未配置，报错提示先注册并启用；
    - 多个启用：视为配置错误，明确列出冲突项要求只保留一个。
    """
    role_value = _role_value(role)
    rows = (
        await db.execute(
            select(ModelConfig)
            .where(ModelConfig.role == role_value, ModelConfig.is_active.is_(True))
            .order_by(ModelConfig.id.asc())
        )
    ).scalars().all()

    if not rows:
        raise ValidationError(
            f"未配置启用的 {role_value} 模型，请先在模型注册中新增并启用"
        )
    if len(rows) > 1:
        names = "、".join(f"{item.name}(id={item.id})" for item in rows)
        raise ValidationError(
            f"{role_value} 角色启用了 {len(rows)} 个模型（{names}），"
            "请只保留一个启用状态"
        )
    return rows[0]


async def resolve_unique_context(db: AsyncSession, role: ModelRole | str) -> ModelContext:
    """唯一启用模型的执行期快照（短 TTL 缓存），供模拟器等固定角色使用。"""
    key = ("unique", _role_value(role))
    hit = _cache_hit(key)
    if hit is not None:
        return _copy(hit)
    context = ModelContext.from_orm(await resolve_unique_active(db, role))
    _cache_store(key, context)
    return _copy(context)


async def resolve_contexts(
    db: AsyncSession, model_ids: Sequence[int], role: ModelRole | str
) -> list[ModelContext]:
    """按 id 顺序批量解析多个同角色模型的执行期快照（缓存 + 单条 IN 查询）。

    校验口径与 resolve_model 完全一致（存在 / 启用 / 角色匹配），异常
    类型与消息也逐字相同；缓存命中的 id 不再查库，未命中的合并为一次
    IN 查询。重复 id 按原顺序重复返回（与逐个 db.get 的历史行为一致）。
    """
    allowed = [_role_value(role)]
    ordered = [int(item) for item in model_ids]
    if not ordered:
        return []
    resolved: dict[int, ModelContext] = {}
    missing: list[int] = []
    for model_id in dict.fromkeys(ordered):
        hit = _cache_hit(("ctx", model_id, tuple(allowed)))
        if hit is not None:
            resolved[model_id] = hit
        else:
            missing.append(model_id)
    if missing:
        rows = {
            int(row.id): row
            for row in (
                await db.execute(
                    select(ModelConfig).where(ModelConfig.id.in_(missing))
                )
            ).scalars()
        }
        for model_id in missing:
            model = rows.get(model_id)
            if model is None:
                raise NotFoundError(f"模型 id={model_id} 不存在")
            if not model.is_active:
                raise ValidationError(f"模型 {model.name} 已停用，请换用启用的模型")
            if model.role not in allowed:
                raise ValidationError(
                    f"模型 {model.name} 的角色是 {model.role}，"
                    f"当前场景需要 {' / '.join(allowed)}"
                )
            resolved[model_id] = _cache_store(
                ("ctx", model_id, tuple(allowed)), ModelContext.from_orm(model)
            )
    return [_copy(resolved[model_id]) for model_id in ordered]


__all__ = [
    "get_model",
    "invalidate_model_cache",
    "resolve_context",
    "resolve_contexts",
    "resolve_model",
    "resolve_unique_active",
    "resolve_unique_context",
]
