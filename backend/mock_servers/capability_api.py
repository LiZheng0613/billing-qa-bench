"""内置 Mock：电信计费「Cypher 执行器工具」。

演示数据源 / 取数能力链路使用。协议与 `CypherToolExecutor` 对齐：

- ``POST /execute`` 执行参数化查询；
- ``GET  /logs``    按 session_id 增量拉取工具调用日志；
- ``GET  /health``  探活；
- ``GET  /meta``    版本与限额元信息。

启动：uvicorn mock_servers.capability_api:app --host 127.0.0.1 --port 8003
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mock_servers.target_api import AVAILABLE_OFFER_IDS, OFFER_CATALOG, SUBSCRIBERS

app = FastAPI(title="Mock Telecom Cypher Tool", version="0.1.0")

TOOL_VERSION = "mock-cypher-v0.1"
CAPABILITY_MARKER = re.compile(r"capability[:\s]+([a-zA-Z0-9_]+)")
WRITE_KEYWORDS = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", re.IGNORECASE)

_LOGS: list[dict[str, Any]] = []
_SEQ = 0


def _money(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _offers_for_phone(phone: str) -> list[dict[str, Any]]:
    subscriber = SUBSCRIBERS.get(phone) or {}
    rows: list[dict[str, Any]] = []
    for ordered in subscriber.get("ordered_offers") or []:
        offer_id = str(ordered.get("offer_id") or "")
        catalog = OFFER_CATALOG.get(offer_id) or {}
        rows.append(
            {
                "offer_id": offer_id,
                "offer_name": catalog.get("name") or offer_id,
                "category": catalog.get("category"),
                "monthly_fee": _money(catalog.get("monthly_fee")),
                "discount_fee": _money(catalog.get("discount_fee")),
                "effective_date": ordered.get("effective_date"),
                "expire_date": ordered.get("expire_date"),
                "contract_until": ordered.get("contract_until"),
            }
        )
    return rows


def build_capability_result(capability_key: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回 ``columns`` / ``rows``，供 Mock 执行器与演示数据种子共用。"""
    params = params or {}
    phone = str(params.get("phone") or params.get("userid") or "").strip()
    subscriber = SUBSCRIBERS.get(phone)
    period = str(params.get("period") or "").strip()
    columns: list[str] = []
    rows: list[dict[str, Any]] = []

    if subscriber is None:
        return {
            "columns": ["phone"],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "executor_version": TOOL_VERSION,
        }

    period = period or str(subscriber["bill_period"])

    if capability_key == "bill_current":
        columns = [
            "phone",
            "period",
            "bill_total",
            "item_count",
            "package_name",
            "monthly_fee",
            "customer",
            "account_no",
        ]
        rows = [
            {
                "phone": subscriber["phone"],
                "period": period,
                "bill_total": _money(subscriber["bill_total"]),
                "item_count": len(subscriber["bill_items"]),
                "package_name": subscriber["package_name"],
                "monthly_fee": _money(subscriber["monthly_fee"]),
                "customer": subscriber["customer"],
                "account_no": subscriber["account_no"],
            }
        ]
    elif capability_key == "bill_items":
        columns = ["phone", "period", "item_name", "amount"]
        rows = [
            {
                "phone": subscriber["phone"],
                "period": period,
                "item_name": item["name"],
                "amount": _money(item["amount"]),
            }
            for item in subscriber["bill_items"]
        ]
    elif capability_key == "usage_current":
        used = float(subscriber["data_used_gb"])
        total = float(subscriber["data_total_gb"])
        overage = max(0.0, round(used - total, 2))
        columns = [
            "phone",
            "period",
            "package_name",
            "data_total_gb",
            "data_used_gb",
            "data_overage_gb",
            "data_overage_fee",
            "voice_total_min",
            "voice_used_min",
            "voice_remain_min",
            "sms_total",
            "sms_used",
            "sms_remain",
        ]
        rows = [
            {
                "phone": subscriber["phone"],
                "period": period,
                "package_name": subscriber["package_name"],
                "data_total_gb": total,
                "data_used_gb": used,
                "data_overage_gb": overage,
                "data_overage_fee": _money(subscriber["data_overage_fee"]),
                "voice_total_min": subscriber["voice_total_min"],
                "voice_used_min": subscriber["voice_used_min"],
                "voice_remain_min": max(
                    0, int(subscriber["voice_total_min"]) - int(subscriber["voice_used_min"])
                ),
                "sms_total": subscriber["sms_total"],
                "sms_used": subscriber["sms_used"],
                "sms_remain": max(0, int(subscriber["sms_total"]) - int(subscriber["sms_used"])),
            }
        ]
    elif capability_key == "offer_available":
        columns = ["offer_id", "offer_name", "category", "monthly_fee", "discount_fee", "contract_months"]
        rows = []
        for offer_id in AVAILABLE_OFFER_IDS:
            offer = OFFER_CATALOG[offer_id]
            rows.append(
                {
                    "offer_id": offer_id,
                    "offer_name": offer["name"],
                    "category": offer["category"],
                    "monthly_fee": _money(offer["monthly_fee"]),
                    "discount_fee": _money(offer["discount_fee"]),
                    "contract_months": int(offer.get("contract_months") or 0),
                }
            )
    elif capability_key == "offer_ordered":
        columns = [
            "offer_id",
            "offer_name",
            "category",
            "monthly_fee",
            "discount_fee",
            "effective_date",
            "expire_date",
            "contract_until",
        ]
        rows = _offers_for_phone(phone)
    elif capability_key == "balance_account":
        available = _money(subscriber["balance_available"])
        frozen = _money(subscriber["balance_frozen"])
        columns = [
            "phone",
            "available_balance",
            "frozen_balance",
            "total_balance",
            "grant_amount",
            "grant_expire",
        ]
        rows = [
            {
                "phone": subscriber["phone"],
                "available_balance": available,
                "frozen_balance": frozen,
                "total_balance": round(available + frozen, 2),
                "grant_amount": _money(subscriber["balance_grant"]),
                "grant_expire": subscriber["balance_grant_expire"],
            }
        ]
    elif capability_key == "arrears_status":
        unpaid = subscriber.get("unpaid_bills") or []
        columns = [
            "phone",
            "arrears",
            "service_status",
            "unpaid_count",
            "unpaid_periods",
            "unpaid_amount",
        ]
        rows = [
            {
                "phone": subscriber["phone"],
                "arrears": _money(subscriber["arrears"]),
                "service_status": subscriber["service_status"],
                "unpaid_count": len(unpaid),
                "unpaid_periods": ",".join(str(item.get("period")) for item in unpaid),
                "unpaid_amount": round(sum(_money(item.get("amount")) for item in unpaid), 2),
            }
        ]
    elif capability_key == "roaming_usage":
        roaming = subscriber.get("roaming")
        columns = ["phone", "destination", "start_date", "end_date", "data_mb", "fee"]
        rows = (
            [
                {
                    "phone": subscriber["phone"],
                    "destination": roaming["destination"],
                    "start_date": roaming["start"],
                    "end_date": roaming["end"],
                    "data_mb": roaming["data_mb"],
                    "fee": _money(roaming["fee"]),
                }
            ]
            if roaming
            else []
        )
    elif capability_key == "vas_items":
        columns = ["phone", "service_name", "status", "monthly_fee"]
        rows = [
            {
                "phone": subscriber["phone"],
                "service_name": item["name"],
                "status": item["status"],
                "monthly_fee": _money(item["monthly_fee"]),
            }
            for item in subscriber.get("vas") or []
        ]
    elif capability_key == "broadband_bundle":
        broadband = subscriber.get("broadband")
        columns = ["phone", "product_name", "monthly_fee", "status", "sub_card_count", "package_name"]
        rows = (
            [
                {
                    "phone": subscriber["phone"],
                    "product_name": broadband["name"],
                    "monthly_fee": _money(broadband["monthly_fee"]),
                    "status": broadband["status"],
                    "sub_card_count": len(subscriber.get("sub_cards") or []),
                    "package_name": subscriber["package_name"],
                }
            ]
            if broadband
            else []
        )
    elif capability_key == "invoice_info":
        columns = ["phone", "period", "invoice_amount", "invoice_status", "issued_amount"]
        rows = [
            {
                "phone": subscriber["phone"],
                "period": period,
                "invoice_amount": _money(subscriber["invoice_amount"]),
                "invoice_status": subscriber["invoice_status"],
                "issued_amount": 0.0,
            }
        ]
    elif capability_key == "points_info":
        columns = ["phone", "available_points", "exchangable"]
        rows = [
            {
                "phone": subscriber["phone"],
                "available_points": subscriber["points"],
                "exchangable": True,
            }
        ]
    elif capability_key == "package_contract":
        columns = ["phone", "package_name", "monthly_fee", "contract_until", "sub_card_count"]
        rows = [
            {
                "phone": subscriber["phone"],
                "package_name": subscriber["package_name"],
                "monthly_fee": _money(subscriber["monthly_fee"]),
                "contract_until": subscriber["contract_until"],
                "sub_card_count": len(subscriber.get("sub_cards") or []),
            }
        ]
    elif capability_key == "customer_profile":
        columns = ["phone", "customer", "city", "account_no", "package_name"]
        rows = [
            {
                "phone": subscriber["phone"],
                "customer": subscriber["customer"],
                "city": subscriber["city"],
                "account_no": subscriber["account_no"],
                "package_name": subscriber["package_name"],
            }
        ]

    max_rows = int(params.get("max_rows") or 200)
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "executor_version": TOOL_VERSION,
    }


