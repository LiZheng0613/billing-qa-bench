"""答案契约判分子域。"""

from __future__ import annotations

from billing_qa.services.grading.answer_grader import grade_answer
from billing_qa.services.grading.behavior import detect_behavior, resolve_required_facts
from billing_qa.services.grading.claims import extract_claims, rule_claims
from billing_qa.services.grading.diagnostics import (
    build_diagnostics,
    collect_tool_calls,
    is_lucky_pass,
    suggest_root_cause,
)
from billing_qa.services.grading.envelope import EnvelopeResult, grade_envelope
from billing_qa.services.grading.extras import classify_extras
from billing_qa.services.grading.matchers import match_banned, match_fact
from billing_qa.services.grading.types import (
    GRADER_VERSION,
    SCORECARD_WEIGHTS,
    AnswerGrading,
    BehaviorResult,
    Claim,
    ExtraItem,
    FactOutcome,
)

__all__ = [
    "GRADER_VERSION",
    "SCORECARD_WEIGHTS",
    "AnswerGrading",
    "BehaviorResult",
    "Claim",
    "EnvelopeResult",
    "ExtraItem",
    "FactOutcome",
    "build_diagnostics",
    "classify_extras",
    "collect_tool_calls",
    "detect_behavior",
    "extract_claims",
    "grade_answer",
    "grade_envelope",
    "is_lucky_pass",
    "match_banned",
    "match_fact",
    "resolve_required_facts",
    "rule_claims",
    "suggest_root_cause",
]
