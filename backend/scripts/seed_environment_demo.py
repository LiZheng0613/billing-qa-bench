"""写入「按执行环境对比」演示数据（电信计费问账 Agent 调优场景）。

已停用（仅作历史参考）：当前 ``GradingMode`` 只有 ``LEGACY`` / ``ANSWER_CONTRACT``，
本脚本会给非契约用例写入 ``RUBRIC``，属于当前代码无法产生的数据，重建演示库时不要执行。
需要环境对比数据请改用 ``scripts/seed_environment_runs.py``（真实引擎执行）。

用法（在 backend 目录下）：
    python scripts/seed_environment_demo.py           # 幂等重建 3 个演示环境及其执行记录
    python scripts/seed_environment_demo.py --purge   # 只清理演示环境与演示执行记录

演示环境对应三种优化思路，数据覆盖最近 30 天（含前 7 天对照窗口）：
- DEMO-ENV-BASE      基线 Prompt v1：正确率约 58%，波动大、轮次与 Token 偏高；
- DEMO-ENV-PROMPT-V2 精简 Prompt + 结构化输出：正确率约 74%，更稳、更快；
- DEMO-ENV-TOOL-V3   工具调用增强 + 失败自检：正确率约 86%，抖动最少。

数据全部落库为真实的 execution_record，可直接被 /executions/summary 与
/executions/environment-summary 聚合；用例取自库内 ACTIVE 的 DEMO-* 计费场景。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import delete, func, select  # noqa: E402 - 先调整 sys.path 与输出编码

from billing_qa.db.session import SessionLocal  # noqa: E402
from billing_qa.models.enums import ExecutionSource  # noqa: E402
from billing_qa.models.environment import Environment  # noqa: E402
from billing_qa.models.execution import ExecutionRecord  # noqa: E402
from billing_qa.models.test_case import TestCase, TestCaseRevision  # noqa: E402

ENV_PREFIX = "DEMO-ENV-"
MAX_TURNS = 12


@dataclass(frozen=True)
class EnvProfile:
    code: str
    name: str
    description: str
    health_latency_ms: float
    # 近 7 天每日目标通过率（今天 → 6 天前），用固定波形制造可解释的波动
    week_wave: tuple[float, ...]
    prev_week_rate: float
    old_rate: float
    # 每次执行出现 ERROR 的概率
    error_prob: float
    # 特殊注错用例（DEMO-SINGLE-ERROR）注入异常的概率
    injected_error_prob: float
    # 日间比分扰动，越大越容易出现跨天翻转（抖动）
    day_noise: float
    # 时延均值 / TTFT 占比、轮次修正
    latency_mean: float
    ttft_ratio: float
    turn_shift: int
    sim_token_base: int
    judge_token_base: int
    # 每日执行量区间（近 7 天 / 前 7 天 / 更早）
    volume_week: tuple[int, int]
    volume_prev: tuple[int, int]
    volume_old: tuple[int, int]


PROFILES: tuple[EnvProfile, ...] = (
    EnvProfile(
        code=f"{ENV_PREFIX}BASE",
        name="电信计费问账 · 基线 Prompt v1",
        description="原始系统提示词与单轮工具编排，作为调优对照基线；上下文未压缩。",
        health_latency_ms=96.0,
        week_wave=(0.74, 0.56, 0.70, 0.54, 0.68, 0.57, 0.64),
        prev_week_rate=0.60,
        old_rate=0.57,
        error_prob=0.012,
        injected_error_prob=0.55,
        day_noise=0.012,
        latency_mean=960.0,
        ttft_ratio=0.42,
        turn_shift=0,
        sim_token_base=300,
        judge_token_base=360,
        volume_week=(20, 24),
        volume_prev=(12, 16),
        volume_old=(8, 12),
    ),
    EnvProfile(
        code=f"{ENV_PREFIX}PROMPT-V2",
        name="电信计费问账 · 精简 Prompt + 结构化输出 v2",
        description="压缩系统提示词、约束 JSON 答案契约，减少轮次与重复追问。",
        health_latency_ms=64.0,
        week_wave=(0.805, 0.795, 0.803, 0.796, 0.802, 0.798, 0.801),
        prev_week_rate=0.73,
        old_rate=0.70,
        error_prob=0.002,
        injected_error_prob=0.20,
        day_noise=0.006,
        latency_mean=700.0,
        ttft_ratio=0.38,
        turn_shift=-1,
        sim_token_base=210,
        judge_token_base=280,
        volume_week=(18, 22),
        volume_prev=(11, 15),
        volume_old=(8, 11),
    ),
    EnvProfile(
        code=f"{ENV_PREFIX}TOOL-V3",
        name="电信计费问账 · 工具调用增强 + 失败自检 v3",
        description="补齐账单/套餐/余量工具编排，LLM 判负后同会话自检追问，稳定性最佳。",
        health_latency_ms=58.0,
        week_wave=(0.905, 0.903, 0.906, 0.902, 0.905, 0.903, 0.905),
        prev_week_rate=0.87,
        old_rate=0.85,
        error_prob=0.0005,
        injected_error_prob=0.05,
        day_noise=0.002,
        latency_mean=580.0,
        ttft_ratio=0.35,
        turn_shift=-1,
        sim_token_base=240,
        judge_token_base=320,
        volume_week=(20, 24),
        volume_prev=(12, 16),
        volume_old=(8, 12),
    ),
)

EASY_KEYWORDS = (
    "BILL",
    "PACKAGE",
    "SUBCARD",
    "USAGE",
    "VOICE",
    "OFFER",
    "BALANCE",
    "ARREARS",
    "ROAMING",
    "BROADBAND",
    "VAS",
    "INVOICE",
    "POINTS",
)
HARD_KEYWORDS = (
    "DYNAMIC",
    "MAXTURNS",
    "REPEAT",
    "MULTI-INTENT",
    "AMBIGUOUS",
    "CONTRADICTORY",
    "UNAUTHORIZED",
    "UNDERSPECIFIED",
    "NO-NUMBER",
    "VAGUE",
    "UNANSWERABLE",
)
FORCED_FAIL_KEYWORDS = ("VETO", "LUCKY", "WRONG-AMOUNT")
SPECIAL_ERROR_KEYWORDS = ("DEMO-SINGLE-ERROR",)
SPECIAL_FAIL_KEYWORDS = ("DEMO-SINGLE-FAIL",)


def difficulty(code: str) -> str:
    upper = code.upper()
    if any(word in upper for word in HARD_KEYWORDS):
        return "hard"
    if "CONTRACT" in upper or "STATIC" in upper:
        return "medium"
    if any(word in upper for word in EASY_KEYWORDS):
        return "easy"
    return "medium"


def base_ability(code: str, rng: random.Random) -> float:
    """用例固有难度 + 随机个体差异，用于在给定通过率下挑选当日通过项。"""
    bonus = {"easy": 0.34, "medium": 0.0, "hard": -0.28}[difficulty(code)]
    return rng.random() + bonus


def failure_status(code: str, case_type: str) -> str:
    upper = code.upper()
    if "REPEAT" in upper:
        return "REPETITION_ABORT"
    if "MAXTURNS" in upper:
        return "MAX_TURNS_EXCEEDED"
    if case_type == "DYNAMIC_MULTI":
        return "REPETITION_ABORT" if "REPEAT" in upper else "MAX_TURNS_EXCEEDED"
    if difficulty(code) == "hard":
        return "REPETITION_ABORT" if "CONTRADICTORY" in upper else "FAIL"
    return "FAIL"


def day_target(profile: EnvProfile, offset: int) -> float:
    if offset < 7:
        return profile.week_wave[offset]
    if offset < 14:
        # 前 7 天：整体略低，配合每日小幅波动，便于展示环比
        return (
            profile.prev_week_rate
            + (-0.015, 0.012, -0.008, 0.014, -0.011, 0.006, -0.004)[offset - 7]
        )
    return profile.old_rate + (-0.012, 0.009, -0.006, 0.011, -0.009, 0.005, -0.003)[offset % 7]


async def _purge_demo(db: Any) -> int:
    env_ids = (
        (await db.execute(select(Environment.id).where(Environment.code.like(f"{ENV_PREFIX}%"))))
        .scalars()
        .all()
    )
    if not env_ids:
        return 0
    removed = (
        await db.execute(delete(ExecutionRecord).where(ExecutionRecord.environment_id.in_(env_ids)))
    ).rowcount or 0
    await db.execute(delete(Environment).where(Environment.id.in_(env_ids)))
    return int(removed)


async def _load_cases(db: Any) -> list[tuple[int, int, dict[str, Any], str, str]]:
    cases = (
        (
            await db.execute(
                select(TestCase)
                .where(TestCase.status == "ACTIVE", TestCase.code.like("DEMO-%"))
                .order_by(TestCase.id)
            )
        )
        .scalars()
        .all()
    )
    rows: list[tuple[int, int, dict[str, Any], str, str]] = []
    for case in cases:
        revision = (
            await db.execute(
                select(TestCaseRevision).where(
                    TestCaseRevision.case_id == case.id,
                    TestCaseRevision.version == case.current_version,
                )
            )
        ).scalar_one_or_none()
        if revision is None or not isinstance(revision.snapshot, dict):
            continue
        rows.append(
            (
                int(case.id),
                int(revision.version),
                copy.deepcopy(revision.snapshot),
                str(case.code),
                str(case.case_type),
            )
        )
    return rows


async def _ensure_environment(db: Any, profile: EnvProfile, template: Environment) -> Environment:
    env = (
        await db.execute(select(Environment).where(Environment.code == profile.code))
    ).scalar_one_or_none()
    if env is None:
        env = Environment(
            code=profile.code,
            base_url=template.base_url,
            health_path=template.health_path,
            api_method=template.api_method,
            api_path=template.api_path,
        )
        db.add(env)
    env.name = profile.name
    env.description = profile.description
    env.headers_template = copy.deepcopy(template.headers_template)
    env.body_template = copy.deepcopy(template.body_template)
    env.extractors = copy.deepcopy(template.extractors)
    env.is_stream = False
    env.session_mode = template.session_mode
    env.timeout_seconds = template.timeout_seconds
    env.verify_ssl = template.verify_ssl
    env.is_active = True
    env.last_health_status = "UP"
    env.last_health_latency_ms = profile.health_latency_ms
    env.last_health_at = datetime.now() - timedelta(minutes=2)
    env.last_health_message = "演示环境：健康探活正常"
    await db.flush()
    return env


def _build_records(
    profile: EnvProfile,
    environment_id: int,
    cases: list[tuple[int, int, dict[str, Any], str, str]],
    now: datetime,
) -> list[ExecutionRecord]:
    rng = random.Random(f"environment-demo::{profile.code}")
    abilities = {code: base_ability(code, rng) for _, _, _, code, _ in cases}
    records: list[ExecutionRecord] = []
    today = datetime.combine(now.date(), time.min)

    for offset in range(30):
        day = today - timedelta(days=offset)
        if offset < 7:
            low, high = profile.volume_week
        elif offset < 14:
            low, high = profile.volume_prev
        else:
            low, high = profile.volume_old
        slots = _build_day_slots(day, offset, rng, now, low, high)
        if not slots:
            continue
        picked = rng.sample(cases, k=min(len(slots), len(cases)))
        target = max(0.05, min(0.97, day_target(profile, offset)))
        pass_count = round(len(picked) * target)

        # 先决定异常项：特殊注错用例优先，其次按环境异常率随机注入
        error_codes: set[str] = set()
        for _, _, _, code, _ in picked:
            if any(keyword in code for keyword in SPECIAL_ERROR_KEYWORDS) and (
                rng.random() < profile.injected_error_prob
            ):
                error_codes.add(code)
        expected_errors = profile.error_prob * len(picked)
        extra_errors = int(expected_errors) + (1 if rng.random() < expected_errors % 1 else 0)
        spare = [item for item in picked if item[3] not in error_codes]
        rng.shuffle(spare)
        for item in spare[:extra_errors]:
            error_codes.add(item[3])
        if len(error_codes) > len(picked) - 1:
            error_codes = set(list(error_codes)[: len(picked) - 1])

        # 当日比分：固有难度 + 少量日间扰动，边界用例会跨天翻转形成抖动。
        # 注错/负向用例固定不计入通过名额，避免把目标通过率整体压低。
        forced_fail = {
            item[3]
            for item in picked
            if any(keyword in item[3] for keyword in FORCED_FAIL_KEYWORDS)
            or any(keyword in item[3] for keyword in SPECIAL_FAIL_KEYWORDS)
        }
        noise = profile.day_noise
        scored = sorted(
            (
                (abilities[code] + rng.uniform(-noise, noise), item)
                for item in picked
                if item[3] not in error_codes and item[3] not in forced_fail
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        pass_slots = min(len(scored), max(0, pass_count - len(error_codes)))
        passing = {item[3] for _, item in scored[:pass_slots]}

        for (case_id, version, snapshot, code, case_type), started in zip(
            picked, slots, strict=False
        ):
            status = (
                "PASS"
                if code in passing
                else "ERROR"
                if code in error_codes
                else failure_status(code, case_type)
            )
            if code in forced_fail:
                status = "FAIL"
            records.append(
                _make_record(
                    profile=profile,
                    environment_id=environment_id,
                    case_id=case_id,
                    version=version,
                    snapshot=snapshot,
                    code=code,
                    case_type=case_type,
                    status=status,
                    started=started,
                    rng=rng,
                )
            )
    return records


def _build_day_slots(
    day: datetime,
    offset: int,
    rng: random.Random,
    now: datetime,
    low: int,
    high: int,
) -> list[datetime]:
    start_hour, end_hour = 9, 21
    if offset == 0:
        end_hour = max(start_hour, min(end_hour, now.hour))
        if end_hour <= start_hour:
            return []
    span = (end_hour - start_hour) * 60
    wanted = rng.randint(low, high)
    minutes = rng.sample(range(span), k=min(wanted, span))
    slots = [
        datetime.combine(day.date(), time(hour=0)) + timedelta(minutes=start_hour * 60 + minute)
        for minute in sorted(minutes)
    ]
    return [slot for slot in slots if slot <= now - timedelta(minutes=1)]


def _make_record(
    *,
    profile: EnvProfile,
    environment_id: int,
    case_id: int,
    version: int,
    snapshot: dict[str, Any],
    code: str,
    case_type: str,
    status: str,
    started: datetime,
    rng: random.Random,
) -> ExecutionRecord:
    passed = status == "PASS"
    if status == "ERROR":
        turns = rng.randint(1, 3)
        latency = profile.latency_mean * rng.uniform(2.0, 3.2)
        score: float | None = None
        error_kind: str | None = "ENV"
        error_message = "演示数据：被测 Agent 调用超时（连接被重置）"
        sim_tokens = rng.randint(profile.sim_token_base // 2, profile.sim_token_base)
        judge_tokens = 0
    elif status == "MAX_TURNS_EXCEEDED":
        turns = MAX_TURNS
        latency = profile.latency_mean * rng.uniform(1.3, 1.7)
        score = round(rng.uniform(38, 62), 1)
        error_kind = None
        error_message = "演示数据：达到最大轮次仍未完成目标"
        sim_tokens = profile.sim_token_base * turns + rng.randint(0, 600)
        judge_tokens = profile.judge_token_base + rng.randint(0, 300)
    elif status == "REPETITION_ABORT":
        turns = rng.randint(4, 8)
        latency = profile.latency_mean * rng.uniform(1.1, 1.5)
        score = round(rng.uniform(40, 66), 1)
        error_kind = None
        error_message = "演示数据：复读触发熔断"
        sim_tokens = profile.sim_token_base * turns + rng.randint(0, 500)
        judge_tokens = profile.judge_token_base + rng.randint(0, 260)
    else:
        turns = _turns_for(case_type, status, profile.turn_shift, rng)
        latency = profile.latency_mean * _latency_factor(case_type, rng)
        score = round(rng.uniform(86, 99) if passed else rng.uniform(45, 74), 1)
        error_kind = None
        error_message = None
        sim_tokens = profile.sim_token_base * turns + rng.randint(0, 240)
        judge_tokens = (
            profile.judge_token_base + rng.randint(0, 260)
            if passed
            else profile.judge_token_base + rng.randint(0, 180)
        )

    latency = max(80.0, latency)
    source = rng.choices(
        [ExecutionSource.BATCH, ExecutionSource.RETRY, ExecutionSource.DEBUG],
        weights=[0.72, 0.14, 0.14],
        k=1,
    )[0]
    attempt_no = 2 if source == ExecutionSource.RETRY else 1
    if status == "MAX_TURNS_EXCEEDED":
        termination_status = "MAX_TURNS"
    elif passed:
        termination_status = "GOAL_ACHIEVED"
    elif status == "ERROR":
        termination_status = "ERROR"
    else:
        termination_status = "GIVE_UP"
    return ExecutionRecord(
        trace_uid=str(uuid.uuid4()),
        task_id=None,
        case_id=case_id,
        case_version=version,
        case_snapshot=snapshot,
        environment_id=environment_id,
        source=source,
        status=status,
        attempt_no=attempt_no,
        error_kind=error_kind,
        pass_flag=passed,
        total_score=score,
        termination_status=termination_status,
        turns_used=turns,
        max_turns=MAX_TURNS,
        latency_ms=round(latency, 1),
        ttft_ms=round(latency * profile.ttft_ratio * rng.uniform(0.85, 1.15), 1),
        simulator_tokens=sim_tokens,
        judge_tokens=judge_tokens,
        error_message=error_message,
        started_at=started,
        finished_at=started + timedelta(milliseconds=round(latency)),
        grading_mode="ANSWER_CONTRACT" if snapshot.get("answer_contract") else "RUBRIC",
        lucky_pass=False,
        root_cause=(None if passed else "AGENT" if status != "ERROR" else "ENVIRONMENT"),
        as_of=started.strftime("%Y-%m-%d"),
    )


def _turns_for(case_type: str, status: str, shift: int, rng: random.Random) -> int:
    if status != "PASS":
        base = {"SINGLE": 1, "STATIC_MULTI": 3, "DYNAMIC_MULTI": 5}.get(case_type, 2)
    else:
        base = {"SINGLE": 1, "STATIC_MULTI": 2, "DYNAMIC_MULTI": 4}.get(case_type, 1)
    return max(1, base + shift + rng.choice((0, 0, 0, 1)))


def _latency_factor(case_type: str, rng: random.Random) -> float:
    multiplier = {"SINGLE": 1.0, "STATIC_MULTI": 1.25, "DYNAMIC_MULTI": 1.6}.get(case_type, 1.0)
    return multiplier * rng.uniform(0.82, 1.22)


async def main() -> None:
    parser = argparse.ArgumentParser(description="生成按执行环境对比的演示数据")
    parser.add_argument("--purge", action="store_true", help="只清理演示环境与执行记录")
    args = parser.parse_args()

    async with SessionLocal() as db:
        template = (
            await db.execute(select(Environment).where(Environment.code == "QA-MOCK-JSON"))
        ).scalar_one_or_none()
        if template is None:
            template = (
                await db.execute(select(Environment).where(Environment.code == "QA-MOCK"))
            ).scalar_one_or_none()
        if template is None:
            raise SystemExit("未找到可参考的 Mock 环境，请先初始化数据库")

        removed = await _purge_demo(db)
        if args.purge:
            await db.commit()
            print(f"已清理演示环境及 {removed} 条演示执行记录", flush=True)
            return

        cases = await _load_cases(db)
        if not cases:
            raise SystemExit("未找到 ACTIVE 的 DEMO-* 用例，请先执行 init_db --demo")

        now = datetime.now()
        summary: list[tuple[str, int, int, int, int]] = []
        for profile in PROFILES:
            env = await _ensure_environment(db, profile, template)
            records = _build_records(profile, int(env.id), cases, now)
            db.add_all(records)
            passed = sum(1 for record in records if record.pass_flag)
            errors = sum(1 for record in records if record.status == "ERROR")
            summary.append((profile.code, int(env.id), len(records), passed, errors))
        await db.commit()

        # 复用聚合口径回读一次，确认写入的数据能被大盘正确解释
        for code, env_id, total, passed, errors in summary:
            measured = (
                await db.execute(
                    select(func.count(ExecutionRecord.pass_flag)).where(
                        ExecutionRecord.environment_id == env_id
                    )
                )
            ).scalar() or 0
            print(
                f"{code} env_id={env_id} 写入 {total} 条 / 已判定 {measured} 条 "
                f"/ 通过 {passed} 条 / 异常 {errors} 条 / 通过率 {passed / measured * 100:.1f}%",
                flush=True,
            )
        print("环境对比演示数据写入完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