def capability_key_from_query(query: str) -> str | None:
    match = CAPABILITY_MARKER.search(query or "")
    return match.group(1) if match else None


def _log(
    *,
    session_id: str | None,
    query: str,
    params: dict[str, Any],
    status: str,
    row_count: int,
    elapsed_ms: float,
    error: str | None = None,
) -> None:
    global _SEQ
    _SEQ += 1
    _LOGS.append(
        {
            "seq": _SEQ,
            "session_id": session_id,
            "kind": "CYPHER",
            "query": query,
            "params": params,
            "status": status,
            "rows": row_count,
            "elapsed_ms": round(elapsed_ms, 2),
            "error": error,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    del _LOGS[:-500]


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "mock-cypher-tool", "tool_version": TOOL_VERSION}


@app.get("/meta")
async def meta() -> dict[str, Any]:
    return {
        "tool_version": TOOL_VERSION,
        "read_only": True,
        "max_rows": 200,
        "timeout_ms": 10000,
        "capabilities": sorted(
            {
                "bill_current",
                "bill_items",
                "usage_current",
                "offer_available",
                "offer_ordered",
                "balance_account",
                "arrears_status",
                "roaming_usage",
                "vas_items",
                "broadband_bundle",
                "invoice_info",
                "points_info",
                "package_contract",
                "customer_profile",
            }
        ),
    }


@app.get("/logs")
async def logs(
    session_id: str | None = None,
    since_seq: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    items = [item for item in _LOGS if int(item["seq"]) > since_seq]
    if session_id:
        items = [item for item in items if item.get("session_id") == session_id]
    return {"items": items[-max(1, min(limit, 500)) :]}


@app.post("/execute")
async def execute(request: Request) -> JSONResponse:
    started = time.perf_counter()
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_REQUEST", "message": f"invalid json: {exc}"},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_REQUEST", "message": "body must be an object"},
        )

    query = str(payload.get("query") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    session_id = payload.get("session_id")
    if not query.strip():
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_REQUEST", "message": "query 不能为空"},
        )
    if WRITE_KEYWORDS.search(query):
        elapsed = (time.perf_counter() - started) * 1000
        _log(
            session_id=session_id,
            query=query,
            params=params,
            status="ERROR",
            row_count=0,
            elapsed_ms=elapsed,
            error="QUERY_FORBIDDEN",
        )
        return JSONResponse(
            status_code=403,
            content={"code": "QUERY_FORBIDDEN", "message": "查询包含写操作关键字"},
        )

    marker = str(params.get("__error") or "").upper()
    if marker == "TIMEOUT" or "MOCK_TIMEOUT" in query:
        elapsed = (time.perf_counter() - started) * 1000
        _log(
            session_id=session_id,
            query=query,
            params=params,
            status="ERROR",
            row_count=0,
            elapsed_ms=elapsed,
            error="TIMEOUT",
        )
        return JSONResponse(
            status_code=504,
            content={"code": "TIMEOUT", "message": "查询超时（演示注入）"},
        )
    if marker == "INTERNAL" or "MOCK_INTERNAL_ERROR" in query:
        elapsed = (time.perf_counter() - started) * 1000
        _log(
            session_id=session_id,
            query=query,
            params=params,
            status="ERROR",
            row_count=0,
            elapsed_ms=elapsed,
            error="INTERNAL_ERROR",
        )
        return JSONResponse(
            status_code=502,
            content={"code": "INTERNAL_ERROR", "message": "执行器内部错误（演示注入）"},
        )

    phone = str(params.get("phone") or params.get("userid") or "").strip()
    if not phone:
        elapsed = (time.perf_counter() - started) * 1000
        _log(
            session_id=session_id,
            query=query,
            params=params,
            status="ERROR",
            row_count=0,
            elapsed_ms=elapsed,
            error="PARAM_MISSING",
        )
        return JSONResponse(
            status_code=422,
            content={"code": "PARAM_MISSING", "message": "缺少必填参数 phone"},
        )

    capability_key = capability_key_from_query(query)
    if not capability_key:
        elapsed = (time.perf_counter() - started) * 1000
        _log(
            session_id=session_id,
            query=query,
            params=params,
            status="ERROR",
            row_count=0,
            elapsed_ms=elapsed,
            error="INVALID_REQUEST",
        )
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_REQUEST",
                "message": "无法识别查询模板（缺少 capability:<key> 标记）",
            },
        )

    result = build_capability_result(capability_key, {**params, "phone": phone})
    elapsed = (time.perf_counter() - started) * 1000
    _log(
        session_id=session_id,
        query=query,
        params=params,
        status="SUCCESS",
        row_count=int(result["row_count"]),
        elapsed_ms=elapsed,
    )
    await _sleep()
    return JSONResponse(
        status_code=200,
        content={
            "code": "OK",
            "message": "success",
            "data": {
                **result,
                "elapsed_ms": round(elapsed, 2),
                "query_id": f"q-{uuid.uuid4().hex[:10]}",
            },
        },
    )


async def _sleep() -> None:
    await asyncio.sleep(0.02)
