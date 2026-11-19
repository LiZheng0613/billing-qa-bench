"""环境与被测接口配置接口。"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_qa.api.v1.serializers import env_to_out, env_variable_to_out
from billing_qa.core.authz import require_module_access
from billing_qa.core.context import CurrentUser
from billing_qa.core.crypto import decrypt_value, encrypt_value, mask_value
from billing_qa.core.exceptions import ConflictError, NotFoundError, TargetApiError, ValidationError
from billing_qa.core.permissions import PermissionCode
from billing_qa.core.security import get_current_operator, get_current_user
from billing_qa.db.session import get_db
from billing_qa.models.environment import Environment, EnvironmentVariable
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.environment import (
    DEFAULT_BODY_TEMPLATE,
    BasicAuthIn,
    EnvDebugChatRequest,
    EnvDebugChatResult,
    EnvironmentCreate,
    EnvironmentOut,
    EnvironmentUpdate,
    EnvironmentUsage,
    EnvPreviewRequest,
    EnvPreviewResponse,
    EnvVariableOut,
    HealthCheckResult,
    MtlsAuthIn,
    VariableBatchUpdate,
)
from billing_qa.services.adapter.renderer import find_variables, format_history, render_structure
from billing_qa.services.adapter.session import (
    init_session_id,
    resolve_session_mode,
    session_updates_from_response,
)
from billing_qa.services.adapter.target_api import (
    EnvContext,
    TargetApiAdapter,
    _sanitize_headers,
    basic_auth_header,
    now_iso,
)

router = APIRouter(
    prefix="/environments",
    tags=["环境配置"],
    dependencies=[
        Depends(
            require_module_access(
                "environment:read",
                ("environment:write", "environment:secret_read"),
            )
        )
    ],
)


def _can_read_secret(user: CurrentUser) -> bool:
    """拥有环境密钥读取权限时才回显明文。"""
    return PermissionCode.ENVIRONMENT_SECRET_READ.value in user.permissions


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


def _encode_basic_auth(config: BasicAuthIn | None, existing: dict | None) -> dict | None:
    """把 Basic 鉴权配置转为存储结构（密码加密；留空表示沿用旧密码）。"""
    if config is None:
        return None
    password = (config.password or "").strip()
    if password:
        encrypted = encrypt_value(password)
    else:
        encrypted = (existing or {}).get("password_encrypted")
        if not encrypted:
            raise ValidationError("Basic 鉴权密码不能为空")
    return {"username": config.username, "password_encrypted": encrypted}


def _encode_mtls(config: MtlsAuthIn | None, existing: dict | None) -> dict | None:
    """把 mTLS 配置转为存储结构。

    证书/私钥留空表示沿用旧值；私钥被替换时，口令以本次输入为准（可为空）。
    """
    if config is None:
        return None
    old = existing or {}
    cert = (config.client_cert_pem or "").strip()
    key = (config.client_key_pem or "").strip()
    encrypted_cert = encrypt_value(cert) if cert else old.get("client_cert_encrypted")
    encrypted_key = encrypt_value(key) if key else old.get("client_key_encrypted")
    if not encrypted_cert or not encrypted_key:
        raise ValidationError("mTLS 客户端证书与私钥不能为空")
    password = (config.key_password or "").strip()
    if password:
        encrypted_password = encrypt_value(password)
    elif key:
        # 替换私钥时清空旧口令，避免与新的私钥不匹配
        encrypted_password = None
    else:
        encrypted_password = old.get("key_password_encrypted")
    ca = (config.ca_cert_pem or "").strip()
    encrypted_ca = encrypt_value(ca) if ca else old.get("ca_cert_encrypted")
    payload: dict[str, Any] = {
        "client_cert_encrypted": encrypted_cert,
        "client_key_encrypted": encrypted_key,
    }
    if encrypted_password:
        payload["key_password_encrypted"] = encrypted_password
    if encrypted_ca:
        payload["ca_cert_encrypted"] = encrypted_ca
    return payload


@router.get("", response_model=Page[EnvironmentOut], summary="环境列表")
async def list_environments(
    keyword: str | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Page[EnvironmentOut]:
    conditions = []
    if keyword:
        like = f"%{keyword}%"
        conditions.append(Environment.name.ilike(like) | Environment.code.ilike(like))
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
        items=[env_to_out(item, reveal=_can_read_secret(user)) for item in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=EnvironmentOut, summary="创建环境")
async def create_environment(
    payload: EnvironmentCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
    user: CurrentUser = Depends(get_current_user),
) -> EnvironmentOut:
    exists = (
        await db.execute(select(Environment.id).where(Environment.code == payload.code))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"环境编码 {payload.code} 已存在")
    data = payload.model_dump(exclude={"variables", "basic_auth", "mtls"})
    data["pre_hook"] = payload.pre_hook.model_dump() if payload.pre_hook else None
    data["basic_auth"] = _encode_basic_auth(payload.basic_auth, None)
    data["mtls"] = _encode_mtls(payload.mtls, None)
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
    return env_to_out(await _get_env(db, env.id), reveal=_can_read_secret(user))


@router.get("/{env_id}", response_model=EnvironmentOut, summary="环境详情")
async def get_environment(
    env_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EnvironmentOut:
    return env_to_out(await _get_env(db, env_id), reveal=_can_read_secret(user))


@router.put("/{env_id}", response_model=EnvironmentOut, summary="更新环境")
async def update_environment(
    env_id: int,
    payload: EnvironmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EnvironmentOut:
    env = await _get_env(db, env_id)
    data = payload.model_dump(exclude_unset=True)
    if "pre_hook" in data:
        data["pre_hook"] = (
            payload.pre_hook.model_dump() if payload.pre_hook is not None else None
        )
    if "basic_auth" in data:
        data["basic_auth"] = _encode_basic_auth(payload.basic_auth, env.basic_auth)
    if "mtls" in data:
        data["mtls"] = _encode_mtls(payload.mtls, env.mtls)
    for key, value in data.items():
        setattr(env, key, value)
    await db.commit()
    return env_to_out(await _get_env(db, env_id), reveal=_can_read_secret(user))


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
    env_id: int,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[EnvVariableOut]:
    env = await _get_env(db, env_id)
    reveal = _can_read_secret(user)
    return [env_variable_to_out(item, reveal=reveal) for item in env.variables]


@router.put(
    "/{env_id}/variables",
    response_model=list[EnvVariableOut],
    summary="批量覆盖环境变量池",
)
async def replace_variables(
    env_id: int,
    payload: VariableBatchUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
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
            # 掩码值视为未修改，避免前端回写掩码覆盖真实密钥；
            # 已回显明文时按明文比较，允许保存包含 * 的真实值。
            if item.value is not None:
                current_plain = decrypt_value(current.value_encrypted) or ""
                if not (
                    item.value == mask_value(current_plain)
                    and item.value != current_plain
                ):
                    current.value_encrypted = encrypt_value(item.value)
            current.is_secret = item.is_secret
            current.description = item.description
    await db.commit()
    reveal = _can_read_secret(user)
    return [
        env_variable_to_out(item, reveal=reveal)
        for item in (await _get_env(db, env_id)).variables
    ]


@router.post(
    "/{env_id}/health-check", response_model=HealthCheckResult, summary="接口连通性探活"
)
async def health_check(env_id: int, db: AsyncSession = Depends(get_db)) -> HealthCheckResult:
    env = await _get_env(db, env_id)
    adapter = TargetApiAdapter(EnvContext.from_orm(env))
    try:
        ok, status_code, latency, message = await adapter.health_check()
    finally:
        adapter.close()
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


# ---------------------------------------------------------------------- #
# 请求报文预览（接口配置页使用）
# ---------------------------------------------------------------------- #
_RUNTIME_VARIABLES = {"query", "session_id", "history", "history_text", "turn", "timestamp_iso"}


def _prehook_variables(ctx: EnvContext) -> list[str]:
    """前置接口执行后会注入的变量名。"""
    hook = ctx.pre_hook or {}
    if not hook.get("enabled"):
        return []
    extract = hook.get("extract") or {}
    if extract:
        return [str(key) for key in extract]
    return [str(hook.get("token_var") or "auth_token")]


def _preview_query(case: TestCase) -> str:
    if case.query:
        return case.query
    if case.initial_query:
        return case.initial_query
    turns = case.turns or []
    if turns and isinstance(turns[0], dict):
        return str(turns[0].get("query") or "")
    return "示例提问"


def _template_placeholders(template: Any) -> list[str]:
    return find_variables(json.dumps(template or {}, ensure_ascii=False))


def _variable_origins(
    ctx: EnvContext, case_vars: dict[str, Any], mode: str
) -> dict[str, str]:
    names: list[str] = []
    for template in (ctx.headers_template, ctx.body_template or DEFAULT_BODY_TEMPLATE):
        for name in _template_placeholders(template):
            if name not in names:
                names.append(name)
    origins: dict[str, str] = {}
    prehook_names = _prehook_variables(ctx)
    for name in names:
        if name == "session_id":
            origins[name] = "SESSION"
        elif name in _RUNTIME_VARIABLES:
            origins[name] = "RUNTIME"
        elif name in case_vars:
            origins[name] = "CASE"
        elif name in prehook_names:
            origins[name] = "PREHOOK"
        elif name in ctx.variables:
            origins[name] = "ENV"
        else:
            origins[name] = "MISSING"
    return origins


def _render_preview_request(
    ctx: EnvContext,
    case_vars: dict[str, Any],
    query: str,
    session_id: str | None,
) -> tuple[dict[str, Any], Any, list[str]]:
    """用环境模板 + 用例变量渲染一次请求（不发起真实前置接口）。"""
    variables: dict[str, Any] = dict(ctx.variables)
    variables.update(case_vars)
    for name in _prehook_variables(ctx):
        # 预览不发起真实前置请求，用占位值让模板可完整渲染
        variables.setdefault(name, f"<前置接口注入:{name}>")
    variables.update(
        {
            "query": query,
            "session_id": session_id or "",
            "history": format_history([], style="messages"),
            "history_text": format_history([], style="text"),
            "turn": 1,
            "timestamp_iso": now_iso(),
        }
    )

    header_missing: list[str] = []
    headers = render_structure(ctx.headers_template or {}, variables, header_missing)
    # 与真实执行一致：配置了 Basic 鉴权且模板未显式覆盖时，补充 Authorization 头
    if not any(str(key).lower() == "authorization" for key in (headers or {})):
        auth_header = basic_auth_header(ctx.basic_username, ctx.basic_password)
        if auth_header:
            headers = dict(headers or {})
            headers["Authorization"] = auth_header
    missing: list[str] = list(header_missing)
    body = render_structure(
        ctx.body_template or dict(DEFAULT_BODY_TEMPLATE), variables, missing
    )
    return headers or {}, body, missing


@router.post(
    "/{env_id}/preview",
    response_model=EnvPreviewResponse,
    summary="请求报文预览（选示例用例带入变量）",
)
async def preview_request(
    env_id: int,
    payload: EnvPreviewRequest,
    db: AsyncSession = Depends(get_db),
) -> EnvPreviewResponse:
    """只读预览：用示例用例的请求变量与会话绑定配置渲染一次请求报文。"""
    env = await _get_env(db, env_id)
    case = await db.get(TestCase, payload.case_id)
    if case is None or case.deleted_at is not None:
        raise NotFoundError("示例用例不存在或已删除")

    ctx = EnvContext.from_orm(env)
    case_vars = case.request_variables or {}
    mode = resolve_session_mode(ctx.session_mode)
    query = _preview_query(case)
    session_id = init_session_id(mode, None)
    headers, body, missing = _render_preview_request(ctx, case_vars, query, session_id)

    warnings: list[str] = []
    unique_missing = list(dict.fromkeys(missing))
    if unique_missing:
        warnings.append(f"模板占位符缺少变量：{', '.join(unique_missing)}")
    if ctx.basic_username:
        warnings.append("已启用 Basic 鉴权，预览中 Authorization 头已掩码显示")
    if ctx.mtls_enabled:
        warnings.append("已启用 mTLS 双向证书认证，预览不展示证书与私钥内容")

    return EnvPreviewResponse(
        headers=_sanitize_headers({str(k): str(v) for k, v in (headers or {}).items()}),
        body=body,
        origins=_variable_origins(ctx, case_vars, mode),
        session_mode_resolved=mode,
        session_id=session_id,
        query=query,
        warnings=warnings,
    )


@router.post(
    "/{env_id}/debug-chat",
    response_model=EnvDebugChatResult,
    summary="调试发送单条消息（不落库）",
)
async def debug_chat(
    env_id: int,
    payload: EnvDebugChatRequest,
    db: AsyncSession = Depends(get_db),
) -> EnvDebugChatResult:
    """探活调试：选一条用例把消息直发目标环境，仅回显报文，不写执行记录。"""
    env = await _get_env(db, env_id)
    case = await db.get(TestCase, payload.case_id)
    if case is None or case.deleted_at is not None:
        raise NotFoundError("调试用例不存在或已删除")

    ctx = EnvContext.from_orm(env)
    case_vars = case.request_variables or {}
    mode = resolve_session_mode(ctx.session_mode)
    query = (payload.query or "").strip() or _preview_query(case)
    if not query:
        raise ValidationError("调试消息内容不能为空")
    session_id = init_session_id(mode, None)

    # 无论本次调用成败，都回显渲染后的请求报文，便于排查模板/鉴权/变量问题
    headers, body, _ = _render_preview_request(ctx, case_vars, query, session_id)
    request_preview = {
        "method": ctx.api_method,
        "url": ctx.chat_url,
        "headers": _sanitize_headers({str(k): str(v) for k, v in (headers or {}).items()}),
        "body": body,
    }

    adapter = TargetApiAdapter(ctx)
    started = time.perf_counter()
    result: Any = None
    ok = True
    message = "调试消息发送成功"
    try:
        result = await adapter.chat(
            query=query,
            session_id=session_id,
            extra_variables=case_vars,
        )
    except TargetApiError as exc:
        ok = False
        message = str(exc)
    except Exception as exc:
        ok = False
        message = f"{type(exc).__name__}: {exc}"
    finally:
        adapter.close()
    if result is not None and result.request_payload:
        # 成功时用真实发送的报文（含前置接口注入的变量）覆盖预渲染结果
        request_preview = result.request_payload
    latency = (
        result.latency_ms if result is not None else round((time.perf_counter() - started) * 1000, 2)
    )

    return EnvDebugChatResult(
        ok=ok,
        message=message,
        persisted=False,
        query=query,
        latency_ms=latency,
        status_code=result.status_code if result is not None else None,
        session_mode_resolved=mode,
        session_id=(
            session_updates_from_response(mode, session_id, result.session_id)
            if result is not None
            else session_id
        ),
        final_answer=(result.final_answer or None) if result is not None else None,
        intent=result.intent if result is not None else None,
        generated_sql=result.generated_sql if result is not None else None,
        api_params=result.api_params if result is not None else None,
        raw_db_data=result.raw_db_data if result is not None else None,
        tool_calls=result.tool_calls if result is not None else None,
        request_preview=request_preview,
        response=dict(result.response_payload or {}) if result is not None else {},
    )
