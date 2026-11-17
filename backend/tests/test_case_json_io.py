"""用例 JSON 导入导出：格式、回灌、预览与冲突策略。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import text

from billing_qa.core.exceptions import ValidationError
from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.services.io.case_json import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    parse_case_file,
)

pytestmark = pytest.mark.asyncio


async def _db_ready() -> bool:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    if not await _db_ready():
        pytest.skip("PostgreSQL 不可用，跳过 API 集成测试")
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test/api/v1", timeout=60
        ) as http:
            response = await http.post(
                "/auth/login", json={"username": "admin", "password": "admin123"}
            )
            assert response.status_code == 200, response.text
            http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
            yield http


def _payload(code: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "name": "JSON 导入导出用例",
        "description": "覆盖嵌套字段与契约配置",
        "case_type": "SINGLE",
        "priority": "P0",
        "tags": ["答案契约", "导入导出"],
        "query": "最近一期账单总额是多少？",
        "grading_mode": "ANSWER_CONTRACT",
        "input_type": "UNDERSPECIFIED",
        "as_of": "2026-10",
        "snapshot_id": "SNAP-2026-10",
        "risk_level": "LOW",
        "owner": "qa-team",
        "prompt_overrides": {"judge": "只关注金额与账期"},
        "plan_spec": {"required_capabilities": ["bill_query"], "max_tool_calls": 3},
        "answer_envelope": {
            "must": [
                {
                    "id": "amount",
                    "kind": "scalar",
                    "label": "账单总额",
                    "value": 268.5,
                    "unit": "CNY",
                    "tolerance": 0.01,
                    "aliases": ["total_amount"],
                    "require_name": True,
                },
                {"id": "period", "kind": "text", "label": "账期", "keywords": ["2026-10"]},
            ],
            "may": [],
            "must_not": [{"id": "banned", "kind": "text", "value": "系统错误"}],
            "extras_policy": "lenient",
        },
        "response_contract": {
            "allowed": ["clarify", "answer_with_assumption"],
            "clarify": {"must_ask_slot": ["time_range"], "must_not_assert": ["amount"]},
            "answer_with_assumption": {
                "must_state_assumption": True,
                "scenarios": [
                    {"assumption": "按最近一期账期 2026-10 口径", "must": {"amount": 268.5}}
                ],
            },
        },
    }
    payload.update(overrides)
    return payload


async def _export(
    client: httpx.AsyncClient, case_id: int
) -> dict[str, Any]:
    response = await client.get("/cases/export", params={"format": "json", "ids": case_id})
    assert response.status_code == 200, response.text
    return response.json()


async def _import(
    client: httpx.AsyncClient,
    payload: dict[str, Any] | list[Any],
    *,
    dry_run: bool = False,
    on_conflict: str = "upsert",
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = await client.post(
        "/cases/import",
        params={"dry_run": dry_run, "on_conflict": on_conflict},
        files={"file": ("cases.json", body, "application/json")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_export_envelope_and_roundtrip_full_fields(client: httpx.AsyncClient) -> None:
    code = f"JSON-IO-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        payload = await _export(client, case_id)
        assert payload["schema"] == SCHEMA_NAME
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["case_count"] == 1
        item = payload["items"][0]
        # 引用型字段导出为编码，不导出数据库自增 id
        assert "environment_id" not in item
        assert "judge_model_ids" not in item
        assert item["judge_model_codes"] == []
        # 嵌套字段完整保留
        assert item["answer_envelope"]["must"][0]["require_name"] is True
        assert item["response_contract"]["clarify"]["must_ask_slot"] == ["time_range"]
        assert item["plan_spec"]["max_tool_calls"] == 3
        assert item["prompt_overrides"]["judge"] == "只关注金额与账期"
        assert item["tags"] == ["答案契约", "导入导出"]

        result = await _import(client, payload)
        assert result["total"] == 1
        assert result["updated"] == 1
        assert result["failed"] == 0, result
        assert result["items"][0]["action"] == "updated"

        detail = (await client.get(f"/cases/{case_id}")).json()
        assert detail["description"] == "覆盖嵌套字段与契约配置"
        assert detail["answer_envelope"]["must"][0]["aliases"] == ["total_amount"]
        assert detail["response_contract"]["allowed"] == ["clarify", "answer_with_assumption"]
        assert detail["prompt_overrides"]["judge"] == "只关注金额与账期"
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_dry_run_previews_without_persisting(client: httpx.AsyncClient) -> None:
    code = f"JSON-DRY-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        payload = await _export(client, case_id)
        payload["items"][0]["name"] = "预览不应落库的新名称"

        preview = await _import(client, payload, dry_run=True)
        assert preview["dry_run"] is True
        assert preview["updated"] == 1
        assert preview["case_ids"] == []

        detail = (await client.get(f"/cases/{case_id}")).json()
        assert detail["name"] == "JSON 导入导出用例"
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_conflict_policies_skip_and_error(client: httpx.AsyncClient) -> None:
    code = f"JSON-CONFLICT-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        payload = await _export(client, case_id)

        skipped = await _import(client, payload, on_conflict="skip")
        assert skipped["skipped"] == 1
        assert skipped["updated"] == 0
        assert skipped["items"][0]["action"] == "skipped"

        failed = await _import(client, payload, on_conflict="error")
        assert failed["failed"] == 1
        assert failed["items"][0]["action"] == "failed"
        assert "已存在" in failed["errors"][0]["message"]
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_legacy_array_and_unknown_fields(client: httpx.AsyncClient) -> None:
    code = f"JSON-LEGACY-{uuid.uuid4().hex[:8]}"
    legacy = [
        {
            "code": code,
            "name": "旧版扁平数组",
            "case_type": "SINGLE",
            "query": "多少钱？",
            "tags": "账单,回归",
            "unknown_field_should_be_ignored": "x",
        }
    ]
    result = await _import(client, legacy)
    assert result["created"] == 1, result
    assert any("未知字段" in warning for warning in result["warnings"])

    detail = (
        await client.get("/cases", params={"keyword": code})
    ).json()
    case = next(item for item in detail["items"] if item["code"] == code)
    try:
        assert case["tags"] == ["账单", "回归"]
    finally:
        await client.delete(f"/cases/{case['id']}")


async def test_export_filters_match_case_list(client: httpx.AsyncClient) -> None:
    marker = f"EXPORT-FILTER-{uuid.uuid4().hex[:8]}"
    active_code = f"{marker}-A"
    disabled_code = f"{marker}-D"
    active = await client.post(
        "/cases",
        json=_payload(active_code, name=f"{marker} 启用用例"),
    )
    assert active.status_code == 200, active.text
    disabled = await client.post(
        "/cases",
        json=_payload(disabled_code, name=f"{marker} 停用用例", status="DISABLED"),
    )
    assert disabled.status_code == 200, disabled.text
    active_id = active.json()["id"]
    disabled_id = disabled.json()["id"]
    try:
        filtered = await client.get(
            "/cases/export",
            params={"format": "json", "keyword": marker, "status": "ACTIVE"},
        )
        assert filtered.status_code == 200, filtered.text
        payload = filtered.json()
        assert {item["code"] for item in payload["items"]} == {active_code}
        assert payload["filters"]["keyword"] == marker
        assert payload["filters"]["status"] == "ACTIVE"

        unfiltered = await client.get(
            "/cases/export", params={"format": "json", "keyword": marker}
        )
        assert {item["code"] for item in unfiltered.json()["items"]} == {
            active_code,
            disabled_code,
        }

        xlsx = await client.get(
            "/cases/export", params={"format": "xlsx", "ids": active_id}
        )
        assert xlsx.status_code == 200, xlsx.text
        sheet = load_workbook(BytesIO(xlsx.content)).active
        headers = [cell.value for cell in sheet[1]]
        assert "用例状态(ACTIVE/DISABLED/DRAFT)" in headers
        assert sheet.cell(row=2, column=headers.index("用例编号") + 1).value == active_code
        assert (
            sheet.cell(
                row=2, column=headers.index("用例状态(ACTIVE/DISABLED/DRAFT)") + 1
            ).value
            == "ACTIVE"
        )
    finally:
        await client.delete(f"/cases/{active_id}")
        await client.delete(f"/cases/{disabled_id}")


async def test_excel_roundtrip_keeps_final_fields(client: httpx.AsyncClient) -> None:
    code = f"EXCEL-ROUND-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/cases",
        json=_payload(
            code,
            status="DRAFT",
            request_variables={"userid": "13800138000", "segment": 7},
            prompt_overrides={"judge": "只关注金额", "simulator": "保持简洁"},
        ),
    )
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        exported = await client.get(
            "/cases/export", params={"format": "xlsx", "ids": case_id}
        )
        assert exported.status_code == 200, exported.text
        imported = await client.post(
            "/cases/import",
            files={
                "file": (
                    "cases.xlsx",
                    exported.content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert result["failed"] == 0, result
        assert result["updated"] == 1

        detail = (await client.get(f"/cases/{case_id}")).json()
        assert detail["status"] == "DRAFT"
        assert detail["description"] == "覆盖嵌套字段与契约配置"
        assert detail["request_variables"] == {
            "userid": "13800138000",
            "segment": 7,
        }
        assert detail["prompt_overrides"] == {
            "judge": "只关注金额",
            "simulator": "保持简洁",
        }
        assert detail["answer_envelope"]["must"][0]["id"] == "amount"
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_parse_case_file_rejects_unsupported_schema() -> None:
    with pytest.raises(ValidationError, match="schema"):
        parse_case_file(b'{"schema": "other/format", "items": []}')
    with pytest.raises(ValidationError, match="schema_version"):
        parse_case_file(
            json.dumps(
                {"schema": SCHEMA_NAME, "schema_version": 99, "items": [{"code": "X"}]}
            ).encode("utf-8")
        )
    with pytest.raises(ValidationError, match="没有用例"):
        parse_case_file(json.dumps({"schema": SCHEMA_NAME, "items": []}).encode("utf-8"))
