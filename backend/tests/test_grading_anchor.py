"""名称-数值绑定测试：中英文名称、对调数值、JSON key、冲突归属。"""

from __future__ import annotations

import pytest

from billing_qa.schemas.case import (
    AnswerEnvelope,
    AssumptionScenario,
    AssumptionSpec,
    ClarifySpec,
    ResponseContract,
)
from billing_qa.services.engine.rule_evaluator import evaluate_rules
from billing_qa.services.grading.behavior import (
    BehaviorResult,
    build_scenario_facts,
    resolve_required_facts,
)
from billing_qa.services.grading.claims import rule_claims
from billing_qa.services.grading.envelope import grade_envelope
from billing_qa.services.grading.matchers import fact_names, match_fact
from billing_qa.services.grading.types import FactOutcome


def _envelope(*, require_name: bool = False, aliases: dict | None = None) -> AnswerEnvelope:
    return AnswerEnvelope.model_validate(
        {
            "must": [
                {
                    "id": "total_amount",
                    "kind": "scalar",
                    "label": "账单总额",
                    "value": 100.0,
                    "unit": "CNY",
                    "aliases": ["账单合计", "total_amount", "bill_total"],
                    "require_name": require_name,
                },
                {
                    "id": "tax",
                    "kind": "scalar",
                    "label": "税额",
                    "value": 10.0,
                    "unit": "CNY",
                    "aliases": ["税金", "tax"],
                    "require_name": require_name,
                },
            ],
            "aliases": aliases or {},
            "extras_policy": "lenient",
        }
    )


def _outcomes(answer: str, envelope: AnswerEnvelope | None = None) -> dict[str, FactOutcome]:
    result = grade_envelope(envelope or _envelope(), answer, rule_claims(answer))
    return {item.fact_id: item for item in result.facts}


def test_scalar_binds_value_to_its_business_name() -> None:
    outcomes = _outcomes("您的账单总额为 ¥100.00，其中税额 ¥10.00。")
    assert outcomes["total_amount"].passed
    assert outcomes["total_amount"].mode == "name_bound"
    assert outcomes["total_amount"].bound_name == "账单总额"
    assert outcomes["tax"].passed
    assert outcomes["tax"].mode == "name_bound"


def test_scalar_rejects_swapped_values() -> None:
    outcomes = _outcomes("您的账单总额为 ¥10.00，其中税额 ¥100.00。")
    assert not outcomes["total_amount"].passed
    assert outcomes["total_amount"].mode == "name_mismatch"
    assert not outcomes["tax"].passed
    assert outcomes["tax"].mode == "name_mismatch"


def test_scalar_binds_english_json_keys() -> None:
    outcomes = _outcomes('{"total_amount": 100.0, "tax": 10.0}')
    assert outcomes["total_amount"].passed
    assert outcomes["total_amount"].mode == "json_key"
    assert outcomes["tax"].passed
    assert outcomes["tax"].mode == "json_key"


def test_scalar_rejects_swapped_english_json_keys() -> None:
    outcomes = _outcomes('{"total_amount": 10.0, "tax": 100.0}')
    assert not outcomes["total_amount"].passed
    assert outcomes["total_amount"].mode == "json_key_mismatch"
    assert not outcomes["tax"].passed
    assert outcomes["tax"].mode == "json_key_mismatch"


def test_scalar_supports_fenced_json_and_envelope_aliases() -> None:
    envelope = AnswerEnvelope.model_validate(
        {
            "must": [
                {"id": "a1", "kind": "scalar", "label": "账单总额", "value": 100.0, "unit": "CNY"},
                {"id": "a2", "kind": "scalar", "label": "税额", "value": 10.0, "unit": "CNY"},
            ],
            "aliases": {"账单总额": ["total_amount"], "税额": ["tax"]},
            "extras_policy": "lenient",
        }
    )
    answer = '查询结果如下：\n```json\n{"total_amount": 100.0, "tax": 10.0}\n```'
    outcomes = _outcomes(answer, envelope)
    assert outcomes["a1"].passed and outcomes["a1"].mode == "json_key"
    assert outcomes["a2"].passed and outcomes["a2"].mode == "json_key"


def test_scalar_name_can_bind_across_line_break() -> None:
    outcomes = _outcomes("账单总额：\n¥100.00\n税额：\n¥10.00")
    assert outcomes["total_amount"].passed
    assert outcomes["total_amount"].mode in {"name_bound", "window"}
    assert outcomes["tax"].passed


