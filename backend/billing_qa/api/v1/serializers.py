"""ORM → Schema 序列化辅助。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.crypto import decrypt_value, mask_value
from billing_qa.models.environment import Environment
from billing_qa.models.execution import ExecutionRecord
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase
from billing_qa.schemas.case import CaseOut
from billing_qa.schemas.environment import BasicAuthOut, EnvironmentOut, EnvVariableOut
from billing_qa.schemas.model_config import ModelConfigOut
from billing_qa.schemas.task import ExecutionDetail, ExecutionListItem, TaskOut


def env_variable_to_out(variable: Any) -> EnvVariableOut:
    raw = decrypt_value(variable.value_encrypted) or ""
    return EnvVariableOut(
        id=variable.id,
        key=variable.key,
        value_masked=mask_value(raw) if variable.is_secret else raw,
        is_secret=variable.is_secret,
        description=variable.description,
        updated_at=variable.updated_at,
    )


def env_to_out(env: Environment) -> EnvironmentOut:
    # 显式组装：variables 需要按掩码规则转换，basic_auth 需要屏蔽密码
    fields = {
        name: getattr(env, name)
        for name in EnvironmentOut.model_fields
        if name not in {"variables", "basic_auth"} and hasattr(env, name)
    }
    basic = env.basic_auth or {}
    basic_out = (
        BasicAuthOut(
            username=str(basic.get("username") or ""),
            password_set=bool(basic.get("password_encrypted")),
        )
        if basic.get("username")
        else None
    )
    return EnvironmentOut(
        **fields,
        basic_auth=basic_out,
        variables=[env_variable_to_out(item) for item in env.variables],
    )


def model_to_out(model: ModelConfig) -> ModelConfigOut:
    data = ModelConfigOut.model_validate(model)
    raw_key = decrypt_value(model.api_key_encrypted)
    data.api_key_masked = mask_value(raw_key) if raw_key else ""
    raw_serve_password = decrypt_value(model.serve_password_encrypted)
    data.serve_password_masked = mask_value(raw_serve_password) if raw_serve_password else ""
    return data


def case_to_out(
    case: TestCase,
    *,
    env_name: str | None = None,
    simulator_code: str | None = None,
    judge_code: str | None = None,
) -> CaseOut:
    data = CaseOut.model_validate(case)
    data.environment_name = env_name
    data.simulator_template_code = simulator_code
    data.judge_template_code = judge_code
    return data


def execution_to_item(
    record: ExecutionRecord,
    *,
    task_name: str | None = None,
    case_code: str | None = None,
    case_name: str | None = None,
) -> ExecutionListItem:
    return ExecutionListItem(
        id=record.id,
        task_id=record.task_id,
        task_name=task_name,
        case_id=record.case_id,
        case_code=case_code,
        case_name=case_name,
        case_version=record.case_version,
        source=record.source,
        status=record.status,
        attempt_no=record.attempt_no,
        error_kind=record.error_kind,
        pass_flag=record.pass_flag,
        total_score=record.total_score,
        termination_status=record.termination_status,
        turns_used=record.turns_used,
        latency_ms=record.latency_ms,
        ttft_ms=record.ttft_ms,
        simulator_tokens=record.simulator_tokens,
        judge_tokens=record.judge_tokens,
        grading_mode=record.grading_mode,
        lucky_pass=record.lucky_pass,
        root_cause=record.root_cause,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


async def build_execution_detail(
    db: AsyncSession,
    record: ExecutionRecord,
    *,
    task_name: str | None = None,
    case_code: str | None = None,
    case_name: str | None = None,
) -> ExecutionDetail:
    base = execution_to_item(
        record, task_name=task_name, case_code=case_code, case_name=case_name
    )
    raw_payloads = [
        {
            "turn": turn.turn_index,
            "request": turn.request_payload,
            "response": turn.response_raw,
        }
        for turn in record.turns
    ]
    return ExecutionDetail(
        **base.model_dump(),
        environment_id=record.environment_id,
        max_turns=record.max_turns,
        simulator_template_version=record.simulator_template_version,
        judge_template_version=record.judge_template_version,
        rule_result=record.rule_result,
        judge_result=record.judge_result,
        error_message=record.error_message,
        case_snapshot=record.case_snapshot,
        trace_uid=record.trace_uid,
        turns=record.turns,
        judges=record.judges,
        answer_grading=record.answer_grading,
        fact_results=record.fact_results,
        diagnostics=record.diagnostics,
        grader_version=record.grader_version,
        as_of=record.as_of,
        raw_request_response=raw_payloads,
    )


def task_to_out(
    task: BatchTask, *, env_name: str | None = None, run_count: int = 0
) -> TaskOut:
    data = TaskOut.model_validate(task)
    data.environment_name = env_name
    data.run_count = run_count
    return data
