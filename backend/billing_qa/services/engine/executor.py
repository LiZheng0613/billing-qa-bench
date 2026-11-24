"""单用例执行器：单轮 / 静态多轮 / 动态多轮状态机 + 混合裁决。"""

from __future__ import annotations

import asyncio
import difflib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from billing_qa.core.config import settings
from billing_qa.core.exceptions import ValidationError
from billing_qa.core.logging import get_logger
from billing_qa.db.base import now
from billing_qa.db.session import SessionLocal
from billing_qa.models.enums import ExecutionSource, ExecutionStatus, TerminationStatus
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.gold_query import FactResult
from billing_qa.schemas.case import normalize_request_variables
from billing_qa.services.adapter.renderer import format_history, render_prompt
from billing_qa.services.adapter.session import (
    init_session_id,
    resolve_session_mode,
    session_updates_from_response,
)
from billing_qa.services.adapter.target_api import TargetApiAdapter, TargetResponse
from billing_qa.services.engine.context import ExecutionContext, load_execution_context
from billing_qa.services.engine.error_policy import classify_exception
from billing_qa.services.engine.judge import (
    JudgeOutcome,
    build_judge_variables,
    run_judges,
)
from billing_qa.services.engine.rule_evaluator import (
    RuleResult,
    evaluate_rules,
    merge_rule_results,
)
from billing_qa.services.engine.self_check import (
    SelfCheckResult,
    run_self_check,
)
from billing_qa.services.llm.client import LLMClient
from billing_qa.services.llm.json_parser import parse_structured

logger = get_logger(__name__)

DEFAULT_PERSONA = "你是一名真实的企业财务人员，说话口语化，关注金额、笔数与账期明细。"


def _json_value(value: Any) -> Any:
    """把判分结果中的非 JSON 原生对象降级为字符串，避免落库失败。"""
    if value is None or isinstance(value, dict | list | str | int | float | bool):
        return value
    return str(value)


def _legacy_rules_enabled(snapshot: dict[str, Any]) -> bool:
    """规则硬校验与答案契约判分互斥：仅 4 维评分（LEGACY）模式执行。"""
    return str(snapshot.get("grading_mode") or "LEGACY") != "ANSWER_CONTRACT"


@dataclass
class ConversationResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    termination: str = TerminationStatus.COMPLETED
    rule_result: dict[str, Any] | None = None
    simulator_tokens: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class RunOutcome:
    status: str
    pass_flag: bool
    total_score: float
    termination_status: str
    turns_used: int
    latency_ms: float
    ttft_ms: float | None
    simulator_tokens: int
    judge_tokens: int
    rule_result: dict[str, Any] | None
    judge_result: dict[str, Any] | None
    judges: list[JudgeOutcome]
    error_message: str | None = None
    # ---- 答案契约判分（仅 ANSWER_CONTRACT 填充）----
    grading_mode: str | None = None
    answer_grading: dict[str, Any] | None = None
    fact_results: list[dict[str, Any]] | None = None
    diagnostics: dict[str, Any] | None = None
    lucky_pass: bool | None = None
    root_cause: str | None = None
    grader_version: str | None = None
    as_of: str | None = None
    # ---- 失败追问自检（LLM 裁决失败后追问被测 Agent 的结论）----
    self_check: SelfCheckResult | None = None


