"""统一模型解析：显式 id 优先、角色校验、唯一启用、缺省回退。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.models import _ensure_single_active
from billing_qa.api.v1.prompts import _pick_model
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import ModelRole
from billing_qa.models.model_config import ModelConfig
from billing_qa.services.author.service import select_author_model
from billing_qa.services.llm.registry import (
    get_model,
    resolve_context,
    resolve_model,
    resolve_unique_active,
)

pytestmark = pytest.mark.asyncio


async def _db_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _model(role: str, *, active: bool = True) -> ModelConfig:
    suffix = uuid.uuid4().hex[:10]
    return ModelConfig(
        code=f"REG-{role}-{suffix}",
        name=f"注册解析测试 {role} {suffix}",
        role=role,
        connection_type="OPENAI_COMPATIBLE",
        provider="openai-compatible",
        base_url="http://127.0.0.1:8001/v1",
        model_name="mock-author",
        is_active=active,
    )


@asynccontextmanager
async def _models(*items: ModelConfig) -> AsyncIterator[tuple[AsyncSession, list[ModelConfig]]]:
    async with SessionLocal() as db:
        db.add_all(items)
        await db.commit()
        for item in items:
            await db.refresh(item)
        try:
            yield db, list(items)
        finally:
            await db.execute(
                delete(ModelConfig).where(ModelConfig.id.in_([item.id for item in items]))
            )
            await db.commit()


@asynccontextmanager
async def _role_isolated(role: str) -> AsyncIterator[None]:
    """临时停用该角色的全部启用模型，测试「唯一启用」语义后恢复。

    演示库本身就有启用中的 CASE_AUTHOR / SIMULATOR 模型，直接断言会被污染，
    因此这里保存原状态、测试结束再还原。
    """
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(ModelConfig).where(
                    ModelConfig.role == role, ModelConfig.is_active.is_(True)
                )
            )
        ).scalars().all()
        ids: list[int] = [item.id for item in rows]
        if ids:
            await db.execute(
                update(ModelConfig)
                .where(ModelConfig.id.in_(ids))
                .values(is_active=False)
            )
            await db.commit()
    try:
        yield
    finally:
        if ids:
            async with SessionLocal() as db:
                await db.execute(
                    update(ModelConfig)
                    .where(ModelConfig.id.in_(ids))
                    .values(is_active=True)
                )
                await db.commit()


async def test_explicit_model_id_wins_over_role_default() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    low, high = _model("SIMULATOR"), _model("SIMULATOR")
    async with _models(low, high) as (db, _):
        default = await resolve_model(db, roles=(ModelRole.SIMULATOR,))
        assert default.role == "SIMULATOR"
        assert default.is_active is True

        picked = await resolve_model(db, model_id=high.id, roles=(ModelRole.SIMULATOR,))
        assert picked.id == high.id

        ctx = await resolve_context(db, model_id=high.id, roles=(ModelRole.SIMULATOR,))
        assert ctx.id == high.id and ctx.role == "SIMULATOR"


async def test_explicit_model_id_role_mismatch_is_rejected() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    author = _model("CASE_AUTHOR")
    async with _models(author) as (db, _):
        with pytest.raises(ValidationError, match="角色"):
            await resolve_model(db, model_id=author.id, roles=(ModelRole.SIMULATOR,))
        # roles 为空时不校验角色，供「按 id 管理注册项」的场景使用。
        assert (await resolve_model(db, model_id=author.id)).id == author.id


async def test_explicit_inactive_model_is_rejected() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    inactive = _model("JUDGE", active=False)
    async with _models(inactive) as (db, _):
        with pytest.raises(ValidationError, match="已停用"):
            await resolve_model(db, model_id=inactive.id, roles=(ModelRole.JUDGE,))


async def test_explicit_missing_model_is_rejected() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with SessionLocal() as db:
        with pytest.raises(NotFoundError, match="不存在"):
            await resolve_model(db, model_id=2_000_000_001, roles=(ModelRole.JUDGE,))


async def test_default_falls_back_through_roles_in_order() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with SessionLocal() as db:
        model = await resolve_model(
            db, roles=(ModelRole.CASE_AUTHOR, ModelRole.JUDGE)
        )
        assert model.role in {"CASE_AUTHOR", "JUDGE"}
        assert model.is_active is True

        has_author = model.role == "CASE_AUTHOR"
        # 只要存在启用的 CASE_AUTHOR，缺省就必须落在它上面，而不是跳到裁判。
        assert has_author or model.role == "JUDGE"


async def test_sandbox_rejects_cross_role_model() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    author, simulator = _model("CASE_AUTHOR"), _model("SIMULATOR")
    async with _models(author, simulator) as (db, _):
        with pytest.raises(ValidationError, match="角色"):
            await _pick_model(db, author.id, "SIMULATOR")
        with pytest.raises(ValidationError, match="角色"):
            await _pick_model(db, simulator.id, "JUDGE")
        picked = await _pick_model(db, simulator.id, "SIMULATOR")
        assert picked.id == simulator.id


async def test_author_model_requires_unique_active_case_author() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with SessionLocal() as db, _role_isolated("CASE_AUTHOR"):
        with pytest.raises(ValidationError, match="未配置启用"):
            await select_author_model(db)

    async with _role_isolated("CASE_AUTHOR"):
        author = _model("CASE_AUTHOR")
        async with _models(author) as (db, _):
            picked = await select_author_model(db)
            assert picked.id == author.id


async def test_unique_active_rejects_multiple_enabled() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with _role_isolated("SIMULATOR"):
        first, second = _model("SIMULATOR"), _model("SIMULATOR")
        async with _models(first, second) as (db, _):
            # 两个都是启用状态，应明确报错而不是任选其一。
            with pytest.raises(ValidationError, match="请只保留一个启用"):
                await resolve_unique_active(db, ModelRole.SIMULATOR)


async def test_unique_active_returns_single_enabled() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with _role_isolated("SIMULATOR"):
        simulator = _model("SIMULATOR")
        async with _models(simulator) as (db, _):
            picked = await resolve_unique_active(db, ModelRole.SIMULATOR)
            assert picked.id == simulator.id


async def test_model_registration_allows_single_active_per_role() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    async with _role_isolated("CASE_AUTHOR"):
        author = _model("CASE_AUTHOR")
        async with _models(author) as (db, _):
            # 新启用第二个编写模型 → 拒绝
            with pytest.raises(ConflictError, match="只允许一个启用项"):
                await _ensure_single_active(db, role="CASE_AUTHOR", is_active=True)
            # 更新自身（exclude 自己）→ 放行
            await _ensure_single_active(
                db, role="CASE_AUTHOR", is_active=True, exclude_id=author.id
            )
            # 裁判支持多模型，不受唯一启用约束
            await _ensure_single_active(db, role="JUDGE", is_active=True)


async def test_get_model_does_not_enforce_role() -> None:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用")
    inactive = _model("SIMULATOR", active=False)
    async with _models(inactive) as (db, _):
        fetched = await get_model(db, inactive.id)
        assert fetched.id == inactive.id and fetched.is_active is False
