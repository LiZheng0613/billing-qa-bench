"""数据源与取数能力管理，以及取证执行（内部能力，外部 endpoint 由对接方提供）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.authz import require_module_access
from billing_qa.core.context import CurrentUser
from billing_qa.core.crypto import decrypt_value, encrypt_value
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.permissions import PermissionCode
from billing_qa.core.search import keyword_condition
from billing_qa.core.security import get_current_user
from billing_qa.db.base import now
from billing_qa.db.session import get_db
from billing_qa.models.capability import Capability, DataSource
from billing_qa.models.gold_query import GoldQuery
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.ops import (
    CapabilityCreate,
    CapabilityExecuteRequest,
    CapabilityExecuteResult,
    CapabilityOut,
    CapabilityUpdate,
    DataSourceCreate,
    DataSourceOut,
    DataSourceUpdate,
    GoldQueryOut,
    ProbeResult,
)
from billing_qa.services.capability.service import execute_capability, probe_data_source
from billing_qa.services.facts import iter_envelope_facts

router = APIRouter(
    tags=["取数资产"],
    dependencies=[
        Depends(
            require_module_access(
                "capability:read",
                ("capability:write", "capability:execute"),
            )
        )
    ],
)


# ---------------------------------------------------------------------- #
# 数据源
# ---------------------------------------------------------------------- #
async def _get_source(db: AsyncSession, source_id: int) -> DataSource:
    source = await db.get(DataSource, source_id)
    if source is None:
        raise NotFoundError("数据源不存在")
    return source


def _source_out(source: DataSource, *, reveal: bool = False) -> DataSourceOut:
    # ORM 上的 auth_config 是密文字符串，不能直接交给 dict 字段校验。
    fields = {
        name: getattr(source, name)
        for name in DataSourceOut.model_fields
        if name not in {"has_auth", "auth_config"} and hasattr(source, name)
    }
    data = DataSourceOut(**fields, has_auth=bool(source.auth_config))
    if reveal and source.auth_config:
        try:
            data.auth_config = json.loads(decrypt_value(source.auth_config) or "{}")
        except (json.JSONDecodeError, TypeError):
            data.auth_config = None
    return data


def _can_read_auth(user: CurrentUser) -> bool:
    """数据源鉴权配置属于敏感值，仅对拥有编辑权限的账号回显明文。"""
    return PermissionCode.CAPABILITY_WRITE.value in user.permissions


def _encode_auth(auth: dict | None) -> str | None:
    if auth is None:
        return None
    return encrypt_value(json.dumps(auth, ensure_ascii=False))


@router.get("/data-sources", response_model=Page[DataSourceOut], summary="数据源列表")
async def list_data_sources(
    kind: str | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Page[DataSourceOut]:
    conditions = []
    if kind:
        conditions.append(DataSource.kind == kind)
    if is_active is not None:
        conditions.append(DataSource.is_active.is_(is_active))
    total = (
        await db.execute(select(func.count()).select_from(DataSource).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(DataSource)
            .where(*conditions)
            .order_by(DataSource.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[DataSourceOut](
        items=[_source_out(row, reveal=_can_read_auth(user)) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("/data-sources", response_model=DataSourceOut, summary="新增数据源")
async def create_data_source(
    payload: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DataSourceOut:
    exists = (
        await db.execute(select(DataSource.id).where(DataSource.code == payload.code))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"数据源编码 {payload.code} 已存在")
    data = payload.model_dump()
    auth = data.pop("auth_config", None)
    source = DataSource(**data, auth_config=_encode_auth(auth))
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _source_out(source, reveal=_can_read_auth(user))


@router.get("/data-sources/{source_id}", response_model=DataSourceOut, summary="数据源详情")
async def get_data_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DataSourceOut:
    return _source_out(await _get_source(db, source_id), reveal=_can_read_auth(user))


@router.put("/data-sources/{source_id}", response_model=DataSourceOut, summary="编辑数据源")
async def update_data_source(
    source_id: int,
    payload: DataSourceUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> DataSourceOut:
    source = await _get_source(db, source_id)
    updates = payload.model_dump(exclude_unset=True)
    if "auth_config" in updates:
        updates["auth_config"] = _encode_auth(updates["auth_config"])
    for key, value in updates.items():
        setattr(source, key, value)
    await db.commit()
    await db.refresh(source)
    return _source_out(source, reveal=_can_read_auth(user))


@router.delete(
    "/data-sources/{source_id}", response_model=MessageResponse, summary="停用数据源"
)
async def delete_data_source(
    source_id: int, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    source = await _get_source(db, source_id)
    source.is_active = False
    await db.commit()
    return MessageResponse(message=f"数据源 {source.code} 已停用")


@router.post(
    "/data-sources/{source_id}/probe", response_model=ProbeResult, summary="探活数据源"
)
async def probe_source(
    source_id: int, db: AsyncSession = Depends(get_db)
) -> ProbeResult:
    source = await _get_source(db, source_id)
    result = await probe_data_source(source)
    source.last_probe_status = result["status"]
    source.last_probe_latency_ms = result["latency_ms"]
    source.last_probe_message = result["message"][:2000]
    source.last_probe_at = now()
    await db.commit()
    return ProbeResult(**result)


# ---------------------------------------------------------------------- #
# 取数能力
# ---------------------------------------------------------------------- #
async def _get_capability(db: AsyncSession, capability_id: int) -> Capability:
    capability = await db.get(Capability, capability_id)
    if capability is None:
        raise NotFoundError("取数能力不存在")
    return capability


@router.get("/capabilities", response_model=Page[CapabilityOut], summary="能力列表")
async def list_capabilities(
    status: str | None = None,
    is_active: bool | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[CapabilityOut]:
    conditions = []
    if status:
        conditions.append(Capability.status == status)
    if is_active is not None:
        conditions.append(Capability.is_active.is_(is_active))
    keyword_filter = keyword_condition((Capability.name, Capability.key), keyword)
    if keyword_filter is not None:
        conditions.append(keyword_filter)
    total = (
        await db.execute(select(func.count()).select_from(Capability).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(Capability)
            .where(*conditions)
            .order_by(Capability.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[CapabilityOut](
        items=[CapabilityOut.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("/capabilities", response_model=CapabilityOut, summary="新增能力")
async def create_capability(
    payload: CapabilityCreate, db: AsyncSession = Depends(get_db)
) -> CapabilityOut:
    exists = (
        await db.execute(select(Capability.id).where(Capability.key == payload.key))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"能力键 {payload.key} 已存在")
    await _ensure_source(db, payload.source_id)
    capability = Capability(**payload.model_dump())
    db.add(capability)
    await db.commit()
    await db.refresh(capability)
    return CapabilityOut.model_validate(capability)


@router.get("/capabilities/{capability_id}", response_model=CapabilityOut, summary="能力详情")
async def get_capability(
    capability_id: int, db: AsyncSession = Depends(get_db)
) -> CapabilityOut:
    return CapabilityOut.model_validate(await _get_capability(db, capability_id))


@router.put("/capabilities/{capability_id}", response_model=CapabilityOut, summary="编辑能力")
async def update_capability(
    capability_id: int, payload: CapabilityUpdate, db: AsyncSession = Depends(get_db)
) -> CapabilityOut:
    capability = await _get_capability(db, capability_id)
    updates = payload.model_dump(exclude_unset=True)
    await _ensure_source(db, updates.get("source_id"))
    versioned_fields = {"query_text", "params_schema", "param_binding", "source_id", "kind"}
    if versioned_fields & set(updates):
        capability.version += 1
    if updates.get("status") == "CERTIFIED":
        capability.certified_at = now()
    for key, value in updates.items():
        setattr(capability, key, value)
    await db.commit()
    await db.refresh(capability)
    return CapabilityOut.model_validate(capability)


@router.delete(
    "/capabilities/{capability_id}", response_model=MessageResponse, summary="停用能力"
)
async def delete_capability(
    capability_id: int, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    capability = await _get_capability(db, capability_id)
    capability.is_active = False
    await db.commit()
    return MessageResponse(message=f"能力 {capability.key} 已停用")


@router.post(
    "/capabilities/{capability_id}/evidence",
    response_model=CapabilityExecuteResult,
    summary="执行能力取证并生成 GoldQuery",
)
async def execute_evidence(
    capability_id: int,
    payload: CapabilityExecuteRequest,
    db: AsyncSession = Depends(get_db),
) -> CapabilityExecuteResult:
    capability = await _get_capability(db, capability_id)
    case = await db.get(TestCase, payload.case_id)
    if case is None or case.deleted_at is not None:
        raise NotFoundError("用例不存在")
    fact_ids = list(payload.fact_ids)
    if not fact_ids and case.answer_envelope:
        fact_ids = [
            str(fact.get("id"))
            for section, fact in iter_envelope_facts(case.answer_envelope)
            if section == "must" and fact.get("id")
        ]
    result, gold = await execute_capability(
        db,
        capability,
        case_id=case.id,
        params=payload.params,
        context=payload.context,
        fact_ids=fact_ids,
        session_id=payload.session_id,
        max_rows=payload.max_rows,
        timeout_seconds=payload.timeout_seconds,
    )
    await db.commit()
    await db.refresh(gold)
    return CapabilityExecuteResult(
        capability_id=capability.id,
        capability_key=capability.key,
        execution=result.to_dict(),
        gold_query=GoldQueryOut.model_validate(gold),
    )


@router.get(
    "/gold-queries", response_model=Page[GoldQueryOut], summary="取证记录（GoldQuery）"
)
async def list_gold_queries(
    case_id: int | None = None,
    capability_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[GoldQueryOut]:
    conditions = []
    if case_id:
        conditions.append(GoldQuery.case_id == case_id)
    if capability_id:
        conditions.append(GoldQuery.capability_id == capability_id)
    total = (
        await db.execute(select(func.count()).select_from(GoldQuery).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(GoldQuery)
            .where(*conditions)
            .order_by(GoldQuery.executed_at.desc(), GoldQuery.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[GoldQueryOut](
        items=[GoldQueryOut.model_validate(row) for row in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


async def _ensure_source(db: AsyncSession, source_id: int | None) -> None:
    if source_id is None:
        return
    source = await db.get(DataSource, source_id)
    if source is None:
        raise ValidationError(f"数据源 {source_id} 不存在")
