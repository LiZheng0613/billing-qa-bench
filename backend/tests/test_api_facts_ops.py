"""事实验证、评审冻结与运营 API 的端到端测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select, text

from billing_qa.db.session import SessionLocal
from billing_qa.main import app
from billing_qa.models.candidate import CaseCandidate
from billing_qa.models.capability import Capability, DataSource
from billing_qa.models.gold_query import GoldQuery
from billing_qa.models.test_case import TestCase as CaseModel
from billing_qa.services.capability.executors.cypher_tool import CypherToolExecutor
from tests.mocks import cypher_tool_mock as tool_mock

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


def _contract_payload(code: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "name": "事实闸门用例",
        "case_type": "SINGLE",
        "query": "本期账单金额是多少？",
        "grading_mode": "ANSWER_CONTRACT",
        "input_type": "CLEAR",
        "as_of": "2026-10",
        "answer_envelope": {
            "must": [
                {"id": "amount", "kind": "scalar", "value": 123.45, "unit": "CNY"},
                {"id": "due", "kind": "date", "value": "2026-10-31"},
            ],
            "must_not": [{"id": "banned", "kind": "text", "value": "系统错误"}],
            "extras_policy": "lenient",
        },
    }
    payload.update(overrides)
    return payload


async def test_facts_review_and_freeze_gate(client: httpx.AsyncClient) -> None:
    code = f"GATE-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_contract_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        assert created.json()["review_status"] == "NOT_REQUIRED"

        facts = await client.get(f"/cases/{case_id}/facts")
        assert facts.status_code == 200, facts.text
        items = facts.json()
        assert {item["id"] for item in items} == {"amount", "due", "banned"}
        assert all(item["verification_status"] == "PENDING" for item in items)

        # 未取证 → 评审通过被拒
        blocked = await client.post(
            f"/cases/{case_id}/review", json={"review_status": "APPROVED"}
        )
        assert blocked.status_code == 422, blocked.text
        assert "取证" in blocked.json()["message"]

        # 未评审通过 → 冻结被拒
        freeze_blocked = await client.post(f"/cases/{case_id}/freeze", json={})
        assert freeze_blocked.status_code == 422, freeze_blocked.text

        first = await client.post(
            f"/cases/{case_id}/facts/amount/verify",
            json={"verification_status": "VERIFIED", "evidence_ref": "gold:1"},
        )
        assert first.status_code == 200, first.text
        verified_fact = next(
            item for item in first.json()["answer_envelope"]["must"] if item["id"] == "amount"
        )
        assert verified_fact["verification_status"] == "VERIFIED"
        assert verified_fact["evidence_ref"] == "gold:1"

        # 仍有一条未取证
        still_blocked = await client.post(
            f"/cases/{case_id}/review", json={"review_status": "APPROVED"}
        )
        assert still_blocked.status_code == 422, still_blocked.text

        second = await client.post(
            f"/cases/{case_id}/facts/due/verify",
            json={"verification_status": "WAIVED", "note": "历史数据不可回溯"},
        )
        assert second.status_code == 200, second.text

        approved = await client.post(
            f"/cases/{case_id}/review", json={"review_status": "APPROVED"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["review_status"] == "APPROVED"

        frozen = await client.post(
            f"/cases/{case_id}/freeze", json={"dataset_version": "v9"}
        )
        assert frozen.status_code == 200, frozen.text
        assert frozen.json()["dataset_version"] == "v9"
        assert frozen.json()["status"] == "ACTIVE"

        # 撤销验证 → 已通过用例自动回退 PENDING，防止绕过闸门
        revoked = await client.post(
            f"/cases/{case_id}/facts/amount/verify",
            json={"verification_status": "NO_EVIDENCE", "note": "取数结果缺失"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["review_status"] == "PENDING"
        assert revoked.json()["reviewed_by"] is None
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_review_reject_and_content_change_gate(client: httpx.AsyncClient) -> None:
    code = f"REVIEW-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_contract_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        # 驳回必须填写原因
        reject_without_note = await client.post(
            f"/cases/{case_id}/review", json={"review_status": "REJECTED"}
        )
        assert reject_without_note.status_code == 422, reject_without_note.text
        assert "原因" in reject_without_note.json()["message"]

        rejected = await client.post(
            f"/cases/{case_id}/review",
            json={"review_status": "REJECTED", "note": "账期与事实口径不一致"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["review_status"] == "REJECTED"
        assert rejected.json()["reviewed_by"] is None

        # 驳回后可重置为待评审，便于整改后再提交
        reset = await client.post(
            f"/cases/{case_id}/review", json={"review_status": "PENDING"}
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["review_status"] == "PENDING"

        # 取证完成后评审通过
        for fact_id in ("amount", "due"):
            verified = await client.post(
                f"/cases/{case_id}/facts/{fact_id}/verify",
                json={"verification_status": "VERIFIED", "evidence_ref": "gold:1"},
            )
            assert verified.status_code == 200, verified.text
        approved = await client.post(
            f"/cases/{case_id}/review", json={"review_status": "APPROVED"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["review_status"] == "APPROVED"
        assert approved.json()["reviewed_by"]

        # 修改已通过用例的契约内容 → 自动退回待评审，旧审批不背书新内容
        edited = await client.put(
            f"/cases/{case_id}",
            json={"query": "帮我查一下 2026-11 的账单", "as_of": "2026-11"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["review_status"] == "PENDING"
        assert edited.json()["reviewed_by"] is None

        # 退回后不可直接冻结
        freeze_blocked = await client.post(f"/cases/{case_id}/freeze", json={})
        assert freeze_blocked.status_code == 422, freeze_blocked.text
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_create_approved_case_with_pending_facts_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    code = f"GATE-BAD-{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/cases", json=_contract_payload(code, review_status="APPROVED")
    )
    assert response.status_code == 422, response.text
    assert "取证" in response.json()["message"]


async def test_export_and_import_roundtrip_contract_fields(
    client: httpx.AsyncClient,
) -> None:
    code = f"IO-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_contract_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        exported = await client.get("/cases/export", params={"format": "json", "ids": case_id})
        assert exported.status_code == 200, exported.text
        payload = exported.json()
        assert payload["schema"] == "billing-qa/cases"
        assert payload["schema_version"] == 1
        assert payload["case_count"] == 1
        row = payload["items"][0]
        assert row["grading_mode"] == "ANSWER_CONTRACT"
        assert row["as_of"] == "2026-10"
        assert row["review_status"] == "NOT_REQUIRED"
        assert row["answer_envelope"]["must"][0]["id"] == "amount"

        imported = await client.post(
            "/cases/import",
            files={"file": ("cases.json", exported.content, "application/json")},
        )
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert result["failed"] == 0, result
        assert result["updated"] == 1

        detail = await client.get(f"/cases/{case_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["answer_envelope"]["must"][0]["value"] == 123.45
    finally:
        await client.delete(f"/cases/{case_id}")


async def test_candidate_promote_creates_case(client: httpx.AsyncClient) -> None:
    question = f"候选问题 {uuid.uuid4().hex[:8]}：本月账单金额是多少？"
    created = await client.post(
        "/candidates",
        json={
            "source": "MANUAL",
            "raw_question": question,
            "context": {"tenant": "demo"},
        },
    )
    assert created.status_code == 200, created.text
    candidate_id = created.json()["id"]
    promoted_case_id: int | None = None
    try:
        promoted = await client.post(
            f"/candidates/{candidate_id}/promote",
            json={"code": f"CAND-{candidate_id}-{uuid.uuid4().hex[:4]}"},
        )
        assert promoted.status_code == 200, promoted.text
        body = promoted.json()
        promoted_case_id = body["case_id"]
        assert body["candidate_id"] == candidate_id

        detail = await client.get(f"/cases/{promoted_case_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["query"] == question

        again = await client.post(f"/candidates/{candidate_id}/promote", json={})
        assert again.status_code == 409, again.text
    finally:
        if promoted_case_id:
            await client.delete(f"/cases/{promoted_case_id}")
        async with SessionLocal() as db:
            await db.execute(delete(CaseCandidate).where(CaseCandidate.id == candidate_id))
            await db.commit()


async def test_grader_eval_run_and_history(client: httpx.AsyncClient) -> None:
    mutants = await client.get("/grader/mutants")
    assert mutants.status_code == 200, mutants.text
    assert mutants.json()["mutants"], "内置变异集不能为空"

    run = await client.post("/grader/evals/run", json={"dataset_version": "builtin"})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["detected"] == body["total"] > 0
    assert body["false_kills"] == 0
    assert body["grader_version"].startswith("answer-grader@")

    history = await client.get("/grader/evals", params={"grader_version": body["grader_version"]})
    assert history.status_code == 200, history.text
    assert any(item["id"] == body["id"] for item in history.json()["items"])


async def test_caliber_crud(client: httpx.AsyncClient) -> None:
    key = f"caliber_{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/calibers",
        json={"key": key, "name": "账期口径", "definition": "自然月", "owner": "qa"},
    )
    assert created.status_code == 200, created.text
    caliber_id = created.json()["id"]
    try:
        updated = await client.put(f"/calibers/{caliber_id}", json={"name": "出账月口径"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "出账月口径"

        listed = await client.get("/calibers", params={"keyword": key})
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1

        deleted = await client.delete(f"/calibers/{caliber_id}")
        assert deleted.status_code == 200, deleted.text
    finally:
        await client.delete(f"/calibers/{caliber_id}")


async def test_data_source_probe_without_endpoint(
    client: httpx.AsyncClient,
) -> None:
    code = f"DS-{uuid.uuid4().hex[:8]}"
    created = await client.post(
        "/data-sources",
        json={
            "code": code,
            "name": "不可达执行器",
            "kind": "CYPHER_TOOL",
            "endpoint_url": "http://127.0.0.1:9",
            "timeout_seconds": 1,
            "auth_config": {"headers": {"X-Token": "secret"}},
        },
    )
    assert created.status_code == 200, created.text
    source_id = created.json()["id"]
    try:
        assert created.json()["has_auth"] is True
        assert created.json()["auth_config"] == {"headers": {"X-Token": "secret"}}
        probe = await client.post(f"/data-sources/{source_id}/probe")
        assert probe.status_code == 200, probe.text
        assert probe.json()["ok"] is False
        assert probe.json()["status"] == "FAILED"

        detail = await client.get(f"/data-sources/{source_id}")
        assert detail.json()["last_probe_status"] == "FAILED"
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(DataSource).where(DataSource.id == source_id))
            await db.commit()


async def test_capability_evidence_records_gold_query(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_mock.reset_state()

    def _fake_executor(_source: Any) -> CypherToolExecutor:
        return CypherToolExecutor(
            "http://tool.local",
            timeout_seconds=2,
            max_rows=50,
            transport=httpx.ASGITransport(app=tool_mock.app),
        )

    monkeypatch.setattr(
        "billing_qa.services.capability.service.build_executor", _fake_executor
    )

    code = f"DS-{uuid.uuid4().hex[:8]}"
    source = await client.post(
        "/data-sources",
        json={
            "code": code,
            "name": "Mock 执行器",
            "kind": "CYPHER_TOOL",
            "endpoint_url": "http://tool.local",
        },
    )
    assert source.status_code == 200, source.text
    source_id = source.json()["id"]

    capability = await client.post(
        "/capabilities",
        json={
            "key": f"cap_{uuid.uuid4().hex[:8]}",
            "name": "订单金额",
            "kind": "CYPHER",
            "source_id": source_id,
            "query_text": "MATCH (b:Bill) RETURN b.amount AS amount",
            "param_binding": {"order_no": "order_no"},
        },
    )
    assert capability.status_code == 200, capability.text
    capability_id = capability.json()["id"]

    case = await client.post("/cases", json=_contract_payload(f"EV-{uuid.uuid4().hex[:8]}"))
    assert case.status_code == 200, case.text
    case_id = case.json()["id"]

    try:
        probe = await client.post(f"/data-sources/{source_id}/probe")
        assert probe.status_code == 200, probe.text
        assert probe.json()["ok"] is True

        evidence = await client.post(
            f"/capabilities/{capability_id}/evidence",
            json={
                "case_id": case_id,
                "context": {"order_no": "A2024001"},
                "params": {
                    "__rows": [{"amount": 123.45}],
                    "__columns": [{"name": "amount", "type": "float"}],
                },
                "fact_ids": ["amount"],
            },
        )
        assert evidence.status_code == 200, evidence.text
        body = evidence.json()
        assert body["execution"]["rows"] == [{"amount": 123.45}]
        gold = body["gold_query"]
        assert gold["case_id"] == case_id
        assert gold["fact_ids"] == ["amount"]
        assert gold["template_params"]["order_no"] == "A2024001"
        assert gold["row_count"] == 1

        listed = await client.get("/gold-queries", params={"case_id": case_id})
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] >= 1
    finally:
        await client.delete(f"/cases/{case_id}")
        async with SessionLocal() as db:
            await db.execute(delete(GoldQuery).where(GoldQuery.capability_id == capability_id))
            await db.execute(delete(Capability).where(Capability.id == capability_id))
            await db.execute(delete(DataSource).where(DataSource.id == source_id))
            await db.commit()


async def test_stats_endpoints_return_shapes(client: httpx.AsyncClient) -> None:
    overview = await client.get("/stats/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    for key in ("execution_total", "pass_rate", "fact_total", "fact_pass_rate", "top_failed"):
        assert key in body

    facts = await client.get("/stats/facts")
    assert facts.status_code == 200, facts.text
    assert isinstance(facts.json(), list)

    datasets = await client.get("/stats/datasets")
    assert datasets.status_code == 200, datasets.text
    assert isinstance(datasets.json(), list)


async def test_stats_accept_timezone_aware_window(client: httpx.AsyncClient) -> None:
    """前端 toISOString() 传入的 UTC 时间（带 Z）不能触发 naive/aware 比较错误。"""
    window = {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00+08:00"}
    for path in ("/stats/overview", "/stats/facts"):
        response = await client.get(path, params=window)
        assert response.status_code == 200, response.text


async def test_list_cases_filters_by_grading_mode(client: httpx.AsyncClient) -> None:
    code = f"FILTER-{uuid.uuid4().hex[:8]}"
    created = await client.post("/cases", json=_contract_payload(code))
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    try:
        listed = await client.get(
            "/cases", params={"grading_mode": "ANSWER_CONTRACT", "keyword": code}
        )
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert [item["code"] for item in items] == [code]

        async with SessionLocal() as db:
            row = (
                await db.execute(select(CaseModel).where(CaseModel.id == case_id))
            ).scalar_one()
            assert row.grading_mode == "ANSWER_CONTRACT"
    finally:
        await client.delete(f"/cases/{case_id}")
