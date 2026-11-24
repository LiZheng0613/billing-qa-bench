"""TargetApiAdapter 条件解析链路测试：SSE 流 + 规则对象提取。

直接构造 EnvContext（不依赖数据库），用本地 asyncio 原始 TCP 服务模拟
SSE 被测接口，验证 chat() 全链路的条件解析与命中轨迹落库结构。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from billing_qa.services.adapter.target_api import EnvContext, TargetApiAdapter

SSE_BODY_FRAMES = [
    {"type": "text", "context": "上下文1"},
    {"type": "chart", "message": "图表结论A"},
    {"type": "text", "context": "上下文2"},
    {"type": "chart", "payload": {"message": "图表结论B(子层)"}},
    {"type": "text", "context": "上下文3"},
]

EXTRACTORS = {
    "final_answer": {
        "rules": [
            {
                "id": "IF-最后一次chart",
                "path": "$..[?(@.type=='chart')]",
                "occurrence": "last",
                "take": ["$.message", "$..message"],
                "take_parent": ["$.message"],
            },
            {"id": "ELSE-最后一次context", "path": "$..context", "occurrence": "last"},
        ],
        "fallback_content": True,
    },
    "session_id": "$.data.sessionid",
}


async def _start_sse_target() -> tuple[asyncio.AbstractServer, int]:
    """最小 SSE 服务：按帧返回 chart/context 结构，最后发 [DONE]。"""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # 按 Content-Length 精确读完请求体（客户端等待响应，不能等 EOF）
            raw_head = await reader.readuntil(b"\r\n\r\n")
            head, _, rest = raw_head.partition(b"\r\n\r\n")
            content_length = 0
            for line in head.decode("latin-1").split("\r\n")[1:]:
                key, _, value = line.partition(":")
                if key.strip().lower() == "content-length":
                    content_length = int(value.strip())
            body = rest
            while len(body) < content_length:
                body += await reader.read(content_length - len(body))
            body_lines = ["event: metadata", 'data: {"data": {"sessionid": "sess-1"}}', ""]
            for frame in SSE_BODY_FRAMES:
                body_lines.append(f"data: {json.dumps(frame, ensure_ascii=False)}")
                body_lines.append("")
            body_lines.append("data: [DONE]")
            body_lines.append("")
            payload = ("\n\n".join(body_lines) + "\n\n").encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def _env(port: int, extractors: dict, is_stream: bool = True) -> EnvContext:
    return EnvContext(
        id=None,
        base_url=f"http://127.0.0.1:{port}",
        health_path="/health",
        api_method="POST",
        api_path="/chat",
        headers_template=None,
        body_template={"query": "{{query}}"},
        is_stream=is_stream,
        extractors=extractors,
        pre_hook=None,
        timeout_seconds=10.0,
        verify_ssl=False,
    )


@pytest.mark.asyncio
async def test_sse_conditional_extraction_picks_last_chart() -> None:
    """流式场景：条件规则命中最后一次 chart 的子层 message，并写入命中轨迹。"""
    server, port = await _start_sse_target()
    adapter = TargetApiAdapter(_env(port, EXTRACTORS))
    try:
        response = await adapter.chat("查一下本月账单")
        assert response.final_answer == "图表结论B(子层)"
        assert response.session_id == "sess-1"
        trace = response.response_payload["extraction"]["final_answer"]
        assert trace["rule"] == "IF-最后一次chart"
        assert trace["take"] == "$..message"
        assert trace["source"] == "frame"
        # 帧序：0=metadata, 1=text, 2=chart, 3=text, 4=chart(payload, 最后一次)
        assert trace["frame_index"] == 4
        # SSE 落库结构保持既有字段不变
        assert response.response_payload["mode"] == "sse"
        assert response.response_payload["frame_count"] == len(SSE_BODY_FRAMES) + 1
        assert response.ttft_ms is not None
    finally:
        server.close()
        await server.wait_closed()
        await adapter.aclose()
        adapter.close()


@pytest.mark.asyncio
async def test_sse_string_extractors_behavior_unchanged() -> None:
    """字符串 extractor 的流式行为与历史一致：merged 元数据提取 + 聚合文本兜底。"""
    server, port = await _start_sse_target()
    extractors = {"final_answer": "$.data.answer", "session_id": "$.data.sessionid"}
    adapter = TargetApiAdapter(_env(port, extractors))
    try:
        response = await adapter.chat("查一下本月账单")
        # 帧内无 answer 字段 -> 聚合文本兜底（SSE 帧无文本增量，content 为空 -> 空答案）
        assert response.session_id == "sess-1"
        assert response.response_payload["extraction"] is None  # 字符串形态不产生轨迹
    finally:
        server.close()
        await server.wait_closed()
        await adapter.aclose()
        adapter.close()


@pytest.mark.asyncio
async def test_json_mode_spec_matches_nested_chart() -> None:
    """非流式：规则对象在 JSON body 上求值，嵌套 chart 可命中。"""
    payload = json.dumps(
        {"data": {"contents": [{"type": "chart"}, {"type": "chart", "message": "第二个图表"}]}},
        ensure_ascii=False,
    ).encode("utf-8")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw_head = await reader.readuntil(b"\r\n\r\n")
            head, _, rest = raw_head.partition(b"\r\n\r\n")
            content_length = 0
            for line in head.decode("latin-1").split("\r\n")[1:]:
                key, _, value = line.partition(":")
                if key.strip().lower() == "content-length":
                    content_length = int(value.strip())
            body = rest
            while len(body) < content_length:
                body += await reader.read(content_length - len(body))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    extractors = {
        "final_answer": {
            "rules": [
                {
                    "path": "$.data.contents[?(@.type=='chart')]",
                    "occurrence": "last",
                    "take": ["$.message"],
                }
            ]
        }
    }
    adapter = TargetApiAdapter(_env(port, extractors, is_stream=False))
    try:
        response = await adapter.chat("查一下本月账单")
        assert response.final_answer == "第二个图表"
        trace = response.response_payload["extraction"]["final_answer"]
        assert trace["rule_index"] == 0
        assert response.response_payload["mode"] == "json"
        assert response.response_payload["body"]["data"]["contents"]
    finally:
        server.close()
        await server.wait_closed()
        await adapter.aclose()
        adapter.close()
