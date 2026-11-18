"""执行上下文加载：环境、模型、Prompt 模板解析与引用策略。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from billing_qa.core.config import settings
from billing_qa.core.exceptions import ValidationError
from billing_qa.core.logging import get_logger
from billing_qa.models.enums import ModelRole, TemplateStrategy, TemplateType
from billing_qa.models.environment import Environment
from billing_qa.models.prompt_template import PromptTemplate, PromptTemplateVersion
from billing_qa.services.adapter.target_api import EnvContext
from billing_qa.services.llm.client import ModelContext
from billing_qa.services.llm.registry import resolve_context, resolve_unique_context

DEFAULT_SIMULATOR_CODE = "SIM_DEFAULT_BILLING_USER"
DEFAULT_JUDGE_CODE = "JUDGE_DEFAULT_ZERO_TOLERANCE"
DEFAULT_SELF_CHECK_CODE = "SELF_CHECK_DEFAULT"

logger = get_logger(__name__)


@dataclass
class ExecutionContext:
    env: EnvContext
    simulator_model: ModelContext
    judge_models: list[ModelContext]
    simulator_prompt: str
    simulator_prompt_version: int | None
    judge_prompt: str
    judge_prompt_version: int | None
    judge_strategy: str
    pass_threshold: float
    judge_temperature: float | None = None
    # ---- 失败追问自检模板（LLM 裁决失败后追问被测 Agent）----
    self_check_prompt: str | None = None
    self_check_prompt_version: int | None = None
    self_check_template_code: str | None = None
    warnings: list[str] = field(default_factory=list)


async def resolve_template(
    db: AsyncSession,
    template_id: int | None,
    strategy: str,
    pinned_version: int | None,
    expected_type: TemplateType,
    default_code: str,
) -> tuple[PromptTemplate, PromptTemplateVersion]:
    """按 LATEST / PINNED 策略解析模板版本。"""
    template: PromptTemplate | None = None
    if template_id:
        template = await db.get(PromptTemplate, template_id)
    if template is None:
        template = (
            await db.execute(select(PromptTemplate).where(PromptTemplate.code == default_code))
        ).scalar_one_or_none()
    if template is None:
        raise ValidationError(f"未找到可用的 Prompt 模板（默认编码 {default_code}）")
    if template.template_type != expected_type:
        raise ValidationError(
            f"模板 {template.code} 类型为 {template.template_type}，期望 {expected_type}"
        )

    target_version: int | None
    if strategy == TemplateStrategy.PINNED and pinned_version:
        target_version = pinned_version
    else:
        target_version = template.current_version

    version_row = (
        await db.execute(
            select(PromptTemplateVersion).where(
                PromptTemplateVersion.template_id == template.id,
                PromptTemplateVersion.version == target_version,
            )
        )
    ).scalar_one_or_none()
    if version_row is None:
        version_row = (
            await db.execute(
                select(PromptTemplateVersion)
                .where(PromptTemplateVersion.template_id == template.id)
                .order_by(PromptTemplateVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if version_row is None:
        raise ValidationError(f"模板 {template.code} 没有任何可用版本")
    return template, version_row


async def _load_model(
    db: AsyncSession, model_id: int | None, role: ModelRole
) -> ModelContext:
    """统一走模型注册表：显式 id 优先，缺省取该角色 id 最小的启用模型。"""
    return await resolve_context(db, model_id=model_id, roles=(role,))


async def load_execution_context(
    db: AsyncSession,
    snapshot: dict[str, Any],
    *,
    default_environment_id: int | None = None,
) -> ExecutionContext:
    """根据用例快照加载一次执行所需的全部依赖。"""
    environment_id = snapshot.get("environment_id") or default_environment_id
    if not environment_id:
        raise ValidationError("用例未绑定执行环境，且任务未指定环境")
    env = (
        await db.execute(
            select(Environment)
            .options(selectinload(Environment.variables))
            .where(Environment.id == environment_id)
        )
    ).scalar_one_or_none()
    if env is None:
        raise ValidationError("执行环境不存在或已被删除")
    if not env.is_active:
        raise ValidationError(f"环境 {env.name} 已停用")

    # 模拟器模型由系统配置统一决定：取唯一启用的 SIMULATOR，不再按用例选择。
    simulator_model = await resolve_unique_context(db, ModelRole.SIMULATOR)

    judge_ids: list[int] = [
        int(item) for item in (snapshot.get("judge_model_ids") or []) if str(item).isdigit()
    ]
    judge_models: list[ModelContext] = []
    for model_id in judge_ids:
        judge_models.append(await _load_model(db, model_id, ModelRole.JUDGE))
    if not judge_models:
        judge_models.append(await _load_model(db, None, ModelRole.JUDGE))

    overrides = snapshot.get("prompt_overrides") or {}
    simulator_override = (overrides.get("simulator") or "").strip()
    judge_override = (overrides.get("judge") or "").strip()

    simulator_version: int | None = None
    if simulator_override:
        simulator_prompt = simulator_override
    else:
        _, sim_row = await resolve_template(
            db,
            snapshot.get("simulator_template_id"),
            snapshot.get("simulator_template_strategy") or TemplateStrategy.LATEST,
            snapshot.get("simulator_template_version"),
            TemplateType.USER_SIMULATOR,
            DEFAULT_SIMULATOR_CODE,
        )
        simulator_prompt = sim_row.content
        simulator_version = sim_row.version

    judge_version: int | None = None
    if judge_override:
        judge_prompt = judge_override
    else:
        _, judge_row = await resolve_template(
            db,
            snapshot.get("judge_template_id"),
            snapshot.get("judge_template_strategy") or TemplateStrategy.LATEST,
            snapshot.get("judge_template_version"),
            TemplateType.LLM_JUDGE,
            DEFAULT_JUDGE_CODE,
        )
        judge_prompt = judge_row.content
        judge_version = judge_row.version

    threshold = snapshot.get("pass_threshold")

    # 追问自检模板：单用例 prompt_overrides.self_check 可覆写正文；
    # 内置模板缺失时降级为「自检不可用」，不阻塞正常执行。
    warnings: list[str] = []
    self_check_override = (overrides.get("self_check") or "").strip()
    self_check_prompt: str | None = None
    self_check_version: int | None = None
    self_check_code: str | None = None
    if self_check_override:
        self_check_prompt = self_check_override
        self_check_code = "PROMPT_OVERRIDE"
    else:
        try:
            self_check_tpl, self_check_row = await resolve_template(
                db,
                snapshot.get("self_check_template_id"),
                snapshot.get("self_check_template_strategy") or TemplateStrategy.LATEST,
                snapshot.get("self_check_template_version"),
                TemplateType.SELF_CHECK,
                DEFAULT_SELF_CHECK_CODE,
            )
            self_check_prompt = self_check_row.content
            self_check_version = self_check_row.version
            self_check_code = self_check_tpl.code
        except ValidationError as exc:
            logger.warning("追问自检模板不可用，自检将被跳过: %s", exc)
            warnings.append(f"追问自检模板不可用: {exc}")

    return ExecutionContext(
        env=EnvContext.from_orm(env),
        simulator_model=simulator_model,
        judge_models=judge_models,
        simulator_prompt=simulator_prompt,
        simulator_prompt_version=simulator_version,
        judge_prompt=judge_prompt,
        judge_prompt_version=judge_version,
        judge_strategy=snapshot.get("judge_strategy") or "AVG",
        pass_threshold=float(threshold) if threshold is not None else settings.pass_threshold,
        self_check_prompt=self_check_prompt,
        self_check_prompt_version=self_check_version,
        self_check_template_code=self_check_code,
        warnings=warnings,
    )
