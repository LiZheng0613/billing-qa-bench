"""环境与被测接口配置接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_qa.api.v1.serializers import env_to_out, env_variable_to_out
from billing_qa.core.crypto import encrypt_value
from billing_qa.core.exceptions import ConflictError, NotFoundError
from billing_qa.core.security import get_current_operator
from billing_qa.db.session import get_db
from billing_qa.models.environment import Environment, EnvironmentVariable
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.environment import (
    EnvironmentCreate,
    EnvironmentOut,
    EnvironmentUpdate,
    EnvironmentUsage,
    EnvVariableOut,
    HealthCheckResult,
    VariableBatchUpdate,
)
from billing_qa.services.adapter.target_api import EnvContext, TargetApiAdapter

router = APIRouter(
    prefix="/environments",
    tags=["环境配置"],
    dependencies=[Depends(get_current_operator)],
)


async def _get_env(db: AsyncSession, env_id: int) -> Environment:
    env = (
        await db.execute(
            select(Environment)
            .options(selectinload(Environment.variables))
            .where(Environment.id == env_id)
        )
    ).scalar_one_or_none()
    if env is None:
        raise NotFoundError("环境不存在")
    return env


@router.get("", response_model=Page[EnvironmentOut], summary="环境列表")
async def list_environments(
    keyword: str | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[EnvironmentOut]:
    conditions = []
    if keyword:
        like = f"%{keyword}%"
        conditions.append(Environment.name.like(like) | Environment.code.like(like))
    if is_active is not None:
        conditions.append(Environment.is_active.is_(is_active))
    total = (
        await db.execute(select(func.count()).select_from(Environment).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(Environment)
            .options(selectinload(Environment.variables))
            .where(*conditions)
            .order_by(Environment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[EnvironmentOut](
        items=[env_to_out(item) for item in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=EnvironmentOut, summary="创建环境")
async def create_environment(
    payload: EnvironmentCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> EnvironmentOut:
    exists = (
        await db.execute(select(Environment.id).where(Environment.code == payload.code))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"环境编码 {payload.code} 已存在")
    data = payload.model_dump(exclude={"variables"})
    data["pre_hook"] = payload.pre_hook.model_dump() if payload.pre_hook else None
    env = Environment(**data)
    for item in payload.variables:
        env.variables.append(
            EnvironmentVariable(
                key=item.key,
                value_encrypted=encrypt_value(item.value or ""),
                is_secret=item.is_secret,
                description=item.description,
            )
        )
    db.add(env)
    await db.commit()
    return env_to_out(await _get_env(db, env.id))


@router.get("/{env_id}", response_model=EnvironmentOut, summary="环境详情")
async def get_environment(env_id: int, db: AsyncSession = Depends(get_db)) -> EnvironmentOut:
    return env_to_out(await _get_env(db, env_id))


@router.put("/{env_id}", response_model=EnvironmentOut, summary="更新环境")
async def update_environment(
    env_id: int,
    payload: EnvironmentUpdate,
    db: AsyncSession = Depends(get_db),
) -> EnvironmentOut:
    env = await _get_env(db, env_id)
    data = payload.model_dump(exclude_unset=True)
    if "pre_hook" in data:
        data["pre_hook"] = (
            payload.pre_hook.model_dump() if payload.pre_hook is not None else None
        )
    for key, value in data.items():
        setattr(env, key, value)
    await db.commit()
    return env_to_out(await _get_env(db, env_id))


@router.delete("/{env_id}", response_model=MessageResponse, summary="删除环境")
async def delete_environment(env_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    env = await _get_env(db, env_id)
    await db.delete(env)
    await db.commit()
    return MessageResponse(message=f"环境 {env.code} 已删除")


@router.get(
    "/{env_id}/variables", response_model=list[EnvVariableOut], summary="环境变量池"
)
async def list_variables(
    env_id: int, db: AsyncSession = Depends(get_db)
) -> list[EnvVariableOut]:
    env = await _get_env(db, env_id)
    return [env_variable_to_out(item) for item in env.variables]


@router.put(
    "/{env_id}/variables",
    response_model=list[EnvVariableOut],
    summary="批量覆盖环境变量池",
)
async def replace_variables(
    env_id: int,
    payload: VariableBatchUpdate,
    db: AsyncSession = Depends(get_db),
) -> list[EnvVariableOut]:
    env = await _get_env(db, env_id)
    existing = {item.key: item for item in env.variables}
    incoming_keys = {item.key for item in payload.variables}
    for key, item in existing.items():
        if key not in incoming_keys:
            await db.delete(item)
    for item in payload.variables:
        current = existing.get(item.key)
        if current is None:
            env.variables.append(
                EnvironmentVariable(
                    key=item.key,
                    value_encrypted=encrypt_value(item.value or ""),
                    is_secret=item.is_secret,
                    description=item.description,
                )
            )
        else:
            # 掩码值（含 *）视为未修改，避免前端回写掩码覆盖真实密钥
            if item.value is not None and "*" not in item.value:
                current.value_encrypted = encrypt_value(item.value)
            current.is_secret = item.is_secret
            current.description = item.description
    await db.commit()
    return [env_variable_to_out(item) for item in (await _get_env(db, env_id)).variables]


@router.post(
    "/{env_id}/health-check", response_model=HealthCheckResult, summary="接口连通性探活"
)
async def health_check(env_id: int, db: AsyncSession = Depends(get_db)) -> HealthCheckResult:
    env = await _get_env(db, env_id)
    adapter = TargetApiAdapter(EnvContext.from_orm(env))
    ok, status_code, latency, message = await adapter.health_check()
    env.last_health_status = "UP" if ok else "DOWN"
    env.last_health_latency_ms = latency
    env.last_health_at = __import__("datetime").datetime.now()
    env.last_health_message = message[:1000]
    await db.commit()
    return HealthCheckResult(
        ok=ok,
        status_code=status_code,
        latency_ms=latency,
        message=message,
        checked_at=env.last_health_at,
    )


@router.get("/{env_id}/usage", response_model=EnvironmentUsage, summary="环境引用影响面")
async def environment_usage(
    env_id: int, db: AsyncSession = Depends(get_db)
) -> EnvironmentUsage:
    await _get_env(db, env_id)
    case_count = (
        await db.execute(
            select(func.count())
            .select_from(TestCase)
            .where(TestCase.environment_id == env_id, TestCase.deleted_at.is_(None))
        )
    ).scalar_one()
    task_count = (
        await db.execute(
            select(func.count()).select_from(BatchTask).where(BatchTask.environment_id == env_id)
        )
    ).scalar_one()
    return EnvironmentUsage(
        environment_id=env_id, case_count=int(case_count), task_count=int(task_count)
    )
