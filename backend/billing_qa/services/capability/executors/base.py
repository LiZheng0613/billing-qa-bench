"""取数能力执行器抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from billing_qa.core.exceptions import AppError


class CapabilityExecutorError(AppError):
    """执行器调用失败（错误码与工具契约保持一致）。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "TOOL_ERROR",
        status_code: int = 502,
        retryable: bool = False,
        detail: dict[str, Any] | None = None,
    ):
        super().__init__(message, code=code, status_code=status_code)
        self.retryable = retryable
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "detail": self.detail,
        }


@dataclass
class ExecutionResult:
    """一次取数的标准化结果；``rows`` 即事实快照。"""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float | None = None
    query_id: str | None = None
    executor_version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "query_id": self.query_id,
            "executor_version": self.executor_version,
        }


@dataclass
class ToolLogEntry:
    seq: int
    session_id: str | None
    kind: str
    query: str | None = None
    params: dict[str, Any] | None = None
    status: str = "SUCCESS"
    rows: int = 0
    elapsed_ms: float | None = None
    error: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "session_id": self.session_id,
            "kind": self.kind,
            "query": self.query,
            "params": self.params,
            "status": self.status,
            "rows": self.rows,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "created_at": self.created_at,
        }


class BaseCapabilityExecutor(ABC):
    """能力执行器接口：平台只依赖这一层，不关心底层是 Cypher 还是 SQL。"""

    @abstractmethod
    async def execute(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def meta(self) -> dict[str, Any]:
        raise NotImplementedError

    async def logs(
        self, *, session_id: str | None = None, since_seq: int = 0, limit: int = 200
    ) -> list[ToolLogEntry]:
        raise NotImplementedError