class CaseExecutor:
    """执行单条用例并完整落库（执行记录 + 轮次轨迹 + 裁判明细）。"""

    async def execute(
        self,
        *,
        case_id: int,
        case_version: int,
        snapshot: dict[str, Any],
        task_id: int | None = None,
        source: str = ExecutionSource.BATCH,
        environment_id: int | None = None,
        attempt_no: int = 1,
        enable_self_check: bool = False,
    ) -> int:
        # 任务显式指定环境时，把它固化进本次执行快照，确保快照/轨迹与实际请求一致；
        # 未指定时保留用例自身绑定，由用例快照决定。
        if environment_id is not None:
            snapshot = {**snapshot, "environment_id": environment_id}
        # 兼容历史快照里的数字型请求变量：执行与落库前统一转成字符串。
        if snapshot.get("request_variables") is not None:
            snapshot = {
                **snapshot,
                "request_variables": normalize_request_variables(
                    snapshot.get("request_variables")
                ),
            }
        execution_id = await self._create_execution(
            case_id=case_id,
            case_version=case_version,
            snapshot=snapshot,
            task_id=task_id,
            source=source,
            environment_id=environment_id,
            attempt_no=attempt_no,
        )
        wall_started = time.perf_counter()
        adapter: TargetApiAdapter | None = None
        try:
            async with SessionLocal() as db:
                ctx = await load_execution_context(
                    db, snapshot, default_environment_id=environment_id
                )
            await self._bind_environment(execution_id, ctx.env.id)
            adapter = TargetApiAdapter(ctx.env)
            case_type = snapshot.get("case_type") or "SINGLE"

            if case_type == "SINGLE":
                conversation = await self._run_single(adapter, snapshot, execution_id)
            elif case_type == "STATIC_MULTI":
                conversation = await self._run_static(adapter, snapshot, execution_id)
            elif case_type == "DYNAMIC_MULTI":
                conversation = await self._run_dynamic(adapter, snapshot, execution_id, ctx)
            else:
                raise ValidationError(f"不支持的用例类型: {case_type}")

            wall_ms = round((time.perf_counter() - wall_started) * 1000, 2)
            outcome = await self._complete(ctx, snapshot, conversation, wall_ms)
            if enable_self_check:
                # 失败追问自检：LLM 裁决判失败时在同一会话追问被测 Agent。
                # 自检自身的异常不回滚执行结果，仅记录为自检失败。
                try:
                    outcome.self_check = await run_self_check(
                        adapter=adapter,
                        prompt=ctx.self_check_prompt,
                        template_code=ctx.self_check_template_code,
                        template_name=ctx.self_check_template_name,
                        template_version=ctx.self_check_prompt_version,
                        snapshot=snapshot,
                        turn_records=conversation.records,
                        judge_result=outcome.judge_result,
                        judges=outcome.judges,
                        fact_results=outcome.fact_results,
                        rule_result=outcome.rule_result,
                        total_score=outcome.total_score,
                        pass_threshold=ctx.pass_threshold,
                        status=outcome.status,
                        termination_status=outcome.termination_status,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("追问自检执行失败 execution=%s", execution_id)
                    outcome.self_check = SelfCheckResult.failed(
                        f"{type(exc).__name__}: {exc}",
                        trigger="自检追问调用异常",
                        template_code=ctx.self_check_template_code,
                        template_name=ctx.self_check_template_name,
                        template_version=ctx.self_check_prompt_version,
                    )
            await self._finalize(execution_id, outcome, ctx)
        except asyncio.CancelledError:
            logger.warning("执行被取消 execution=%s", execution_id)
            await self._mark_aborted(execution_id)
            raise
        except Exception as exc:
            logger.exception("用例执行失败 execution=%s", execution_id)
            await self._mark_error(execution_id, exc)
        finally:
            if adapter is not None:
                adapter.close()
        return execution_id

    # ------------------------------------------------------------------ #
    # 三种用例形态
    # ------------------------------------------------------------------ #
    async def _run_single(
        self, adapter: TargetApiAdapter, snapshot: dict[str, Any], execution_id: int
    ) -> ConversationResult:
        query = (snapshot.get("query") or "").strip()
        if not query:
            raise ValidationError("单轮用例缺少 query")
        case_vars = snapshot.get("request_variables") or {}
        mode = resolve_session_mode(adapter.env.session_mode)
        session_id = init_session_id(mode, execution_id)
        response = await adapter.chat(
            query, session_id=session_id, extra_variables=case_vars
        )
        effective_session = session_updates_from_response(
            mode, session_id, response.session_id
        )
        rule = (
            evaluate_rules(response.final_answer, snapshot.get("expected_rules"))
            if _legacy_rules_enabled(snapshot)
            else None
        )
        record = self._build_record(
            1, query, "STATIC", response, rule, session_id=effective_session
        )
        await self._save_turn(execution_id, record)
        return ConversationResult(
            records=[record],
            termination=TerminationStatus.COMPLETED,
            rule_result=rule.to_dict() if rule else None,
        )

    async def _run_static(
        self, adapter: TargetApiAdapter, snapshot: dict[str, Any], execution_id: int
    ) -> ConversationResult:
        turns: list[dict[str, Any]] = snapshot.get("turns") or []
        if not turns:
            raise ValidationError("静态多轮用例缺少 turns")
        fail_fast = bool(snapshot.get("fail_fast"))
        rules_enabled = _legacy_rules_enabled(snapshot)
        history: list[dict[str, str]] = []
        case_vars = snapshot.get("request_variables") or {}
        session_mode = resolve_session_mode(adapter.env.session_mode)
        session_id: str | None = init_session_id(session_mode, execution_id)
        records: list[dict[str, Any]] = []
        rules: list[RuleResult] = []
        notes: list[str] = []

        for index, turn in enumerate(turns, start=1):
            query = (turn.get("query") or "").strip()
            if not query:
                raise ValidationError(f"第 {index} 轮缺少 query")
            response = await adapter.chat(
                query,
                session_id=session_id,
                history=history,
                extra_variables=case_vars,
            )
            session_id = session_updates_from_response(
                session_mode, session_id, response.session_id
            )
            rule = (
                evaluate_rules(response.final_answer, turn.get("expected_rules"))
                if rules_enabled
                else None
            )
            record = self._build_record(
                index, query, "STATIC", response, rule, session_id=session_id
            )
            await self._save_turn(execution_id, record)
            records.append(record)
            if rule is not None:
                rules.append(rule)
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": response.final_answer})
            if rules_enabled and fail_fast and rule and rule.checks and not rule.passed:
                notes.append(f"第 {index} 轮规则校验失败，触发 fail_fast 中断")
                break

        merged = merge_rule_results(rules) if rules_enabled else None
        return ConversationResult(
            records=records,
            termination=TerminationStatus.COMPLETED,
            rule_result=merged.to_dict() if merged else None,
            notes=notes,
        )

    async def _run_dynamic(
        self,
        adapter: TargetApiAdapter,
        snapshot: dict[str, Any],
        execution_id: int,
        ctx: ExecutionContext,
    ) -> ConversationResult:
        max_turns = int(snapshot.get("max_turns") or 5)
        max_turns = max(1, min(max_turns, settings.max_turns_hard_limit))
        hidden_items: list[dict[str, Any]] = snapshot.get("hidden_context") or []
        released_keys: set[str] = set()
        history: list[dict[str, str]] = []
        case_vars = snapshot.get("request_variables") or {}
        session_mode = resolve_session_mode(adapter.env.session_mode)
        session_id: str | None = init_session_id(session_mode, execution_id)
        records: list[dict[str, Any]] = []
        rules: list[RuleResult] = []
        rules_enabled = _legacy_rules_enabled(snapshot)
        simulator_tokens = 0
        termination: str | None = None
        previous_query: str | None = None
        notes: list[str] = []
        terminate_keywords = [
            str(item) for item in ((snapshot.get("terminate_conditions") or {}).get("keywords") or [])
        ]

        async with LLMClient(ctx.simulator_model) as simulator:
            for turn_index in range(1, max_turns + 1):
                thought: str | None = None
                sim_status: str | None = None
                rendered_prompt: str | None = None
                sim_prompt_tokens = 0
                sim_completion_tokens = 0
                released_this_turn: list[dict[str, Any]] = []

                initial_query = (snapshot.get("initial_query") or "").strip()
                if turn_index == 1 and initial_query:
                    query = initial_query
                    input_source = "INITIAL"
                else:
                    variables = {
                        "persona_description": snapshot.get("persona_description")
                        or DEFAULT_PERSONA,
                        "user_goal": snapshot.get("user_goal") or "",
                        "hidden_context": self._format_hidden(hidden_items, released_keys),
                        "current_turn": turn_index,
                        "max_turns": max_turns,
                        "chat_history": format_history(history, style="text"),
                    }
                    rendered_prompt = render_prompt(
                        ctx.simulator_prompt, variables, strict=False
                    )
                    result = await simulator.chat(
                        [
                            {
                                "role": "system",
                                "content": "你是用户模拟器，必须严格按用户给定的模板输出合法 JSON，不得输出 Markdown。",
                            },
                            {"role": "user", "content": rendered_prompt},
                        ],
                        json_mode=True,
                    )
                    sim_prompt_tokens += result.prompt_tokens
                    sim_completion_tokens += result.completion_tokens
                    # 模拟器 token 立即计入总额：下方 GOAL_ACHIEVED / GIVE_UP / 复读机
                    # 熔断等 break 路径会直接退出轮次循环，绕过循环尾部的累加。
                    simulator_tokens += result.prompt_tokens + result.completion_tokens
                    data = parse_structured(
                        result.content, ["thought", "status", "query"]
                    )
                    thought = str(data.get("thought") or "")
                    sim_status = str(data.get("status") or "CONTINUE").upper()
                    if sim_status == "GOAL_ACHIEVED":
                        termination = TerminationStatus.GOAL_ACHIEVED
                        notes.append(f"第 {turn_index} 轮模拟器判定目标已达成")
                        break
                    if sim_status == "GIVE_UP":
                        termination = TerminationStatus.GIVE_UP
                        notes.append(f"第 {turn_index} 轮模拟器放弃追问")
                        break
                    query = str(data.get("query") or "").strip()
                    if not query:
                        termination = TerminationStatus.GIVE_UP
                        notes.append(f"第 {turn_index} 轮模拟器返回空 query，视为放弃")
                        break
                    if previous_query is not None:
                        similarity = difflib.SequenceMatcher(
                            None, previous_query, query
                        ).ratio()
                        if similarity > settings.repetition_similarity_threshold:
                            termination = TerminationStatus.REPETITION_ABORT
                            notes.append(
                                f"第 {turn_index} 轮与上一轮提问相似度 {similarity:.2f} "
                                f"超过 {settings.repetition_similarity_threshold}，触发复读机熔断"
                            )
                            break
                    input_source = "SIMULATOR"
                    released_this_turn = self._detect_released(
                        query, hidden_items, released_keys
                    )
                    for item in released_this_turn:
                        released_keys.add(str(item["key"]))

                response = await adapter.chat(
                    query,
                    session_id=session_id,
                    history=history,
                    extra_variables=case_vars,
                )
                session_id = session_updates_from_response(
                    session_mode, session_id, response.session_id
                )
                rule = (
                    evaluate_rules(response.final_answer, snapshot.get("expected_rules"))
                    if rules_enabled
                    else None
                )
                record = self._build_record(
                    turn_index, query, input_source, response, rule, session_id=session_id
                )
                record.update(
                    {
                        "simulator_thought": thought,
                        "simulator_status": sim_status,
                        "hidden_context_released": released_this_turn or None,
                        "rendered_prompt": rendered_prompt,
                        "simulator_prompt_tokens": sim_prompt_tokens,
                        "simulator_completion_tokens": sim_completion_tokens,
                    }
                )
                await self._save_turn(execution_id, record)
                records.append(record)
                if rule is not None:
                    rules.append(rule)
                history.append({"role": "user", "content": query})
                history.append({"role": "assistant", "content": response.final_answer})
                previous_query = query

                if terminate_keywords and any(
                    keyword and keyword in response.final_answer for keyword in terminate_keywords
                ):
                    termination = TerminationStatus.GOAL_ACHIEVED
                    notes.append(f"第 {turn_index} 轮命中提前终止关键词")
                    break

        if termination is None:
            termination = TerminationStatus.MAX_TURNS_EXCEEDED
            notes.append(f"达到最大轮次 {max_turns} 仍未达成目标")

        merged = merge_rule_results(rules) if rules_enabled else None
        return ConversationResult(
            records=records,
            termination=termination,
            # 动态多轮的规则期望针对最终回复，这里以最后一轮校验结果作为硬校验结论
            rule_result=(rules[-1].to_dict() if rules else merged.to_dict()) if merged else None,
            simulator_tokens=simulator_tokens,
            notes=notes,
        )

    # ------------------------------------------------------------------ #
    # 混合裁决
    # ------------------------------------------------------------------ #
    async def _complete(
        self,
        ctx: ExecutionContext,
        snapshot: dict[str, Any],
        conversation: ConversationResult,
        wall_ms: float,
    ) -> RunOutcome:
        ttfts = [
            float(item["ttft_ms"])
            for item in conversation.records
            if item.get("ttft_ms") is not None
        ]
        avg_ttft = round(sum(ttfts) / len(ttfts), 2) if ttfts else None
        base = RunOutcome(
            status=ExecutionStatus.FAIL,
            pass_flag=False,
            total_score=0.0,
            termination_status=conversation.termination,
            turns_used=len(conversation.records),
            latency_ms=wall_ms,
            ttft_ms=avg_ttft,
            simulator_tokens=conversation.simulator_tokens,
            judge_tokens=0,
            rule_result=conversation.rule_result,
            judge_result=None,
            judges=[],
        )
        if conversation.notes:
            base.judge_result = {"notes": conversation.notes}

        grading_mode = str(snapshot.get("grading_mode") or "LEGACY")
        base.grading_mode = grading_mode
        if grading_mode == "ANSWER_CONTRACT":
            return await self._complete_contract(ctx, snapshot, conversation, base)

        if not conversation.records:
            base.error_message = "未产生任何有效对话轮次：" + "; ".join(conversation.notes)
            return base

        variables = build_judge_variables(
            snapshot, conversation.records, conversation.termination
        )
        aggregate, outcomes = await run_judges(
            models=ctx.judge_models,
            prompt_template=ctx.judge_prompt,
            variables=variables,
            strategy=ctx.judge_strategy,
            threshold=ctx.pass_threshold,
            termination_status=conversation.termination,
        )
        if conversation.notes:
            aggregate.setdefault("notes", []).extend(conversation.notes)

        rule_passed = True
        if conversation.rule_result and conversation.rule_result.get("checks"):
            rule_passed = bool(conversation.rule_result.get("passed"))
        if not rule_passed:
            aggregate["policy_notes"].append("规则硬校验未通过，最终判定为不通过")

        passed = bool(aggregate.get("pass")) and rule_passed
        if conversation.termination in {
            TerminationStatus.MAX_TURNS_EXCEEDED,
            TerminationStatus.REPETITION_ABORT,
        }:
            passed = False
        if conversation.termination == TerminationStatus.MAX_TURNS_EXCEEDED:
            status = ExecutionStatus.MAX_TURNS_EXCEEDED
        elif conversation.termination == TerminationStatus.REPETITION_ABORT:
            status = ExecutionStatus.REPETITION_ABORT
        else:
            status = ExecutionStatus.PASS if passed else ExecutionStatus.FAIL

        base.status = status
        base.pass_flag = passed
        base.total_score = float(aggregate.get("total_score") or 0)
        base.judge_tokens = int(aggregate.get("judge_tokens") or 0)
        base.judge_result = aggregate
        base.judges = outcomes
        if conversation.termination in {
            TerminationStatus.GOAL_ACHIEVED,
            TerminationStatus.GIVE_UP,
        }:
            base.termination_status = conversation.termination
        return base

    async def _complete_contract(
        self,
        ctx: ExecutionContext,
        snapshot: dict[str, Any],
        conversation: ConversationResult,
        base: RunOutcome,
    ) -> RunOutcome:
        """答案契约判分：正确性只看最终答案，四维裁判降级为质量分。"""
        from billing_qa.services.grading import grade_answer

        # 契约模式与规则硬校验互斥：不落库、不展示规则校验结果。
        base.rule_result = None

        if not conversation.records:
            base.error_message = "未产生任何有效对话轮次：" + "; ".join(conversation.notes)
            base.answer_grading = {
                "passed": False,
                "gates": {"safety": False, "behavior": False, "facts": False, "extras": False},
                "pass_threshold": ctx.pass_threshold,
                "notes": conversation.notes,
            }
            return base

        grading = await grade_answer(
            snapshot=snapshot,
            turn_records=conversation.records,
            judge_model=ctx.judge_models[0] if ctx.judge_models else None,
            pass_threshold=ctx.pass_threshold,
        )

        quality: dict[str, Any] | None = None
        outcomes: list[JudgeOutcome] = []
        try:
            variables = build_judge_variables(
                snapshot, conversation.records, conversation.termination
            )
            quality, outcomes = await run_judges(
                models=ctx.judge_models,
                prompt_template=ctx.judge_prompt,
                variables=variables,
                strategy=ctx.judge_strategy,
                threshold=ctx.pass_threshold,
                termination_status=conversation.termination,
            )
        except Exception as exc:
            logger.warning("契约模式质量分裁判不可用: %s", exc)
            quality = {
                "error": f"{type(exc).__name__}: {exc}",
                "notes": ["质量分裁判不可用，已跳过（不影响正确性）"],
            }

        passed = grading.passed
        if conversation.termination in {
            TerminationStatus.MAX_TURNS_EXCEEDED,
            TerminationStatus.REPETITION_ABORT,
        }:
            passed = False
        if conversation.termination == TerminationStatus.MAX_TURNS_EXCEEDED:
            status = ExecutionStatus.MAX_TURNS_EXCEEDED
        elif conversation.termination == TerminationStatus.REPETITION_ABORT:
            status = ExecutionStatus.REPETITION_ABORT
        else:
            status = ExecutionStatus.PASS if passed else ExecutionStatus.FAIL

        notes = list(grading.notes) + list(conversation.notes)
        base.status = status
        base.pass_flag = passed
        base.total_score = grading.total_score
        base.answer_grading = grading.to_dict()
        # 阈值随执行落库：契约模式下判定由 gates 决定，但界面展示口径需要真实通过线
        base.answer_grading["pass_threshold"] = ctx.pass_threshold
        base.fact_results = grading.fact_dicts()
        base.diagnostics = grading.diagnostics
        base.lucky_pass = grading.lucky_pass
        base.root_cause = grading.root_cause
        base.grader_version = grading.grader_version
        base.as_of = str(snapshot.get("as_of") or "") or None
        base.judge_tokens = int((quality or {}).get("judge_tokens") or 0)
        base.judges = outcomes
        base.judge_result = {
            "mode": "ANSWER_CONTRACT",
            "passed": grading.passed,
            "gates": grading.gates,
            "scorecard": grading.scorecard,
            "root_cause": grading.root_cause,
            "lucky_pass": grading.lucky_pass,
            "grader_version": grading.grader_version,
            "threshold": ctx.pass_threshold,
            "notes": notes,
            # 四维裁判仅作质量分参考，不参与 pass
            "quality": quality,
        }
        if conversation.termination in {
            TerminationStatus.GOAL_ACHIEVED,
            TerminationStatus.GIVE_UP,
        }:
            base.termination_status = conversation.termination
        return base

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    async def _create_execution(
        self,
        *,
        case_id: int,
        case_version: int,
        snapshot: dict[str, Any],
        task_id: int | None,
        source: str,
        environment_id: int | None,
        attempt_no: int,
    ) -> int:
        async with SessionLocal() as db:
            record = ExecutionRecord(
                trace_uid=str(uuid.uuid4()),
                task_id=task_id,
                case_id=case_id,
                case_version=case_version,
                case_snapshot=snapshot,
                environment_id=environment_id or snapshot.get("environment_id"),
                source=source,
                status="RUNNING",
                grading_mode=str(snapshot.get("grading_mode") or "LEGACY"),
                attempt_no=max(1, int(attempt_no)),
                started_at=now(),
            )
            # 冻结本执行生效的最大轮次（与 _run_dynamic 使用同一钳制口径）
            max_turns_raw = snapshot.get("max_turns")
            if max_turns_raw:
                try:
                    record.max_turns = max(
                        1, min(int(max_turns_raw), settings.max_turns_hard_limit)
                    )
                except (TypeError, ValueError):
                    record.max_turns = None
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return int(record.id)

    async def _bind_environment(self, execution_id: int, environment_id: int | None) -> None:
        if environment_id is None:
            return
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            if record is not None:
                record.environment_id = environment_id
                await db.commit()

    async def _save_turn(self, execution_id: int, data: dict[str, Any]) -> None:
        async with SessionLocal() as db:
            db.add(TurnTrace(execution_id=execution_id, **data))
            await db.commit()

    async def _finalize(
        self, execution_id: int, outcome: RunOutcome, ctx: ExecutionContext
    ) -> None:
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            if record is None:
                return
            record.status = outcome.status
            record.pass_flag = outcome.pass_flag
            record.total_score = outcome.total_score
            record.termination_status = outcome.termination_status
            record.turns_used = outcome.turns_used
            record.latency_ms = outcome.latency_ms
            record.ttft_ms = outcome.ttft_ms
            record.simulator_tokens = outcome.simulator_tokens
            record.judge_tokens = outcome.judge_tokens
            record.rule_result = outcome.rule_result
            record.judge_result = outcome.judge_result
            record.simulator_template_version = ctx.simulator_prompt_version
            record.judge_template_version = ctx.judge_prompt_version
            record.error_message = outcome.error_message
            record.grading_mode = outcome.grading_mode
            record.answer_grading = outcome.answer_grading
            record.fact_results = outcome.fact_results
            record.diagnostics = outcome.diagnostics
            record.lucky_pass = outcome.lucky_pass
            record.root_cause = outcome.root_cause
            record.grader_version = outcome.grader_version
            record.as_of = outcome.as_of
            record.self_check = (
                outcome.self_check.to_dict() if outcome.self_check is not None else None
            )
            record.finished_at = now()
            for item in outcome.fact_results or []:
                db.add(
                    FactResult(
                        execution_id=execution_id,
                        fact_id=str(item.get("fact_id") or "")[:64],
                        kind=str(item.get("kind") or "scalar")[:16],
                        label=(str(item.get("label"))[:200] if item.get("label") else None),
                        expected=_json_value(item.get("expected")),
                        actual=_json_value(item.get("actual")),
                        passed=bool(item.get("passed")),
                        reason=(str(item.get("reason"))[:512] if item.get("reason") else None),
                        evidence_ref=(
                            str(item.get("evidence_ref"))[:256]
                            if item.get("evidence_ref")
                            else None
                        ),
                        is_gate=bool(item.get("is_gate", True)),
                    )
                )
            for judge in outcome.judges:
                db.add(
                    JudgeResult(
                        execution_id=execution_id,
                        model_config_id=judge.model_id,
                        model_name=judge.model_name,
                        pass_flag=judge.pass_flag,
                        total_score=judge.total_score,
                        dimension_scores=judge.dimension_scores,
                        error_tags=judge.error_tags,
                        reason=judge.reason,
                        raw_response=judge.raw_response,
                        judge_prompt=judge.judge_prompt,
                        prompt_tokens=judge.prompt_tokens,
                        completion_tokens=judge.completion_tokens,
                    )
                )
            await db.commit()

    async def _mark_error(self, execution_id: int, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            if record is None:
                return
            record.status = ExecutionStatus.ERROR
            record.pass_flag = False
            record.error_kind = classify_exception(exc)
            record.error_message = message[:4000]
            record.finished_at = now()
            await db.commit()

    async def _mark_aborted(self, execution_id: int) -> None:
        async with SessionLocal() as db:
            record = await db.get(ExecutionRecord, execution_id)
            if record is None:
                return
            record.status = ExecutionStatus.ABORTED
            record.pass_flag = False
            record.error_message = "任务被终止"
            record.finished_at = now()
            await db.commit()

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_record(
        turn_index: int,
        query: str,
        input_source: str,
        response: TargetResponse,
        rule: RuleResult | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        # 会话痕迹记录的是「本轮实际生效的会话值」：
        # CLIENT_GENERATED 为请求侧生成值，SERVER_RETURNED 采纳响应回填值。
        return {
            "turn_index": turn_index,
            "query": query,
            "input_source": input_source,
            "request_payload": response.request_payload,
            "response_raw": response.response_payload,
            "final_answer": response.final_answer,
            "session_id": session_id if session_id is not None else response.session_id,
            "intent": response.intent,
            "generated_sql": response.generated_sql,
            "api_params": response.api_params,
            "raw_db_data": response.raw_db_data,
            "tool_calls": response.tool_calls,
            "ttft_ms": response.ttft_ms,
            "latency_ms": response.latency_ms,
            "rule_check": rule.to_dict() if rule else None,
        }

    @staticmethod
    def _format_hidden(items: list[dict[str, Any]], released: set[str]) -> str:
        remaining = [item for item in items if str(item.get("key")) not in released]
        if not remaining:
            return "（所有隐藏信息均已释放，可直接给出结论）"
        lines: list[str] = []
        for item in remaining:
            hint = item.get("release_hint")
            suffix = f"（仅当对方主动问及「{hint}」时再透露）" if hint else ""
            lines.append(f"- {item.get('key')}: {item.get('content')}{suffix}")
        return "\n".join(lines)

    @staticmethod
    def _detect_released(
        query: str, items: list[dict[str, Any]], released: set[str]
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for item in items:
            key = str(item.get("key"))
            if key in released:
                continue
            content = str(item.get("content") or "")
            hint = str(item.get("release_hint") or "")
            if (content and content in query) or (hint and hint in query):
                hits.append({"key": key, "content": content, "release_hint": hint or None})
        return hits
