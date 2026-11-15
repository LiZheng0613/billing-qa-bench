"""OpenCode Serve 目录查询：可用模型与 Agent。

模型注册界面的「拉取可用模型」按钮走这里，避免用户手填 ``provider/model`` 出错。
只依赖 OpenCode V2 HTTP API：

- ``GET /api/model``：当前 serve 可用的模型快照
- ``GET /api/agent``：当前 serve 可用的 agent
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from billing_qa.core.exceptions import AppError
from billing_qa.core.logging import get_logger

logger = get_logger(__name__)


class OpenCodeCatalogError(AppError):
    """拉取 OpenCode 目录失败（对前端可直接展示）。"""

    def __init__(self, message: str):
        super().__init__(message, code="OPENCODE_CATALOG_ERROR", status_code=502)


@dataclass
class CatalogModel:
    provider_id: str
    model_id: str
    name: str
    status: str | None = None
    enabled: bool = True
    context_limit: int | None = None
    output_limit: int | None = None

    @property
    def value(self) -> str:
        """稳定标识，与 ``LLMClient._opencode_model_ref`` 的解析方式一致。"""
        return f"{self.provider_id}/{self.model_id}"


@dataclass
class CatalogAgent:
    id: str
    name: str
    mode: str | None = None
    hidden: bool = False


@dataclass
class OpenCodeCatalog:
    serve_url: str
    models: list[CatalogModel]
    agents: list[CatalogAgent]


def _as_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_models(payload: Any) -> list[CatalogModel]:
    """把 ``/api/model`` 响应归一化为按 provider 分组的扁平列表。"""
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise OpenCodeCatalogError("OpenCode 返回的模型列表格式不正确")

    models: list[CatalogModel] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("providerID") or "").strip()
        # V2 中 Info.id 与 Model.Ref.id 对齐；modelID 是 provider 侧的原始标识，兜底使用。
        model_id = str(item.get("id") or item.get("modelID") or "").strip()
        if not provider_id or not model_id:
            continue
        value = f"{provider_id}/{model_id}"
        if value in seen:
            continue
        seen.add(value)
        limit = item.get("limit") if isinstance(item.get("limit"), dict) else {}
        models.append(
            CatalogModel(
                provider_id=provider_id,
                model_id=model_id,
                name=str(item.get("name") or model_id),
                status=str(item["status"]) if item.get("status") else None,
                enabled=bool(item.get("enabled", True)),
                context_limit=_as_int(limit.get("context")),
                output_limit=_as_int(limit.get("output")),
            )
        )

    models.sort(key=lambda model: (model.provider_id.lower(), model.name.lower(), model.model_id))
    return models


def parse_agents(payload: Any) -> list[CatalogAgent]:
    """把 ``/api/agent`` 响应归一化；格式异常时返回空列表而不是阻断模型选择。"""
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []

    agents: list[CatalogAgent] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "").strip()
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        agents.append(
            CatalogAgent(
                id=agent_id,
                name=str(item.get("name") or agent_id),
                mode=str(item["mode"]) if item.get("mode") else None,
                hidden=bool(item.get("hidden", False)),
            )
        )

    agents.sort(key=lambda agent: (agent.hidden, agent.name.lower(), agent.id))
    return agents


def _auth(username: str | None, password: str | None) -> httpx.BasicAuth | None:
    if not password:
        return None
    return httpx.BasicAuth(username or "opencode", password)


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None,
    action: str,
) -> Any:
    try:
        response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise OpenCodeCatalogError(f"无法连接 OpenCode Serve（{action}）：{exc}") from exc
    if response.status_code == 404:
        raise OpenCodeCatalogError(
            f"OpenCode Serve 不支持 {url}（HTTP 404），请确认服务端为 V2 版本"
        )
    if response.status_code >= 400:
        raise OpenCodeCatalogError(
            f"{action}失败 HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OpenCodeCatalogError(f"{action}返回的不是合法 JSON") from exc


async def fetch_opencode_catalog(
    serve_url: str,
    *,
    username: str | None = None,
    password: str | None = None,
    directory: str | None = None,
    timeout_seconds: float = 15.0,
) -> OpenCodeCatalog:
    """读取 serve 上可用的模型与 agent。"""
    base = serve_url.rstrip("/")
    params = {"location[directory]": directory} if directory else None
    timeout = httpx.Timeout(max(timeout_seconds, 5.0), connect=10.0)

    async with httpx.AsyncClient(timeout=timeout, auth=_auth(username, password)) as client:
        models_payload = await _get_json(
            client, f"{base}/api/model", params=params, action="读取模型列表"
        )
        models = parse_models(models_payload)
        try:
            agents_payload = await _get_json(
                client, f"{base}/api/agent", params=params, action="读取 Agent 列表"
            )
        except OpenCodeCatalogError as exc:
            logger.warning("拉取 OpenCode Agent 列表失败（忽略）: %s", exc.message)
            agents: list[CatalogAgent] = []
        else:
            agents = parse_agents(agents_payload)

    return OpenCodeCatalog(serve_url=base, models=models, agents=agents)
