"""ORM 模型统一导出，便于 Base.metadata 收集全部表。"""

from __future__ import annotations

from billing_qa.models.candidate import Caliber, CaseCandidate, GraderEval
from billing_qa.models.capability import Capability, DataSource
from billing_qa.models.environment import Environment, EnvironmentVariable
from billing_qa.models.execution import ExecutionRecord, JudgeResult, TurnTrace
from billing_qa.models.gold_query import FactResult, GoldQuery
from billing_qa.models.model_config import ModelConfig
from billing_qa.models.prompt_template import PromptTemplate, PromptTemplateVersion
from billing_qa.models.task import BatchTask
from billing_qa.models.test_case import TestCase, TestCaseRevision
from billing_qa.models.user import (
    SysAuditLog,
    SysPermission,
    SysRole,
    SysUser,
    sys_role_permission,
    sys_user_role,
)

__all__ = [
    "BatchTask",
    "Caliber",
    "Capability",
    "CaseCandidate",
    "DataSource",
    "Environment",
    "EnvironmentVariable",
    "ExecutionRecord",
    "FactResult",
    "GoldQuery",
    "GraderEval",
    "JudgeResult",
    "ModelConfig",
    "PromptTemplate",
    "PromptTemplateVersion",
    "SysAuditLog",
    "SysPermission",
    "SysRole",
    "SysUser",
    "TestCase",
    "TestCaseRevision",
    "TurnTrace",
    "sys_role_permission",
    "sys_user_role",
]
