"""用例快照与字段级 Diff 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from billing_qa.services.revision.snapshot import (
    SNAPSHOT_FIELDS,
    build_snapshot,
    diff_snapshots,
    summarize_changes,
)


def _case(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {field: None for field in SNAPSHOT_FIELDS}
    base.update(
        {
            "id": 1,
            "code": "CASE-001",
            "name": "初始用例",
            "case_type": "SINGLE",
            "status": "ACTIVE",
            "priority": "P1",
            "query": "查询订单",
            "max_turns": 5,
            "fail_fast": False,
            "tags": ["账单"],
        }
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_snapshot_fields() -> None:
    snapshot = build_snapshot(_case())
    assert snapshot["id"] == 1
    assert snapshot["code"] == "CASE-001"
    assert set(SNAPSHOT_FIELDS).issubset(snapshot.keys())


def test_diff_detects_modified_and_added() -> None:
    left = build_snapshot(_case())
    right = build_snapshot(_case(name="改名用例", expected_answer="预期 ¥100"))
    changes = diff_snapshots(left, right)
    paths = {change["path"]: change for change in changes}
    assert paths["name"]["change_type"] == "modified"
    assert paths["name"]["old"] == "初始用例"
    assert paths["name"]["new"] == "改名用例"
    assert paths["expected_answer"]["change_type"] == "added"
    assert paths["name"]["label"] == "用例名称"


def test_diff_nested_lists() -> None:
    left = build_snapshot(_case(turns=[{"query": "第一轮"}, {"query": "第二轮"}]))
    right = build_snapshot(_case(turns=[{"query": "第一轮"}, {"query": "第二轮改名"}]))
    changes = diff_snapshots(left, right)
    assert [change["path"] for change in changes] == ["turns[1].query"]
    assert changes[0]["old"] == "第二轮"


def test_diff_ignores_system_fields() -> None:
    left = build_snapshot(_case())
    right = {"code": "CASE-001", "name": "初始用例", "id": 99, "current_version": 7}
    # 允许右侧为不完整快照：缺失字段会被视为 removed，因此这里只校验系统字段不参与
    changes = diff_snapshots(left, right)
    assert all(change["path"] not in {"id", "current_version"} for change in changes)


def test_summarize_changes() -> None:
    left = build_snapshot(_case())
    right = build_snapshot(_case(name="改名", priority="P0"))
    summary = summarize_changes(diff_snapshots(left, right))
    assert "用例名称" in summary
    assert "优先级" in summary
    assert summarize_changes([]) == "无字段变更"
