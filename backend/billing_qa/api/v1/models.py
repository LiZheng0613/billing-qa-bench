"""模型注册接口。"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.api.v1.serializers import model_to_out
from billing_qa.core.crypto import decrypt_value, encrypt_value
from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.security import get_current_operator
from billing_qa.db.session import get_db
from billing_qa.models.enums import ModelConnectionType, ModelRole
from billing_qa.models.model_config import ModelConfig
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.model_config import (
    ModelConfigCreate,
    ModelConfigOut,
    ModelConfigUpdate,
    ModelTestResult,
    OpenCodeCatalogAgentOut,
    OpenCodeCatalogModelOut,
    OpenCodeCatalogOut,
    OpenCodeCatalogRequest,
)
from billing_qa.services.llm.client import LLMClient, ModelContext
from billing_qa.services.llm.opencode_catalog import fetch_opencode_catalog

router = APIRouter(
    prefix="/models", tags=["模型注册"], dependencies=[Depends(get_current_operator)]
)


async def _get_model(db: AsyncSession, model_id: int) -> ModelConfig:
    model = await db.get(ModelConfig, model_id)
    if model is None:
        raise NotFoundError("模型不存在")
    return model


# 只允许一个启用项的角色：用例编写与动态模拟器由系统配置统一决定，
# 裁判支持多模型 AVG / VETO，不在此列。
_SINGLE_ACTIVE_ROLES = {ModelRole.CASE_AUTHOR.value, ModelRole.SIMULATOR.value}


async def _ensure_single_active(
    db: AsyncSession,
    *,
    role: str,
    is_active: bool,
    exclude_id: int | None = None,
) -> None:
    """同一角色只允许一个启用项，避免「配了多个、实际用哪个不确定」。"""
    if not is_active or role not in _SINGLE_ACTIVE_ROLES:
        return
    query = select(ModelConfig).where(
        ModelConfig.role == role, ModelConfig.is_active.is_(True)
    )
    if exclude_id is not None:
        query = query.where(ModelConfig.id != exclude_id)
    existing = (
        await db.execute(query.order_by(ModelConfig.id.asc()).limit(1))
    ).scalar_one_or_none()
    if existing is None:
        return
    label = "用例编写模型" if role == ModelRole.CASE_AUTHOR.value else "模拟器模型"
    raise ConflictError(
        f"{label}只允许一个启用项：{existing.name}（id={existing.id}）已启用，"
        "请先停用它再启用新模型"
    )


def _compose_serve_base(serve_url: str, serve_port: int | None) -> str:
    """把 serve 地址与端口拼成根地址；地址里已带端口时不重复拼接。"""
    base = serve_url.rstrip("/")
    if not serve_port:
        return base
    netloc = urlsplit(base).netloc
    if ":" in netloc.rsplit("]", 1)[-1]:
        return base
    return f"{base}:{serve_port}"


def _apply_connection_defaults(model: ModelConfig) -> None:
    """OpenCode 模式下同步 base_url / model_name，并校验必填项。"""
    if model.connection_type == ModelConnectionType.OPENCODE_SERVE.value:
        if not model.serve_url:
            raise ValidationError("OpenCode Serve 接入需填写 serve_url")
        model.base_url = _compose_serve_base(model.serve_url, model.serve_port)
        if not model.model_name:
            model.model_name = model.opencode_model or model.opencode_agent or "opencode-default"
        if not model.provider or model.provider == "openai-compatible":
            model.provider = "opencode-serve"
    else:
        if not model.base_url:
            raise ValidationError("OpenAI 兼容接入需填写 base_url")
        if not model.model_name:
            raise ValidationError("OpenAI 兼容接入需填写 model_name")


@router.get("", response_model=Page[ModelConfigOut], summary="模型列表")
async def list_models(
    role: ModelRole | None = None,
    connection_type: ModelConnectionType | None = None,
    is_active: bool | None = None,
    keyword: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[ModelConfigOut]:
    conditions = []
    if role:
        conditions.append(ModelConfig.role == role)
    if connection_type:
        conditions.append(ModelConfig.connection_type == connection_type)
    if is_active is not None:
        conditions.append(ModelConfig.is_active.is_(is_active))
    if keyword:
        like = f"%{keyword}%"
        conditions.append(ModelConfig.name.ilike(like) | ModelConfig.code.ilike(like))
    total = (
        await db.execute(select(func.count()).select_from(ModelConfig).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ModelConfig)
            .where(*conditions)
            .order_by(ModelConfig.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[ModelConfigOut](
        items=[model_to_out(item) for item in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ModelConfigOut, summary="注册模型")
async def create_model(
    payload: ModelConfigCreate, db: AsyncSession = Depends(get_db)
) -> ModelConfigOut:
    exists = (
        await db.execute(select(ModelConfig.id).where(ModelConfig.code == payload.code))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"模型编码 {payload.code} 已存在")
    await _ensure_single_active(
        db, role=payload.role.value, is_active=payload.is_active
    )
    data = payload.model_dump(exclude={"api_key", "serve_password"})
    model = ModelConfig(**data)
    _apply_connection_defaults(model)
    model.api_key_encrypted = encrypt_value(payload.api_key)
    model.serve_password_encrypted = encrypt_value(payload.serve_password)
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model_to_out(model)


@router.post(
    "/opencode/catalog",
    response_model=OpenCodeCatalogOut,
    summary="拉取 OpenCode 可用模型与 Agent",
)
async def opencode_catalog(
    payload: OpenCodeCatalogRequest, db: AsyncSession = Depends(get_db)
) -> OpenCodeCatalogOut:
    serve_url = payload.serve_url
    serve_port = payload.serve_port
    username = payload.serve_username
    password = payload.serve_password
    directory = payload.opencode_directory

    # 编辑既有模型时允许留空密码，复用库中已加密的凭据。
    if payload.model_id is not None:
        stored = await db.get(ModelConfig, payload.model_id)
        if stored is not None and stored.connection_type == ModelConnectionType.OPENCODE_SERVE.value:
            serve_url = serve_url or stored.serve_url
            serve_port = serve_port or stored.serve_port
            username = username or stored.serve_username
            directory = directory or stored.opencode_directory
            if not password:
                password = decrypt_value(stored.serve_password_encrypted)

    if not serve_url:
        raise ValidationError("请先填写 OpenCode Serve 地址")

    base_url = _compose_serve_base(serve_url, serve_port or 4096)
    catalog = await fetch_opencode_catalog(
        base_url,
        username=username,
        password=password,
        directory=directory,
        timeout_seconds=payload.timeout_seconds,
    )
    return OpenCodeCatalogOut(
        serve_url=catalog.serve_url,
        models=[
            OpenCodeCatalogModelOut(
                value=item.value,
                provider_id=item.provider_id,
                model_id=item.model_id,
                name=item.name,
                status=item.status,
                enabled=item.enabled,
                context_limit=item.context_limit,
                output_limit=item.output_limit,
            )
            for item in catalog.models
        ],
        agents=[
            OpenCodeCatalogAgentOut(
                id=item.id,
                name=item.name,
                mode=item.mode,
                hidden=item.hidden,
            )
            for item in catalog.agents
        ],
    )


@router.get("/{model_id}", response_model=ModelConfigOut, summary="模型详情")
async def get_model(model_id: int, db: AsyncSession = Depends(get_db)) -> ModelConfigOut:
    return model_to_out(await _get_model(db, model_id))


@router.put("/{model_id}", response_model=ModelConfigOut, summary="更新模型")
async def update_model(
    model_id: int, payload: ModelConfigUpdate, db: AsyncSession = Depends(get_db)
) -> ModelConfigOut:
    model = await _get_model(db, model_id)
    data = payload.model_dump(exclude_unset=True, exclude={"api_key", "serve_password"})
    final_role = data.get("role", model.role)
    if isinstance(final_role, ModelRole):
        final_role = final_role.value
    final_active = data.get("is_active", model.is_active)
    await _ensure_single_active(
        db,
        role=str(final_role),
        is_active=bool(final_active),
        exclude_id=model.id,
    )
    for key, value in data.items():
        setattr(model, key, value)
    _apply_connection_defaults(model)
    if payload.api_key is not None and "*" not in payload.api_key:
        model.api_key_encrypted = encrypt_value(payload.api_key)
    if payload.serve_password is not None and "*" not in payload.serve_password:
        model.serve_password_encrypted = encrypt_value(payload.serve_password)
    await db.commit()
    await db.refresh(model)
    return model_to_out(model)


@router.delete("/{model_id}", response_model=MessageResponse, summary="删除模型")
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)) -> MessageResponse:
    model = await _get_model(db, model_id)
    code = model.code
    await db.delete(model)
    await db.commit()
    return MessageResponse(message=f"模型 {code} 已删除")


@router.post("/{model_id}/test", response_model=ModelTestResult, summary="模型连通性测试")
async def test_model(model_id: int, db: AsyncSession = Depends(get_db)) -> ModelTestResult:
    model = await _get_model(db, model_id)
    context = ModelContext.from_orm(model)
    started = time.perf_counter()
    try:
        async with LLMClient(context) as client:
            result = await client.ping()
        return ModelTestResult(
            ok=True,
            connection_type=model.connection_type,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            content=result.content[:500],
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            message="连接成功",
        )
    except Exception as exc:
        return ModelTestResult(
            ok=False,
            connection_type=model.connection_type,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            message=f"{type(exc).__name__}: {exc}",
        )
