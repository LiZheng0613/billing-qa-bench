"""演示数据：Mock 环境、模型与三类用例（用于开箱自测/前端演示）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.config import settings
from billing_qa.core.crypto import encrypt_value
from billing_qa.models.enums import (
    CaseType,
    JudgeStrategy,
    ModelRole,
    RevisionChangeType,
    TemplateStrategy,
)
from billing_qa.models.environment import Environment, EnvironmentVariable
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.prompt_template import PromptTemplate
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.services.revision.refs import refresh_template_refs
from billing_qa.services.revision.snapshot import build_snapshot

DEMO_ENV_CODE = "QA-MOCK"
DEMO_JSON_ENV_CODE = "QA-MOCK-JSON"


async def _get_or_create_environment(
    db: AsyncSession, code: str, *, is_stream: bool
) -> Environment:
    env = (
        await db.execute(select(Environment).where(Environment.code == code))
    ).scalar_one_or_none()
    if env is not None:
        return env
    env = Environment(
        code=code,
        name="QA 问账环境（Mock 流式）" if is_stream else "QA 问账环境（Mock 非流式）",
        description="内置 Mock 被测系统，用于离线端到端自测与演示。",
        base_url=settings.mock_target_base_url,
        health_path="/health",
        api_method="POST",
        api_path="/api/v1/chat",
        headers_template={"X-Tenant-Id": "{{tenant_id}}"},
        body_template={
            "query": "{{query}}",
            "session_id": "{{session_id}}",
            "history": "{{history}}",
        },
        is_stream=is_stream,
        extractors={
            "final_answer": "$.data.answer",
            "session_id": "$.data.session_id",
            "intent": "$.data.intent",
            "generated_sql": "$.data.generated_sql",
            "api_params": "$.data.api_params",
            "raw_db_data": "$.data.raw_db_data",
        },
        pre_hook={
            "enabled": False,
            "method": "POST",
            "path": "/api/auth/token",
            "headers": {"X-Tenant-Id": "{{tenant_id}}"},
            "body": {"tenant_id": "{{tenant_id}}"},
            "token_jsonpath": "$.data.token",
            "token_var": "auth_token",
            "cache_seconds": 300,
        },
        timeout_seconds=60,
        verify_ssl=False,
        variables=[
            EnvironmentVariable(
                key="tenant_id",
                value_encrypted=encrypt_value("TENANT_QA_001"),
                is_secret=False,
                description="租户 ID",
            ),
            EnvironmentVariable(
                key="auth_token",
                value_encrypted=encrypt_value("mock-agent-token-2026"),
                is_secret=True,
                description="动态鉴权 Token（Pre-Hook 可覆盖）",
            ),
        ],
    )
    db.add(env)
    await db.flush()
    return env


async def _get_or_create_model(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    role: ModelRole,
    model_name: str,
    temperature: float,
) -> ModelConfig:
    model = (
        await db.execute(select(ModelConfig).where(ModelConfig.code == code))
    ).scalar_one_or_none()
    if model is not None:
        return model
    model = ModelConfig(
        code=code,
        name=name,
        role=role,
        provider="openai-compatible",
        base_url=settings.mock_llm_base_url,
        api_key_encrypted=encrypt_value("mock-key"),
        model_name=model_name,
        temperature=temperature,
        max_tokens=1024,
        timeout_seconds=30,
        rpm_limit=120,
        max_retries=2,
        response_format_json=True,
    )
    db.add(model)
    await db.flush()
    return model


async def _create_case(db: AsyncSession, data: dict) -> TestCase | None:
    exists = (
        await db.execute(select(TestCase.id).where(TestCase.code == data["code"]))
    ).scalar_one_or_none()
    if exists:
        return None
    case = TestCase(**data)
    case.current_version = 1
    case.created_by = "system"
    case.updated_by = "system"
    db.add(case)
    await db.flush()
    db.add(
        TestCaseRevision(
            case_id=case.id,
            version=1,
            change_type=RevisionChangeType.CREATE,
            operator="system",
            summary="演示数据初始化",
            snapshot=build_snapshot(case),
        )
    )
    return case


async def seed_demo_data(db: AsyncSession) -> None:
    env = await _get_or_create_environment(db, DEMO_ENV_CODE, is_stream=True)
    await _get_or_create_environment(db, DEMO_JSON_ENV_CODE, is_stream=False)

    simulator = await _get_or_create_model(
        db,
        code="MOCK-SIM",
        name="Mock 用户模拟器",
        role=ModelRole.SIMULATOR,
        model_name="mock-simulator",
        temperature=0.7,
    )
    judge = await _get_or_create_model(
        db,
        code="MOCK-JUDGE",
        name="Mock 主裁判",
        role=ModelRole.JUDGE,
        model_name="mock-judge",
        temperature=0.0,
    )
    judge2 = await _get_or_create_model(
        db,
        code="MOCK-JUDGE-2",
        name="Mock 交叉裁判",
        role=ModelRole.JUDGE,
        model_name="mock-judge-2",
        temperature=0.0,
    )

    templates = {
        template.code: template
        for template in (await db.execute(select(PromptTemplate))).scalars().all()
    }
    sim_template = templates.get("SIM_DEFAULT_BILLING_USER")
    judge_template = templates.get("JUDGE_DEFAULT_ZERO_TOLERANCE")
    if sim_template is None or judge_template is None:
        return

    common = {
        "environment_id": env.id,
        "judge_template_id": judge_template.id,
        "judge_template_strategy": TemplateStrategy.PINNED,
        "judge_template_version": 1,
        "simulator_template_id": sim_template.id,
        "simulator_template_strategy": TemplateStrategy.PINNED,
        "simulator_template_version": 1,
        "simulator_model_id": simulator.id,
        "status": "ACTIVE",
        "priority": "P1",
    }

    await _create_case(
        db,
        {
            **common,
            "code": "DEMO-SINGLE-001",
            "name": "单轮 · 订单含税金额与笔数零容错",
            "description": "验证金额、笔数与底层取数完全一致，命中数值精确比对与双裁判平均分。",
            "case_type": CaseType.SINGLE,
            "tags": ["账单查询", "金额核对", "P0"],
            "query": "请帮我查一下订单 A2024001 的含税金额和明细笔数",
            "expected_answer": "订单 A2024001 含税金额为 ¥12,345.67，共 3 笔明细。",
            "expected_rules": {
                "amounts": [{"label": "含税金额", "value": 12345.67, "tolerance": 0.01}],
                "counts": [{"label": "明细笔数", "value": 3}],
                "must_contain": ["A2024001"],
            },
            "judge_model_ids": [judge.id, judge2.id],
            "judge_strategy": JudgeStrategy.AVG,
            "pass_threshold": 60,
        },
    )

    await _create_case(
        db,
        {
            **common,
            "code": "DEMO-SINGLE-FAIL",
            "name": "单轮 · 数据幻觉红线（Mock 注入）",
            "description": "裁判命中 [MOCK:FAIL] 标记，数据准确性归零，验证一票否决与失败归因。",
            "case_type": CaseType.SINGLE,
            "tags": ["红线用例", "数据幻觉"],
            "query": "请帮我查一下订单 A2024002 的含税金额和明细笔数",
            "expected_answer": "[MOCK:FAIL] 订单 A2024002 含税金额为 ¥5,678.90，共 2 笔明细。",
            "expected_rules": {
                "amounts": [{"label": "含税金额", "value": 5678.90, "tolerance": 0.01}],
                "counts": [{"label": "明细笔数", "value": 2}],
            },
            "judge_model_ids": [judge.id],
            "judge_strategy": JudgeStrategy.VETO,
            "pass_threshold": 60,
        },
    )

    await _create_case(
        db,
        {
            **common,
            "code": "DEMO-STATIC-001",
            "name": "静态多轮 · 指代继承与明细追问",
            "description": "第二轮使用“那…”指代上一轮订单，验证上下文继承与轮次级规则校验。",
            "case_type": CaseType.STATIC_MULTI,
            "tags": ["多轮", "上下文指代"],
            "turns": [
                {
                    "index": 1,
                    "query": "帮我查下订单 A2024001 的费用明细",
                    "expected_rules": {
                        "amounts": [{"label": "含税金额", "value": 12345.67, "tolerance": 0.01}],
                        "counts": [{"label": "明细笔数", "value": 3}],
                    },
                },
                {
                    "index": 2,
                    "query": "那这笔订单的含税总额到底是多少？",
                    "expected_rules": {
                        "amounts": [{"label": "含税总额", "value": 12345.67, "tolerance": 0.01}]
                    },
                },
            ],
            "session_expected": {
                "expected_answer": "两轮均围绕订单 A2024001，含税总额 ¥12,345.67，共 3 笔明细。",
                "expected_rules": {
                    "amounts": [{"label": "含税总额", "value": 12345.67, "tolerance": 0.01}]
                },
            },
            "fail_fast": False,
            "judge_model_ids": [judge.id],
            "judge_strategy": JudgeStrategy.AVG,
            "pass_threshold": 60,
        },
    )

    await _create_case(
        db,
        {
            **common,
            "code": "DEMO-DYNAMIC-001",
            "name": "动态多轮 · 隐藏信息挤牙膏式释放",
            "description": "用户不主动给订单号，仅当客服反问时才释放隐藏条件，验证目标驱动闭环。",
            "case_type": CaseType.DYNAMIC_MULTI,
            "tags": ["动态多轮", "隐藏信息", "目标驱动"],
            "user_goal": "在不主动提供订单号的前提下，通过多轮追问拿到订单 A2024001 的含税金额与明细笔数。",
            "initial_query": "我想核对一下这个月的账单金额和明细，但订单号一时找不到了。",
            "persona_description": "杭州某科技公司财务经理，说话口语化，关注金额与明细，习惯先描述诉求再补充单号。",
            "hidden_context": [
                {
                    "key": "order_no",
                    "content": "A2024001",
                    "release_hint": "订单号",
                },
                {
                    "key": "period",
                    "content": "2026-10",
                    "release_hint": "账期",
                },
            ],
            "max_turns": 5,
            "terminate_conditions": {"keywords": ["¥12,345.67"]},
            "expected_answer": "最终应给出订单 A2024001 含税金额 ¥12,345.67，共 3 笔明细。",
            "expected_rules": {
                "amounts": [{"label": "含税金额", "value": 12345.67, "tolerance": 0.01}],
                "counts": [{"label": "明细笔数", "value": 3}],
            },
            "judge_model_ids": [judge.id, judge2.id],
            "judge_strategy": JudgeStrategy.AVG,
            "pass_threshold": 60,
        },
    )

    await _create_case(
        db,
        {
            **common,
            "code": "DEMO-DYNAMIC-MAXTURNS",
            "name": "动态多轮 · 最大轮次熔断",
            "description": "模拟器持续追问不收敛，验证 MAX_TURNS_EXCEEDED 熔断与记为不通过。",
            "case_type": CaseType.DYNAMIC_MULTI,
            "tags": ["动态多轮", "熔断"],
            "user_goal": "[MOCK:MAXTURNS] 验证最大轮次熔断行为",
            "initial_query": "我想了解订单 A2024001 的账单构成。",
            "persona_description": "谨慎的财务人员，会不断追问不同细节。",
            "hidden_context": [
                {"key": "order_no", "content": "A2024001", "release_hint": "订单号"}
            ],
            "max_turns": 3,
            "expected_answer": "应明确说明金额与笔数并在 3 轮内收敛。",
            "judge_model_ids": [judge.id],
            "judge_strategy": JudgeStrategy.AVG,
            "pass_threshold": 60,
        },
    )

    await refresh_template_refs(db)
    await db.commit()
