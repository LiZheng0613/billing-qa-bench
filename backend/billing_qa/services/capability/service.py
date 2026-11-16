"""取数能力执行与取证：内部链路复用，外部 endpoint 由数据源配置决定。"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from billing_qa.core.exceptions import NotFoundError, ValidationError
from billing_qa.models.capability import Capability, DataSource
from billing_qa.models.gold_query import GoldQuery
from billing_qa.services.capability.executors.base import (
    CapabilityExecutorError,
    ExecutionResult,
)
from billing_qa.services.capability.factory import build_executor


async def probe_data_source(source: DataSource) -> dict[str, Any]:
    """探活并返回可落库的探测结果。"""
    started = perf_counter()
    try:
        executor = build_executor(source)
        health = await executor.health()
        meta = await executor.meta()
    except CapabilityExecutorError as exc:
        return {
            "ok": False,
            "status": "FAILED",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "message": exc.message,
            "meta": exc.to_dict(),
        }
    except Exception as exc:  # pragma: no cover - 兜底防御
        return {
            "ok": False,
            "status": "FAILED",
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "message": f"{type(exc).__name__}: {exc}",
            "meta": None,
        }
    return {
        "ok": True,
        "status": "OK",
        "latency_ms": round((perf_counter() - started) * 1000, 2),
        "message": "探活成功",
        "meta": {"health": health, "meta": meta},
    }


def bind_params(
    capability: Capability,
    params: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """context 槽位 → 查询参数 的机械映射；显式 params 优先。"""
    bound = dict(params or {})
    for slot, param in (capability.param_binding or {}).items():
        if context and slot in context and param not in bound:
            bound[str(param)] = context[slot]
    return bound


async def execute_capability(
    db: AsyncSession,
    capability: Capability,
    *,
    case_id: int,
    params: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    fact_ids: list[str] | None = None,
    session_id: str | None = None,
    max_rows: int | None = None,
    timeout_seconds: float | None = None,
) -> tuple[ExecutionResult, GoldQuery]:
    """执行能力模板并生成一条可审计的 GoldQuery。"""
    if not capability.is_active:
        raise ValidationError("能力已停用，不能执行")
    if not capability.query_text:
        raise ValidationError("能力未配置查询模板")
    source = await db.get(DataSource, capability.source_id) if capability.source_id else None
    if source is None:
        raise ValidationError("能力未绑定有效数据源")
    if not source.is_active:
        raise ValidationError("数据源已停用")

    executor = build_executor(source)
    bound = bind_params(capability, params, context)
    result = await executor.execute(
        capability.query_text,
        bound,
        session_id=session_id,
        max_rows=max_rows or source.max_rows,
        timeout_seconds=timeout_seconds or source.timeout_seconds,
    )
    gold = GoldQuery(
        case_id=case_id,
        capability_id=capability.id,
        capability_version=capability.version,
        origin="CAPABILITY",
        fact_ids=list(fact_ids or []),
        template_params=bound,
        query_text=capability.query_text,
        columns=list(result.columns),
        rows=list(result.rows),
        row_count=int(result.row_count or len(result.rows)),
        truncated=bool(result.truncated),
        executor_version=result.executor_version or source.tool_version,
    )
    db.add(gold)
    await db.flush()
    return result, gold


async def get_gold_query(db: AsyncSession, gold_id: int) -> GoldQuery:
    gold = await db.get(GoldQuery, gold_id)
    if gold is None:
        raise NotFoundError("取证记录不存在")
    return gold
