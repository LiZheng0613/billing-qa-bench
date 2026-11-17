"""统一模型解析：所有 LLM 调用场景共用同一份模型注册数据。

用例编写、动态模拟器、LLM 裁判、提示词沙盒等入口都必须经这里按
``model_id`` 取模型，避免各功能各写一套「显式 id → 角色缺省」逻辑，
导致同一份注册配置在不同入口表现不一致（选中的模型没生效、
或未做角色校验把模拟器模型当成裁判用）。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.exceptions import NotFoundError, ValidationError
from billing_qa.models.enums import ModelRole
from billing_qa.models.model_config import ModelConfig
from billing_qa.services.llm.client import ModelContext


def _role_value(role: ModelRole | str) -> str:
    return role.value if isinstance(role, ModelRole) else str(role)


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
    """解析模型并转成执行期快照，供 LLMClient 直接使用。"""
    return ModelContext.from_orm(
        await resolve_model(db, model_id=model_id, roles=roles)
    )


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
    """唯一启用模型的执行期快照，供模拟器等固定角色使用。"""
    return ModelContext.from_orm(await resolve_unique_active(db, role))


__all__ = [
    "get_model",
    "resolve_context",
    "resolve_model",
    "resolve_unique_active",
    "resolve_unique_context",
]
