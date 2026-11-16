"""Cypher 执行器工具 mock 契约测试。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport

from billing_qa.services.capability.executors.base import CapabilityExecutorError
from billing_qa.services.capability.executors.cypher_tool import (
    ERROR_CODES,
    CypherToolExecutor,
)
from tests.mocks import cypher_tool_mock as mock


@pytest.fixture(autouse=True)
def _reset_mock_state() -> None:
    mock.reset_state()


def _executor() -> CypherToolExecutor:
    return CypherToolExecutor(
        "http://tool.local",
        timeout_seconds=2,
        max_rows=50,
        transport=ASGITransport(app=mock.app),
    )


async def test_execute_returns_rows_and_logs() -> None:
    executor = _executor()
    result = await executor.execute(
        "MATCH (b:Bill) RETURN b.amount AS amount",
        {"__rows": [{"amount": 123.45}], "__columns": [{"name": "amount", "type": "float"}]},
        session_id="s1",
    )
    assert result.columns == ["amount"]
    assert result.rows == [{"amount": 123.45}]
    assert result.row_count == 1
    assert result.executor_version == "mock-0.1"

    entries = await executor.logs(session_id="s1", since_seq=0)
    assert len(entries) == 1
    assert entries[0].kind == "CYPHER"
    assert entries[0].rows == 1

    entries = await executor.logs(session_id="s1", since_seq=1)
    assert entries == []


async def test_health_and_meta() -> None:
    executor = _executor()
    assert (await executor.health())["status"] == "UP"
    meta = await executor.meta()
    assert meta["version"] == "mock-0.1"
    assert meta["limits"]["max_rows"] == 200


async def test_row_limit_truncation() -> None:
    executor = _executor()
    result = await executor.execute(
        "MATCH (n) RETURN n",
        {"__rows": [{"n": index} for index in range(100)], "__truncate": True},
    )
    assert result.truncated
    assert result.row_count == 50


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
async def test_all_contract_error_codes_are_mapped(code: str) -> None:
    executor = _executor()
    with pytest.raises(CapabilityExecutorError) as captured:
        await executor.execute("MATCH (n) RETURN n", {"__error": code})
    assert captured.value.code == code
    assert captured.value.retryable == ERROR_CODES[code]["retryable"]


async def test_platform_side_read_only_guard() -> None:
    executor = _executor()
    with pytest.raises(CapabilityExecutorError) as captured:
        # 平台侧第一道防线，不会发出 HTTP 请求
        await executor.execute("CREATE (n:Bill) RETURN n")
    assert captured.value.code == "QUERY_FORBIDDEN"


async def test_read_only_guard_allows_match_with_write_word_in_string() -> None:
    executor = _executor()
    result = await executor.execute(
        "MATCH (n:Bill) WHERE n.name = 'CREATE' RETURN n.name AS name",
        {"__rows": [{"name": "CREATE"}]},
    )
    assert result.rows == [{"name": "CREATE"}]
