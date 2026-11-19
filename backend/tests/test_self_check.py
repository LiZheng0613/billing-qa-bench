"""失败追问自检：触发判定、变量构建、裁判理由格式化与同会话追问单元测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from billing_qa.services.adapter.target_api import TargetApiError
from billing_qa.services.engine.judge import JudgeOutcome
from billing_qa.services.engine.self_check import (
    SELF_CHECK_DONE,
    SELF_CHECK_ERROR,
    SELF_CHECK_SKIPPED,
    build_self_check_variables,
    format_judge_reasons,
    format_rule_failures,
    run_self_check,
    self_check_skip_reason,
    self_check_trigger_reason,
)

# --------------------------------------------------------------------------- #
# 测试夹具数据
# --------------------------------------------------------------------------- #


def _judge(**overrides: Any) -> JudgeOutcome:
    base: dict[str, Any] = {
        "model_id": 1,
        "model_name": "judge-strict",
        "pass_flag": False,
        "total_score": 18.0,
        "dimension_scores": {
            "data_accuracy": 0,
            "logic_consistency": 8,
            "goal_completion": 10,
            "tone_clarity": 0,
        },
        "error_tags": ["数值错误/数据幻觉"],
        "reason": "账单总额 ¥999.00 与底层取数不一致，存在编造数据。",
        "raw_response": {},
        "judge_prompt": "",
    }
    base.update(overrides)
    return JudgeOutcome(**base)


def _turns() -> list[dict[str, Any]]:
    return [
        {"query": "帮我查一下 10 月的账单总额。", "final_answer": "10 月账单总额为 ¥999.00。", "session_id": None},
        {"query": "那一共用了多少流量？", "final_answer": "共使用 20GB 流量。", "session_id": "sess-abc-123"},
    ]


def _snapshot() -> dict[str, Any]:
    return {
        "name": "账单总额查询",
        "code": "TC-BILL-001",
        "case_type": "SINGLE",
        "max_turns": 3,
        "expected_answer": "手机号 13900139000 在 2026-10 的账单总额为 ¥100.00。",
        "user_goal": "核验账单总额口径",
        "pass_threshold": 60,
        "request_variables": {"phone": "13900139000"},
    }


class _FakeAdapter:
    """记录调用参数的假被测适配器，模拟同会话追加轮次。"""

    def __init__(self, *, session_mode: str = "SERVER_RETURNED", error: Exception | None = None):
        self.env = SimpleNamespace(session_mode=session_mode)
        self.calls: list[dict[str, Any]] = []
        self._error = error

    async def chat(self, query, session_id=None, history=None, extra_variables=None):
        self.calls.append(
            {
                "query": query,
                "session_id": session_id,
                "history": history,
                "extra_variables": extra_variables,
            }
        )
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            final_answer="我复查后发现总额取错了口径，正确应为 ¥100.00。",
            session_id="sess-abc-123",
            request_payload={"query": query},
            response_payload={"content": "我复查后发现总额取错了口径。"},
        )


# --------------------------------------------------------------------------- #
# 触发判定
# --------------------------------------------------------------------------- #


def test_trigger_legacy_fail() -> None:
    assert (
        self_check_trigger_reason(
            status="FAIL", judge_result={"pass": False}, has_records=True
        )
        == "LLM 裁决不通过"
    )


def test_trigger_legacy_pass_no_trigger() -> None:
    assert (
        self_check_trigger_reason(
            status="PASS", judge_result={"pass": True}, has_records=True
        )
        is None
    )


def test_trigger_max_turns_breakout() -> None:
    """熔断类红线强制不通过，同样触发追问自检。"""
    reason = self_check_trigger_reason(
        status="MAX_TURNS_EXCEEDED", judge_result={"pass": False}, has_records=True
    )
    assert reason == "LLM 裁决不通过"


def test_trigger_contract_fail() -> None:
    assert (
        self_check_trigger_reason(
            status="FAIL",
            judge_result={"mode": "ANSWER_CONTRACT", "passed": False},
            has_records=True,
        )
        == "答案契约判分不通过"
    )


def test_trigger_contract_pass_no_trigger() -> None:
    assert (
        self_check_trigger_reason(
            status="PASS",
            judge_result={"mode": "ANSWER_CONTRACT", "passed": True},
            has_records=True,
        )
        is None
    )


def test_trigger_excluded_statuses() -> None:
    """环境异常 / 被终止 / 无对话轮次不追问（没有可归因的裁决）。"""
    for status in ("ERROR", "ABORTED"):
        assert (
            self_check_trigger_reason(
                status=status, judge_result={"pass": False}, has_records=True
            )
            is None
        )
    assert (
        self_check_trigger_reason(
            status="FAIL", judge_result={"pass": False}, has_records=False
        )
        is None
    )
    assert (
        self_check_trigger_reason(status="FAIL", judge_result=None, has_records=True)
        is None
    )


def test_skip_reasons() -> None:
    assert "执行异常" in self_check_skip_reason(
        status="ERROR", judge_result=None, has_records=False
    )
    assert "未产生有效对话轮次" in self_check_skip_reason(
        status="FAIL", judge_result={"pass": True}, has_records=False
    )
    assert "LLM 裁决通过" in self_check_skip_reason(
        status="PASS", judge_result={"pass": True}, has_records=True
    )
    assert "契约判分通过" in self_check_skip_reason(
        status="PASS",
        judge_result={"mode": "ANSWER_CONTRACT", "passed": True},
        has_records=True,
    )
    assert "未产出 LLM 裁决结果" in self_check_skip_reason(
        status="PASS", judge_result=None, has_records=True
    )


# --------------------------------------------------------------------------- #
# 裁判理由与规则失败项格式化
# --------------------------------------------------------------------------- #


def test_format_judge_reasons_legacy() -> None:
    text = format_judge_reasons({"pass": False, "error_tags": ["数值错误/数据幻觉"]}, [_judge()])
    assert "【裁判·judge-strict】18.0 分，未通过" in text
    assert "错误标签：数值错误/数据幻觉" in text
    assert "归因说明：账单总额 ¥999.00 与底层取数不一致" in text


def test_format_judge_reasons_contract() -> None:
    judge_result = {
        "mode": "ANSWER_CONTRACT",
        "passed": False,
        "gates": {"amount_gate": False, "period_gate": True},
        "root_cause": "FACT_MISS",
    }
    facts = [
        {"label": "账单总额", "passed": False, "is_gate": True, "expected": "100.00", "actual": "999.00", "reason": "数值不一致"},
        {"label": "仅供参考", "passed": True, "is_gate": False, "expected": None, "actual": None},
    ]
    text = format_judge_reasons(judge_result, [_judge(pass_flag=True, total_score=85.0, reason="质量分仅供参考")], facts)
    assert "【契约判分】不通过，未过闸门：amount_gate" in text
    assert "【失败根因】FACT_MISS" in text
    assert "【事实未命中】账单总额：期望 '100.00'，实际 '999.00'；数值不一致" in text
    # 契约模式下 LLM 裁判标注为质量分参考
    assert "【质量分参考·judge-strict】85.0 分，通过" in text


def test_format_judge_reasons_empty() -> None:
    assert format_judge_reasons(None, []) == "（未产出裁判理由）"


def test_format_judge_reasons_policy_notes() -> None:
    judge_result = {"pass": False, "quality": {"policy_notes": ["数据准确性红线归零"]}}
    text = format_judge_reasons(judge_result, [])
    assert "【裁决策略】数据准确性红线归零" in text


def test_format_rule_failures() -> None:
    assert format_rule_failures(None) == "（本用例未执行规则硬校验）"
    assert "全部通过" in format_rule_failures({"checks": [{"passed": True}]})
    text = format_rule_failures(
        {
            "checks": [
                {
                    "label": "必须包含账期",
                    "passed": False,
                    "expected": "2026-10",
                    "actual": "2026-01",
                    "reason": "账期口径错误",
                }
            ]
        }
    )
    assert "- 必须包含账期：期望 '2026-10'，实际 '2026-01'；账期口径错误" in text


# --------------------------------------------------------------------------- #
# 模板变量构建
# --------------------------------------------------------------------------- #


def test_build_variables_full_set() -> None:
    variables = build_self_check_variables(
        snapshot=_snapshot(),
        turn_records=_turns(),
        judge_result={"pass": False, "error_tags": ["数值错误/数据幻觉"]},
        judges=[_judge(error_tags=["口径错误", "数值错误/数据幻觉"])],
        rule_result={"checks": [{"label": "必须包含账期", "passed": False, "expected": "2026-10", "actual": "2026-01"}]},
        total_score=18.0,
        pass_threshold=60.0,
        termination_status="COMPLETED",
    )
    expected_keys = {
        "case_name",
        "case_type",
        "chat_history",
        "final_answer",
        "first_query",
        "expected_answer",
        "actual_turns",
        "max_turns",
        "termination_status",
        "judge_reasons",
        "judge_total_score",
        "pass_threshold",
        "judge_error_tags",
        "rule_failures",
    }
    assert expected_keys <= set(variables)
    assert variables["case_name"] == "账单总额查询"
    assert variables["actual_turns"] == 2
    assert variables["max_turns"] == 3
    assert variables["judge_total_score"] == 18.0
    assert variables["pass_threshold"] == 60.0
    # 最后一轮回复
    assert variables["final_answer"] == "共使用 20GB 流量。"
    # 当前用例第一条用户输入
    assert variables["first_query"] == "帮我查一下 10 月的账单总额。"
    # 对话历史按用户/客服交替展开
    assert "用户: 帮我查一下 10 月的账单总额。" in variables["chat_history"]
    assert "客服: 10 月账单总额为 ¥999.00。" in variables["chat_history"]
    # 错误标签汇总去重（裁决聚合 + 各裁判）
    assert variables["judge_error_tags"] == "数值错误/数据幻觉、口径错误"
    # 裁判理由与规则失败项注入
    assert "judge-strict" in variables["judge_reasons"]
    assert "必须包含账期" in variables["rule_failures"]
    # 预期口径
    assert "¥100.00" in variables["expected_answer"]


# --------------------------------------------------------------------------- #
# run_self_check：同会话追问
# --------------------------------------------------------------------------- #


def test_run_self_check_skipped_when_pass() -> None:
    adapter = _FakeAdapter()
    result = asyncio.run(
        run_self_check(
            adapter=adapter,  # type: ignore[arg-type]
            prompt="请自检：{{judge_reasons}}",
            template_code="SELF_CHECK_DEFAULT",
            template_version=1,
            snapshot=_snapshot(),
            turn_records=_turns(),
            judge_result={"pass": True},
            judges=[],
        )
    )
    assert result.status == SELF_CHECK_SKIPPED
    assert result.skip_reason == "LLM 裁决通过，未触发追问自检"
    assert result.conclusion is None
    assert adapter.calls == []


def test_run_self_check_template_missing() -> None:
    adapter = _FakeAdapter()
    result = asyncio.run(
        run_self_check(
            adapter=adapter,  # type: ignore[arg-type]
            prompt=None,
            template_code=None,
            template_version=None,
            snapshot=_snapshot(),
            turn_records=_turns(),
            judge_result={"pass": False},
            judges=[],
        )
    )
    assert result.status == SELF_CHECK_ERROR
    assert "SELF_CHECK_DEFAULT" in (result.error or "")
    assert adapter.calls == []


def test_run_self_check_same_session_and_history() -> None:
    """核心行为：复用原对话最后轮次的会话值与完整历史追加追问。"""
    adapter = _FakeAdapter()
    snapshot = _snapshot()
    turns = _turns()
    result = asyncio.run(
        run_self_check(
            adapter=adapter,  # type: ignore[arg-type]
            prompt=(
                "用例 {{case_name}} 裁决不通过（{{judge_total_score}}/{{pass_threshold}}），"
                "裁判理由：{{judge_reasons}}，请自检。"
            ),
            template_code="SELF_CHECK_DEFAULT",
            template_name="默认失败追问自检模板",
            template_version=2,
            snapshot=snapshot,
            turn_records=turns,
            judge_result={"pass": False},
            judges=[_judge()],
            total_score=18.0,
            pass_threshold=60.0,
            status="FAIL",
            termination_status="COMPLETED",
        )
    )
    assert result.status == SELF_CHECK_DONE
    assert result.trigger == "LLM 裁决不通过"
    assert result.session_id == "sess-abc-123"
    assert result.template_code == "SELF_CHECK_DEFAULT"
    assert result.template_name == "默认失败追问自检模板"
    assert result.template_version == 2
    assert result.to_dict()["template_name"] == "默认失败追问自检模板"
    assert result.conclusion == "我复查后发现总额取错了口径，正确应为 ¥100.00。"
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.performed_at

    # 同一会话：session_id 取原对话最后一轮的生效值
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call["session_id"] == "sess-abc-123"
    # 完整历史：每轮 user/assistant 各一条
    assert call["history"] == [
        {"role": "user", "content": "帮我查一下 10 月的账单总额。"},
        {"role": "assistant", "content": "10 月账单总额为 ¥999.00。"},
        {"role": "user", "content": "那一共用了多少流量？"},
        {"role": "assistant", "content": "共使用 20GB 流量。"},
    ]
    # 快照变量一并透传
    assert call["extra_variables"] == {"phone": "13900139000"}
    # 渲染后的 Prompt 注入裁判理由与分数
    assert "账单总额查询" in call["query"]
    assert "18.0" in call["query"] and "60.0" in call["query"]
    assert "judge-strict" in call["query"]
    assert "数值错误/数据幻觉" in call["query"]


def test_run_self_check_client_generated_session() -> None:
    """CLIENT_GENERATED 模式沿用执行期生成的原会话值。"""
    adapter = _FakeAdapter(session_mode="CLIENT_GENERATED")
    result = asyncio.run(
        run_self_check(
            adapter=adapter,  # type: ignore[arg-type]
            prompt="请自检",
            template_code="SELF_CHECK_DEFAULT",
            template_version=1,
            snapshot=_snapshot(),
            turn_records=_turns(),
            judge_result={"pass": False},
            judges=[],
        )
    )
    assert result.status == SELF_CHECK_DONE
    assert result.session_id == "sess-abc-123"
    assert adapter.calls[0]["session_id"] == "sess-abc-123"


def test_run_self_check_adapter_error_propagates() -> None:
    """追问调用异常向上传播，由执行器兜底降级为自检 ERROR，不影响原裁决。"""
    adapter = _FakeAdapter(error=TargetApiError("被测服务 503"))
    try:
        asyncio.run(
            run_self_check(
                adapter=adapter,  # type: ignore[arg-type]
                prompt="请自检",
                template_code="SELF_CHECK_DEFAULT",
                template_version=1,
                snapshot=_snapshot(),
                turn_records=_turns(),
                judge_result={"pass": False},
                judges=[],
            )
        )
    except TargetApiError:
        pass
    else:
        raise AssertionError("追问调用异常应当向上传播")
