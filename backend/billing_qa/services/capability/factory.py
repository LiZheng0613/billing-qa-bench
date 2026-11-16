"""按数据源配置创建执行器实例。"""

from __future__ import annotations

import json
from typing import Any

from billing_qa.core.crypto import decrypt_value
from billing_qa.models.enums import DataSourceKind
from billing_qa.services.capability.executors.base import (
    BaseCapabilityExecutor,
    CapabilityExecutorError,
)
from billing_qa.services.capability.executors.cypher_tool import CypherToolExecutor


def build_executor(source: Any, *, transport: Any = None) -> BaseCapabilityExecutor:
    """``source`` 可以是 DataSource ORM 对象或等价 dict。"""
    kind = str(_field(source, "kind") or DataSourceKind.CYPHER_TOOL)
    endpoint = str(_field(source, "endpoint_url") or "").strip()
    if not endpoint:
        raise CapabilityExecutorError(
            "数据源未配置 endpoint_url", code="INVALID_REQUEST", status_code=422
        )
    auth = _decode_auth(_field(source, "auth_config"))
    headers = auth.get("headers") if isinstance(auth, dict) else None
    if kind == DataSourceKind.CYPHER_TOOL or kind == "CYPHER_TOOL":
        return CypherToolExecutor(
            endpoint,
            timeout_seconds=float(_field(source, "timeout_seconds") or 10.0),
            max_rows=int(_field(source, "max_rows") or 200),
            headers=headers if isinstance(headers, dict) else None,
            transport=transport,
        )
    raise CapabilityExecutorError(
        f"暂不支持的数据源类型: {kind}", code="INVALID_REQUEST", status_code=422
    )


def _field(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _decode_auth(raw: Any) -> dict[str, Any]:
    """兼容历史明文 dict 与应用层加密后的 JSON 字符串。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(decrypt_value(raw) or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}
