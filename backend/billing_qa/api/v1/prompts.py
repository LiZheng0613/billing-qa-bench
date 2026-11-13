"""Prompt 资产中心接口（含版本管理、引用影响面、沙盒试跑）。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.exceptions import ConflictError, NotFoundError, ValidationError
from billing_qa.core.security import get_current_operator
from billing_qa.db.seed import extract_variables
from billing_qa.db.session import get_db
from billing_qa.models.enums import ModelRole, TemplateType
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.prompt_template import PromptTemplate, PromptTemplateVersion
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.common import MessageResponse, Page
from billing_qa.schemas.prompt import (
    PromptCreate,
    PromptListItem,
    PromptOut,
    PromptUpdate,
    PromptUsage,
    PromptUsageCase,
    PromptVersionCreate,
    PromptVersionOut,
    SandboxRequest,
    SandboxResponse,
)
from billing_qa.services.adapter.renderer import format_history, render_text
from billing_qa.services.engine.judge import build_judge_variables
from billing_qa.services.llm.client import LLMClient, ModelContext
from billing_qa.services.llm.json_parser import parse_json_object

router = APIRouter(
    prefix="/prompts", tags=["Prompt 资产"], dependencies=[Depends(get_current_operator)]
)


async def _get_template(db: AsyncSession, template_id: int) -> PromptTemplate:
    template = (
        await db.execute(
            select(PromptTemplate).where(PromptTemplate.id == template_id)
        )
    ).scalar_one_or_none()
    if template is None:
        raise NotFoundError("Prompt 模板不存在")
    return template


async def _reload_template(db: AsyncSession, template_id: int) -> PromptTemplate:
    """强制重读模板及其版本列表。

    versions 为 selectin 关系，若实例在插入新版本前已加载，集合会保留旧快照；
    populate_existing 可确保返回给前端的版本历史是最新的。
    """
    return (
        await db.execute(
            select(PromptTemplate)
            .where(PromptTemplate.id == template_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _get_version(
    db: AsyncSession, template_id: int, version: int
) -> PromptTemplateVersion:
    row = (
        await db.execute(
            select(PromptTemplateVersion).where(
                PromptTemplateVersion.template_id == template_id,
                PromptTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"模板版本 v{version} 不存在")
    return row


@router.get("", response_model=Page[PromptListItem], summary="模板列表")
async def list_templates(
    template_type: TemplateType | None = None,
    keyword: str | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> Page[PromptListItem]:
    conditions = []
    if template_type:
        conditions.append(PromptTemplate.template_type == template_type)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(PromptTemplate.name.like(like) | PromptTemplate.code.like(like))
    if is_active is not None:
        conditions.append(PromptTemplate.is_active.is_(is_active))
    total = (
        await db.execute(select(func.count()).select_from(PromptTemplate).where(*conditions))
    ).scalar_one()
    rows = (
        await db.execute(
            select(PromptTemplate)
            .where(*conditions)
            .order_by(PromptTemplate.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page[PromptListItem](
        items=[PromptListItem.model_validate(item) for item in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=PromptOut, summary="新建模板")
async def create_template(
    payload: PromptCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> PromptOut:
    exists = (
        await db.execute(select(PromptTemplate.id).where(PromptTemplate.code == payload.code))
    ).scalar_one_or_none()
    if exists:
        raise ConflictError(f"模板编码 {payload.code} 已存在")
    template = PromptTemplate(
        code=payload.code,
        name=payload.name,
        template_type=payload.template_type,
        description=payload.description,
        current_version=1,
        ref_count=0,
        created_by=operator,
        updated_by=operator,
    )
    db.add(template)
    await db.flush()
    db.add(
        PromptTemplateVersion(
            template_id=template.id,
            version=1,
            content=payload.content,
            variables=extract_variables(payload.content),
            changelog=payload.changelog,
            created_by=operator,
        )
    )
    await db.commit()
    return PromptOut.model_validate(await _reload_template(db, template.id))


@router.get("/{template_id}", response_model=PromptOut, summary="模板详情（含全部版本）")
async def get_template(template_id: int, db: AsyncSession = Depends(get_db)) -> PromptOut:
    return PromptOut.model_validate(await _get_template(db, template_id))


@router.put("/{template_id}", response_model=PromptOut, summary="更新模板元信息")
async def update_template(
    template_id: int,
    payload: PromptUpdate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> PromptOut:
    template = await _get_template(db, template_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(template, key, value)
    template.updated_by = operator
    await db.commit()
    return PromptOut.model_validate(await _get_template(db, template_id))


@router.delete("/{template_id}", response_model=MessageResponse, summary="删除模板")
async def delete_template(
    template_id: int, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    template = await _get_template(db, template_id)
    if template.is_builtin:
        raise ConflictError("内置模板不允许删除")
    if template.ref_count > 0:
        raise ConflictError(
            f"模板仍被 {template.ref_count} 条用例引用，请先解除引用后再删除"
        )
    code = template.code
    await db.delete(template)
    await db.commit()
    return MessageResponse(message=f"模板 {code} 已删除")


@router.post("/{template_id}/versions", response_model=PromptOut, summary="发布新版本")
async def create_version(
    template_id: int,
    payload: PromptVersionCreate,
    db: AsyncSession = Depends(get_db),
    operator: str = Depends(get_current_operator),
) -> PromptOut:
    template = await _get_template(db, template_id)
    next_version = template.current_version + 1
    db.add(
        PromptTemplateVersion(
            template_id=template.id,
            version=next_version,
            content=payload.content,
            variables=extract_variables(payload.content),
            changelog=payload.changelog,
            created_by=operator,
        )
    )
    template.current_version = next_version
    template.updated_by = operator
    await db.commit()
    # versions 关系在插入新版本前已随模板加载，需强制重新查询以带回最新版本列表
    return PromptOut.model_validate(await _reload_template(db, template_id))


@router.get(
    "/{template_id}/versions/{version}",
    response_model=PromptVersionOut,
    summary="查看指定版本",
)
async def get_version(
    template_id: int, version: int, db: AsyncSession = Depends(get_db)
) -> PromptVersionOut:
    await _get_template(db, template_id)
    row = await _get_version(db, template_id, version)
    return PromptVersionOut.model_validate(row)


@router.get("/{template_id}/usage", response_model=PromptUsage, summary="引用影响面")
async def template_usage(
    template_id: int, db: AsyncSession = Depends(get_db)
) -> PromptUsage:
    template = await _get_template(db, template_id)
    rows = (
        await db.execute(
            select(TestCase).where(
                TestCase.deleted_at.is_(None),
                or_(
                    TestCase.simulator_template_id == template_id,
                    TestCase.judge_template_id == template_id,
                ),
            )
        )
    ).scalars().all()
    usage_cases: list[PromptUsageCase] = []
    for case in rows:
        if case.simulator_template_id == template_id:
            usage_cases.append(
                PromptUsageCase(
                    id=case.id,
                    code=case.code,
                    name=case.name,
                    strategy=case.simulator_template_strategy,
                    pinned_version=case.simulator_template_version,
                )
            )
        if case.judge_template_id == template_id:
            usage_cases.append(
                PromptUsageCase(
                    id=case.id,
                    code=case.code,
                    name=case.name,
                    strategy=case.judge_template_strategy,
                    pinned_version=case.judge_template_version,
                )
            )
    blockers: list[str] = []
    if template.is_builtin:
        blockers.append("内置模板不允许删除")
    if usage_cases:
        blockers.append(f"被 {len(usage_cases)} 处用例引用")
    return PromptUsage(
        template_id=template_id,
        ref_count=len(usage_cases),
        cases=usage_cases,
        can_delete=not blockers,
        delete_blockers=blockers,
    )


@router.post(
    "/{template_id}/sandbox", response_model=SandboxResponse, summary="沙盒单条试跑"
)
async def sandbox_run(
    template_id: int,
    payload: SandboxRequest,
    db: AsyncSession = Depends(get_db),
) -> SandboxResponse:
    template = await _get_template(db, template_id)
    expected_type = (
        TemplateType.USER_SIMULATOR
        if payload.mode == "SIMULATOR"
        else TemplateType.LLM_JUDGE
    )
    if template.template_type != expected_type:
        raise ValidationError(
            f"模板类型 {template.template_type} 与试跑模式 {payload.mode} 不匹配"
        )

    version_row = await _get_version(db, template_id, template.current_version)
    content = payload.content_override or version_row.content
    variables = await _build_sandbox_variables(db, payload)

    rendered, missing = render_text(content, variables)

    model = await _pick_model(db, payload.model_id, payload.mode)
    started = time.perf_counter()
    async with LLMClient(model) as client:
        result = await client.chat(
            [
                {
                    "role": "system",
                    "content": "你是严格的执行引擎，只能输出合法 JSON，不得输出 Markdown。",
                },
                {"role": "user", "content": rendered},
            ],
            json_mode=True,
            temperature=payload.temperature,
        )
    latency = round((time.perf_counter() - started) * 1000, 2)
    try:
        output = parse_json_object(result.content)
    except Exception:
        output = None
    return SandboxResponse(
        rendered_prompt=rendered,
        output=output,
        raw_content=result.content,
        missing_variables=missing,
        latency_ms=latency,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        model_name=f"{model.name}({model.model_name})",
    )


async def _pick_model(db: AsyncSession, model_id: int | None, mode: str) -> ModelContext:
    if model_id:
        model = await db.get(ModelConfig, model_id)
        if model is None:
            raise NotFoundError("指定模型不存在")
        return ModelContext.from_orm(model)
    role = ModelRole.SIMULATOR if mode == "SIMULATOR" else ModelRole.JUDGE
    model = (
        await db.execute(
            select(ModelConfig)
            .where(ModelConfig.role == role, ModelConfig.is_active.is_(True))
            .order_by(ModelConfig.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if model is None:
        raise ValidationError(f"未配置可用的 {role} 模型")
    return ModelContext.from_orm(model)


async def _build_sandbox_variables(
    db: AsyncSession, payload: SandboxRequest
) -> dict[str, Any]:
    variables: dict[str, Any]
    if payload.execution_id:
        record = await db.get(ExecutionRecord, payload.execution_id)
        if record is None:
            raise NotFoundError("历史执行快照不存在")
        turns = [
            {
                "turn_index": turn.turn_index,
                "query": turn.query,
                "final_answer": turn.final_answer,
                "raw_db_data": turn.raw_db_data,
            }
            for turn in record.turns
        ]
        if payload.mode == "JUDGE":
            variables = build_judge_variables(
                record.case_snapshot, turns, record.termination_status or "COMPLETED"
            )
        else:
            snapshot = record.case_snapshot or {}
            history: list[dict[str, str]] = []
            for turn in turns:
                history.append({"role": "user", "content": turn["query"] or ""})
                history.append({"role": "assistant", "content": turn["final_answer"] or ""})
            hidden_items = snapshot.get("hidden_context") or []
            variables = {
                "persona_description": snapshot.get("persona_description") or "企业财务人员",
                "user_goal": snapshot.get("user_goal") or "（未提供目标）",
                "hidden_context": "\n".join(
                    f"- {item.get('key')}: {item.get('content')}" for item in hidden_items
                )
                or "（无隐藏信息）",
                "current_turn": len(turns) + 1,
                "max_turns": snapshot.get("max_turns") or 5,
                "chat_history": format_history(history, style="text"),
            }
    elif payload.mode == "JUDGE":
        variables = {
            "case_type": "SINGLE",
            "chat_history": "（暂无对话）",
            "raw_db_data": "（无）",
            "expected_answer": "（未提供）",
            "actual_turns": 0,
            "max_turns": 1,
            "termination_status": "COMPLETED",
        }
    else:
        variables = {
            "persona_description": "企业财务人员",
            "user_goal": "（未提供目标）",
            "hidden_context": "（无隐藏信息）",
            "current_turn": 1,
            "max_turns": 5,
            "chat_history": "（暂无对话）",
        }
    if payload.variables:
        variables.update(payload.variables)
    return variables
