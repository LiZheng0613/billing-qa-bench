"""内置 Mock：OpenAI 兼容 LLM（同时扮演用户模拟器与裁判）。

启动：uvicorn mock_servers.llm_api:app --port 8001
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request

app = FastAPI(title="Mock OpenAI Compatible LLM", version="1.0.0")

_ORDER_PATTERN = re.compile(r"[A-Z]\d{7}")
_TURN_PATTERN = re.compile(r"当前轮次：\s*(\d+)")
_TERMINATION_PATTERN = re.compile(r"结束状态\s*([A-Z_]+)")

DISTINCT_QUERIES = [
    "那能不能把每笔明细也发我看一下？",
    "这些费用里包含税费吗？",
    "上个月的账期是不是也一起结清了？",
    "发票大概什么时候可以开出来？",
    "有没有产生额外的违约金或者手续费？",
]


def _simulator_content(prompt: str) -> dict[str, Any]:
    turn_match = _TURN_PATTERN.search(prompt)
    turn = int(turn_match.group(1)) if turn_match else 1
    order_match = _ORDER_PATTERN.search(prompt)

    if "[MOCK:GIVEUP]" in prompt:
        return {"thought": "客服一直给不出有效信息，放弃追问", "status": "GIVE_UP", "query": "那算了吧"}
    if "[MOCK:REPEAT]" in prompt:
        return {
            "thought": "继续用同一句话追问，用于验证复读机熔断",
            "status": "CONTINUE",
            "query": "帮我查一下订单 A2024001 的含税金额",
        }
    if "[MOCK:MAXTURNS]" in prompt:
        query = DISTINCT_QUERIES[(turn - 1) % len(DISTINCT_QUERIES)]
        return {"thought": "目标尚未达成，继续追问不同细节", "status": "CONTINUE", "query": query}

    if turn >= 3:
        return {"thought": "已经拿到金额和笔数，目标达成", "status": "GOAL_ACHIEVED", "query": "好的，清楚了，谢谢"}
    if turn == 2 and order_match:
        return {
            "thought": "客服要求提供订单号，这里释放隐藏信息",
            "status": "CONTINUE",
            "query": f"订单号是 {order_match.group(0)}，麻烦帮我核对一下。",
        }
    if turn == 2:
        return {"thought": "先补充账期信息", "status": "CONTINUE", "query": "账期是 2026-10，麻烦查一下。"}
    return {
        "thought": "先描述诉求，暂不主动给出订单号",
        "status": "CONTINUE",
        "query": "我想核对一下这个月的账单金额和明细，但订单号一时找不到。",
    }


def _judge_content(prompt: str, model: str) -> dict[str, Any]:
    termination_match = _TERMINATION_PATTERN.search(prompt)
    termination = termination_match.group(1) if termination_match else "COMPLETED"

    if "[MOCK:FAIL]" in prompt or "[MOCK:WRONG]" in prompt:
        return {
            "pass": False,
            "total_score": 18,
            "dimension_scores": {
                "data_accuracy": 0,
                "logic_consistency": 8,
                "goal_completion": 10,
                "tone_clarity": 8,
            },
            "error_tags": ["数值错误/数据幻觉"],
            "reason": "第 1 轮回复中的金额与底层 Raw DB Data 不一致，触发数据零容错红线。",
        }
    if termination == "MAX_TURNS_EXCEEDED":
        return {
            "pass": False,
            "total_score": 52,
            "dimension_scores": {
                "data_accuracy": 30,
                "logic_consistency": 22,
                "goal_completion": 0,
                "tone_clarity": 9,
            },
            "error_tags": ["超出最大轮次", "未主动反问"],
            "reason": "对话达到最大轮次仍未解决用户诉求，目标达成项判 0 分。",
        }
    if model.endswith("judge-2"):
        return {
            "pass": True,
            "total_score": 88,
            "dimension_scores": {
                "data_accuracy": 38,
                "logic_consistency": 26,
                "goal_completion": 16,
                "tone_clarity": 8,
            },
            "error_tags": [],
            "reason": "金额与底层取数一致，口径正确，但明细展示可更清晰。",
        }
    return {
        "pass": True,
        "total_score": 94,
        "dimension_scores": {
            "data_accuracy": 40,
            "logic_consistency": 28,
            "goal_completion": 18,
            "tone_clarity": 8,
        },
        "error_tags": [],
        "reason": "金额、笔数与底层 Raw DB Data 完全一致，主动澄清缺失条件，话术清晰。",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-llm"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict[str, Any]:
    payload = await request.json()
    messages = payload.get("messages") or []
    model = str(payload.get("model") or "mock-model")
    prompt = "\n".join(str(item.get("content") or "") for item in messages)

    if "用户模拟器" in prompt or "你正在扮演一名真实的业务用户" in prompt:
        content: Any = _simulator_content(prompt)
    elif "审计裁判官" in prompt or "Scoring Rubrics" in prompt:
        content = _judge_content(prompt, model)
    elif '"ok": true' in prompt:
        content = {"ok": True}
    else:
        content = {"echo": prompt[:200]}

    await asyncio.sleep(0.03)
    text = json.dumps(content, ensure_ascii=False)
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(text) // 4)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
