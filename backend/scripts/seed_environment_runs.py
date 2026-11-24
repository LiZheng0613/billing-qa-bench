"""用真实引擎在指定环境上跑一遍 ACTIVE 用例，补齐「按执行环境对比」数据。

用法（在 backend 目录下，需先启动 Mock 8001 / 8002）：
    python scripts/seed_environment_runs.py --env-code QA-MOCK

与 ``seed_demo_history.py`` 的分工：
- ``seed_demo_history.py`` 在 ``QA-MOCK-JSON`` 上生成 9 条演示任务的批量执行；
- 本脚本在 ``QA-MOCK``（SSE）上生成单笔调试执行，让环境对比页有两个真实环境可看。

只排除两类用例：绑定已停用环境的 BUSINESS 错误用例（不能拿 SSE 环境覆盖它的绑定），
以及慢响应用例（单跑约 30s，仅留给「在途终止」演示，不进入批量与环境回归）；
其余 ACTIVE 用例都在该环境上真实执行一次。

执行记录、轮次轨迹、裁判明细全部由 ``CaseExecutor`` 生产代码写入，脚本本身不构造
任何判分字段、不改写时间；重跑会追加新记录，演示库重建后执行一次即可。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import or_, select  # noqa: E402 - 先调整 sys.path 与输出编码

from billing_qa.db.session import SessionLocal  # noqa: E402
from billing_qa.models.enums import ExecutionSource  # noqa: E402
from billing_qa.models.environment import Environment  # noqa: E402
from billing_qa.models.test_case import TestCase, TestCaseRevision  # noqa: E402
from billing_qa.services.engine.executor import CaseExecutor  # noqa: E402

# 慢响应用例只在「在途终止」演示中执行，避免 30s 单次调用拖长环境回归。
SLOW_CASE_CODE = "DEMO-SINGLE-SLOW-ABORT"


async def _load_cases(db) -> list[tuple[int, int, dict, str]]:
    inactive_envs = select(Environment.id).where(Environment.is_active.is_(False))
    cases = (
        (
            await db.execute(
                select(TestCase)
                .where(
                    TestCase.status == "ACTIVE",
                    TestCase.code != SLOW_CASE_CODE,
                    or_(
                        TestCase.environment_id.is_(None),
                        TestCase.environment_id.notin_(inactive_envs),
                    ),
                )
                .order_by(TestCase.id)
            )
        )
        .scalars()
        .all()
    )
    rows: list[tuple[int, int, dict, str]] = []
    for case in cases:
        revision = (
            await db.execute(
                select(TestCaseRevision).where(
                    TestCaseRevision.case_id == case.id,
                    TestCaseRevision.version == case.current_version,
                )
            )
        ).scalar_one_or_none()
        if revision is not None:
            rows.append((int(case.id), int(case.current_version), revision.snapshot, case.code))
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser(description="在指定环境上生成真实执行记录")
    parser.add_argument("--env-code", default="QA-MOCK", help="执行环境编码，默认 QA-MOCK")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条用例，0 表示全部")
    args = parser.parse_args()

    async with SessionLocal() as db:
        env = (
            await db.execute(select(Environment).where(Environment.code == args.env_code))
        ).scalar_one_or_none()
        if env is None:
            raise SystemExit(f"未找到环境 {args.env_code}，请先执行 init_db --demo")
        rows = await _load_cases(db)

    if args.limit > 0:
        rows = rows[: args.limit]
    print(f"env={args.env_code} cases={len(rows)}", flush=True)

    ok = 0
    for index, (case_id, version, snapshot, code) in enumerate(rows, 1):
        try:
            execution_id = await CaseExecutor().execute(
                case_id=case_id,
                case_version=version,
                snapshot=snapshot,
                source=ExecutionSource.DEBUG,
                environment_id=int(env.id),
                attempt_no=1,
            )
            ok += 1
            print(f"[{index}/{len(rows)}] {code} -> #{execution_id}", flush=True)
        except Exception as exc:
            print(
                f"[{index}/{len(rows)}] {code} 调用异常 {type(exc).__name__}: {exc}",
                flush=True,
            )
    print(f"done {ok}/{len(rows)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
