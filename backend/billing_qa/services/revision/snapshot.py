"""用例快照构建与字段级 Diff。"""

from __future__ import annotations

from typing import Any

from billing_qa.models.test_case import TestCase
from billing_qa.services.facts import build_verified_ledger

# 参与快照与 Diff 的字段（顺序即展示顺序）
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "description",
    "case_type",
    "status",
    "priority",
    "tags",
    "environment_id",
    "request_variables",
    "query",
    "expected_answer",
    "expected_rules",
    "turns",
    "session_expected",
    "fail_fast",
    "user_goal",
    "initial_query",
    "persona_description",
    "hidden_context",
    "max_turns",
    "terminate_conditions",
    "simulator_template_id",
    "simulator_template_strategy",
    "simulator_template_version",
    "judge_template_id",
    "judge_template_strategy",
    "judge_template_version",
    "prompt_overrides",
    "judge_model_ids",
    "judge_strategy",
    "pass_threshold",
    "grading_mode",
    "input_type",
    "as_of",
    "snapshot_id",
    "answer_envelope",
    "response_contract",
    "plan_spec",
    "risk_level",
    "owner",
    "review_status",
    "dataset_version",
    "dedupe_hash",
)

FIELD_LABELS: dict[str, str] = {
    "code": "用例编号",
    "name": "用例名称",
    "description": "用例描述",
    "case_type": "用例类型",
    "status": "状态",
    "priority": "优先级",
    "tags": "业务域标签",
    "environment_id": "执行环境",
    "request_variables": "请求变量",
    "query": "固定输入",
    "expected_answer": "预期口径/数值",
    "expected_rules": "规则校验配置",
    "turns": "多轮话术",
    "session_expected": "会话级预期",
    "fail_fast": "单轮失败中断",
    "user_goal": "测试目标",
    "initial_query": "首轮破冰",
    "persona_description": "用户画像",
    "hidden_context": "隐藏信息释放表",
    "max_turns": "最大轮次",
    "terminate_conditions": "终止条件",
    "simulator_template_id": "模拟器模板",
    "simulator_template_strategy": "模拟器引用策略",
    "simulator_template_version": "模拟器模板版本",
    "judge_template_id": "裁判模板",
    "judge_template_strategy": "裁判引用策略",
    "judge_template_version": "裁判模板版本",
    "prompt_overrides": "Prompt 局部覆写",
    "judge_model_ids": "裁判模型",
    "judge_strategy": "多裁判策略",
    "pass_threshold": "通过分数线",
    "grading_mode": "判分模式",
    "input_type": "输入形态",
    "as_of": "冻结时间",
    "snapshot_id": "数据快照",
    "answer_envelope": "期望事实（Must/May/MustNot）",
    "response_contract": "响应契约",
    "plan_spec": "取数计划（诊断）",
    "risk_level": "风险等级",
    "owner": "用例负责人",
    "review_status": "审核状态",
    "dataset_version": "数据集版本",
    "dedupe_hash": "去重指纹",
}


def build_snapshot(case: TestCase) -> dict[str, Any]:
    """生成用例全量配置快照（不可变，执行时绑定）。"""
    snapshot: dict[str, Any] = {}
    for field in SNAPSHOT_FIELDS:
        value = getattr(case, field, None)
        snapshot[field] = value if value is not None else None
    snapshot["id"] = case.id
    snapshot["name"] = case.name
    # 事实账本：已取证事实随执行快照冻结，便于判分上下文与审计回放。
    snapshot["verified_facts"] = build_verified_ledger(snapshot.get("answer_envelope"))
    return snapshot


def _label(path: str) -> str:
    root = path.split(".", 1)[0].split("[", 1)[0]
    return FIELD_LABELS.get(root, path)


def _diff_node(path: str, old: Any, new: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            _diff_node(f"{path}.{key}" if path else key, old.get(key), new.get(key), out)
        return
    if (
        isinstance(old, list)
        and isinstance(new, list)
        and len(old) == len(new)
        and all(isinstance(x, dict) for x in old + new)
    ):
        for index, (old_item, new_item) in enumerate(zip(old, new, strict=False)):
            _diff_node(f"{path}[{index}]", old_item, new_item, out)
        return
    if old == new:
        return
    if old is None:
        change_type = "added"
    elif new is None:
        change_type = "removed"
    else:
        change_type = "modified"
    out.append(
        {
            "path": path,
            "label": _label(path),
            "old": old,
            "new": new,
            "change_type": change_type,
        }
    )


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """字段级 Diff；仅比较业务字段，忽略 id 与时间戳。"""
    ignored = {"id", "created_at", "updated_at", "current_version", "created_by", "updated_by"}
    left = {key: value for key, value in old.items() if key not in ignored}
    right = {key: value for key, value in new.items() if key not in ignored}
    changes: list[dict[str, Any]] = []
    _diff_node("", left, right, changes)
    return changes


def summarize_changes(changes: list[dict[str, Any]], limit: int = 5) -> str:
    """生成人类可读的变更摘要。"""
    if not changes:
        return "无字段变更"
    labels: list[str] = []
    for change in changes:
        label = change["label"]
        if label not in labels:
            labels.append(label)
    head = "、".join(labels[:limit])
    suffix = f" 等 {len(labels)} 个字段" if len(labels) > limit else ""
    return f"变更 {len(changes)} 处：{head}{suffix}"
