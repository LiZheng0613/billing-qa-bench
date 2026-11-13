"""内置 Mock：被测问账系统（支持 SSE 流式 + 白盒字段 + Pre-Hook 鉴权）。

启动：uvicorn mock_servers.target_api:app --port 8002
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock Billing Q&A Target", version="1.0.0")

ORDERS: dict[str, dict[str, Any]] = {
    "A2024001": {
        "order_no": "A2024001",
        "customer": "杭州云启科技有限公司",
        "period": "2026-10",
        "total_amount": 12345.67,
        "item_count": 3,
        "items": [
            {"sku": "CLOUD-ENT-01", "name": "企业云服务年费", "amount": 8000.00},
            {"sku": "SUPPORT-PRO", "name": "高级技术支持", "amount": 3000.00},
            {"sku": "TRAINING-01", "name": "培训服务", "amount": 1345.67},
        ],
    },
    "A2024002": {
        "order_no": "A2024002",
        "customer": "上海衡远贸易有限公司",
        "period": "2026-09",
        "total_amount": 5678.90,
        "item_count": 2,
        "items": [
            {"sku": "CLOUD-STD-01", "name": "标准云服务", "amount": 4678.90},
            {"sku": "SUPPORT-STD", "name": "标准支持", "amount": 1000.00},
        ],
    },
}

_ORDER_PATTERN = re.compile(r"[A-Z]\d{7}")
_QUESTION_WORDS = ("查", "金额", "账", "明细", "发票", "退款")
_SESSIONS: dict[str, str] = {}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-billing-target"}


@app.post("/api/auth/token")
async def issue_token() -> dict[str, Any]:
    await asyncio.sleep(0.01)
    return {"code": 0, "message": "success", "data": {"token": "mock-agent-token-2026"}}


def _build_answer(query: str, order: dict[str, Any] | None) -> dict[str, Any]:
    if order is None:
        return {
            "answer": "为了准确核对账单，请提供具体的订单号或账期，我再帮您查询明细。",
            "intent": "CLARIFY",
            "generated_sql": None,
            "api_params": {"query": query},
            "raw_db_data": None,
        }
    amount = f"{order['total_amount']:,.2f}"
    # 故障注入：命中「错误金额」时故意返回错误数字，用于验证规则硬校验
    if "错误金额" in query:
        amount = "999.00"
        answer = f"订单 {order['order_no']} 的含税金额为 ¥{amount}，共 {order['item_count']} 笔明细。"
    else:
        answer = (
            f"订单 {order['order_no']}（{order['period']}）的含税金额为 ¥{amount}，"
            f"共 {order['item_count']} 笔明细，客户为{order['customer']}。"
        )
    return {
        "answer": answer,
        "intent": "BILLING_QUERY",
        "generated_sql": (
            "SELECT order_no, SUM(amount) AS total_amount, COUNT(*) AS item_count "
            f"FROM billing_order_item WHERE order_no = '{order['order_no']}' "
            f"AND period = '{order['period']}' GROUP BY order_no;"
        ),
        "api_params": {"order_no": order["order_no"], "period": order["period"]},
        "raw_db_data": order,
    }


def _chunks(text: str, size: int = 6) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


async def _sse_stream(result: dict[str, Any]):
    metadata = {
        "type": "metadata",
        "session_id": result["session_id"],
        "intent": result["intent"],
        "generated_sql": result["generated_sql"],
        "api_params": result["api_params"],
        "raw_db_data": result["raw_db_data"],
    }
    yield f"event: metadata\ndata: {json.dumps(metadata, ensure_ascii=False)}\n\n"
    await asyncio.sleep(0.08)
    for chunk in _chunks(result["answer"]):
        frame = {"choices": [{"index": 0, "delta": {"content": chunk}}]}
        yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.015)
    yield "data: [DONE]\n\n"


@app.post("/api/v1/chat")
@app.post("/api/chat")
async def chat(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    query = str(body.get("query") or "")
    session_id = str(body.get("session_id") or "") or f"sess-{uuid.uuid4().hex[:12]}"

    if "模拟崩溃" in query or "触发异常" in query:
        raise HTTPException(status_code=500, detail="mock internal error injected")

    order_no = None
    match = _ORDER_PATTERN.search(query)
    if match:
        order_no = match.group(0)
    if order_no is None:
        # 多轮指代继承：从历史对话中回溯订单号
        history = body.get("history") or []
        for item in reversed(history if isinstance(history, list) else []):
            content = str((item or {}).get("content") or "")
            history_match = _ORDER_PATTERN.search(content)
            if history_match:
                order_no = history_match.group(0)
                break
    if order_no is None:
        order_no = _SESSIONS.get(session_id)
    if order_no is not None:
        _SESSIONS[session_id] = order_no
    order = ORDERS.get(order_no) if order_no else None

    if order is None and not any(word in query for word in _QUESTION_WORDS):
        # 无关闲聊：引导回业务
        result = {
            "answer": "我主要负责问账与账单核对，您可以告诉我想查询的订单或账期。",
            "intent": "CHITCHAT",
            "generated_sql": None,
            "api_params": None,
            "raw_db_data": None,
        }
    else:
        result = _build_answer(query, order)
    result["session_id"] = session_id

    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return StreamingResponse(_sse_stream(result), media_type="text/event-stream")
    await asyncio.sleep(0.05)
    return JSONResponse(
        {"code": 0, "message": "success", "data": result},
        headers={"X-Mock-Session": session_id},
    )
