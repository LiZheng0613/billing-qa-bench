"""答案契约判分的公共类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GRADER_VERSION = "answer-grader@0.2"

#: 答案侧评分卡权重（仅在通过闸门后计算，仅用于排序与优化）
SCORECARD_WEIGHTS: dict[str, float] = {
    "fact_completeness": 0.5,
    "assumption_honesty": 0.2,
    "clarity": 0.2,
    "helpful_extra": 0.1,
}


@dataclass
class Claim:
    """从答案中抽取出的断言原子。"""

    text: str
    kind: str = "text"  # number / date / entity / statement
    value: Any = None
    unit: str | None = None
    evidence: str = ""
    source: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit,
            "evidence": self.evidence,
            "source": self.source,
        }


@dataclass
class FactOutcome:
    """单条事实的判定结果。"""

    fact_id: str
    kind: str
    label: str | None
    expected: Any
    actual: Any
    passed: bool
    reason: str = ""
    evidence_ref: str | None = None
    is_gate: bool = True
    degraded: bool = False
    mode: str = ""
    bound_name: str | None = None
    conflict_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "kind": self.kind,
            "label": self.label,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "reason": self.reason,
            "evidence_ref": self.evidence_ref,
            "is_gate": self.is_gate,
            "degraded": self.degraded,
            "mode": self.mode,
            "bound_name": self.bound_name,
            "conflict_name": self.conflict_name,
        }


@dataclass
class BehaviorResult:
    """响应契约判定结果。"""

    detected: str = "answer"  # answer / clarify / assumption / partial / refuse
    allowed: bool = True
    stated_assumption: bool = False
    matched_scenario: str | None = None
    scenario_facts: list[dict[str, Any]] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "allowed": self.allowed,
            "stated_assumption": self.stated_assumption,
            "matched_scenario": self.matched_scenario,
            "scenario_facts": self.scenario_facts,
            "missing_slots": self.missing_slots,
            "violations": self.violations,
            "detail": self.detail,
        }


@dataclass
class ExtraItem:
    """期望之外的扩展内容分类。"""

    text: str
    kind: str  # grounded / unsupported / off_topic / may
    evidence_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "kind": self.kind, "evidence_ref": self.evidence_ref}


@dataclass
class AnswerGrading:
    """一次答案契约判分的完整结果。"""

    passed: bool
    gates: dict[str, bool]
    scorecard: dict[str, float]
    total_score: float
    facts: list[FactOutcome]
    extras: list[ExtraItem]
    behavior: BehaviorResult
    diagnostics: dict[str, Any]
    root_cause: str | None
    lucky_pass: bool
    grader_version: str = GRADER_VERSION
    degraded: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "gates": self.gates,
            "scorecard": self.scorecard,
            "total_score": self.total_score,
            "facts": [item.to_dict() for item in self.facts],
            "extras": [item.to_dict() for item in self.extras],
            "behavior": self.behavior.to_dict(),
            "diagnostics": self.diagnostics,
            "root_cause": self.root_cause,
            "lucky_pass": self.lucky_pass,
            "grader_version": self.grader_version,
            "degraded": self.degraded,
            "notes": self.notes,
        }

    def fact_dicts(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.facts]