def test_require_name_rejects_unanchored_number() -> None:
    outcomes = _outcomes("合计 ¥100.00，另有 ¥10.00。", _envelope(require_name=True))
    assert not outcomes["total_amount"].passed
    assert outcomes["total_amount"].mode == "name_missing"
    assert not outcomes["tax"].passed


def test_number_owned_by_another_name_is_not_reused() -> None:
    outcomes = _outcomes("账单总额为 ¥10.00。")
    assert not outcomes["tax"].passed
    assert outcomes["tax"].mode == "name_conflict"
    assert outcomes["tax"].conflict_name == "账单总额"


def test_legacy_rule_amount_uses_name_binding() -> None:
    rules = {
        "amounts": [
            {"label": "账单总额", "value": 100, "tolerance": 0.01, "aliases": ["total_amount"]},
            {"label": "税额", "value": 10, "tolerance": 0.01, "aliases": ["tax"]},
        ]
    }
    normal = evaluate_rules("账单总额为 ¥100.00，税额 ¥10.00。", rules)
    assert normal.passed
    assert {check.mode for check in normal.checks} == {"name_bound"}

    swapped = evaluate_rules("账单总额为 ¥10.00，税额 ¥100.00。", rules)
    assert not swapped.passed
    assert {check.mode for check in swapped.checks} == {"name_mismatch"}

    english = evaluate_rules('{"total_amount": 100.0, "tax": 10.0}', rules)
    assert english.passed
    assert {check.mode for check in english.checks} == {"json_key"}


def test_legacy_rule_require_name_rejects_value_only() -> None:
    result = evaluate_rules(
        "合计 ¥100.00。",
        {
            "amounts": [
                {
                    "label": "账单总额",
                    "value": 100,
                    "tolerance": 0.01,
                    "require_name": True,
                }
            ]
        },
    )
    assert not result.passed
    assert result.checks[0].mode == "name_missing"


def test_legacy_rule_flags_number_owned_by_other_label() -> None:
    result = evaluate_rules(
        "账单总额为 ¥10.00。",
        {
            "amounts": [
                {"label": "账单总额", "value": 100, "tolerance": 0.01},
                {"label": "税额", "value": 10, "tolerance": 0.01},
            ]
        },
    )
    assert not result.passed
    tax_check = next(item for item in result.checks if item.label == "税额")
    assert not tax_check.passed
    assert tax_check.mode == "name_conflict"
    assert tax_check.bound_name is None


def test_scalar_match_fact_exposes_binding_metadata() -> None:
    fact = _envelope().must[0]
    outcome = match_fact(fact, "账单总额为 ¥100.00。", rule_claims("账单总额为 ¥100.00。"))
    assert outcome.passed
    assert outcome.mode == "name_bound"
    assert outcome.actual["bound_value"] == pytest.approx(100.0)


def test_assumption_scenario_fact_inherits_envelope_names() -> None:
    """场景事实用接口字段名（bill_total）时，要能对齐包络里的业务名称。"""
    contract = ResponseContract(
        allowed=["clarify", "answer_with_assumption"],
        clarify=ClarifySpec(must_ask_slot=["time_range"]),
        answer_with_assumption=AssumptionSpec(
            scenarios=[
                AssumptionScenario(assumption="按最近一期 2026-10 口径", must={"bill_total": 268.5})
            ]
        ),
    )
    envelope = AnswerEnvelope.model_validate(
        {
            "must": [
                {
                    "id": "total_amount",
                    "kind": "scalar",
                    "label": "账单总额",
                    "value": 268.5,
                    "unit": "CNY",
                    "aliases": ["账单合计", "total_amount", "bill_total"],
                }
            ],
            "extras_policy": "lenient",
        }
    )
    scenario = contract.answer_with_assumption.scenarios[0]
    behavior = BehaviorResult(
        detected="assumption", scenario_facts=build_scenario_facts(scenario)
    )
    required = resolve_required_facts(contract, envelope, behavior)

    answer = "按最近一期 2026-10 口径：手机号 13800138000 的账单总额为 ¥268.50，共 4 项费用。"
    vocabulary = {
        str(fact.id): fact_names(fact, envelope.aliases) for fact in envelope.must
    }
    outcome = match_fact(
        required[0],
        answer,
        rule_claims(answer),
        aliases=envelope.aliases,
        vocabulary=vocabulary,
    )
    assert outcome.passed
    assert outcome.mode == "name_bound"
    assert outcome.bound_name == "账单总额"
