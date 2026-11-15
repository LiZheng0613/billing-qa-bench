"""业务枚举常量（数据库中以字符串存储，便于演进）。"""

from __future__ import annotations

from enum import StrEnum


class CaseType(StrEnum):
    SINGLE = "SINGLE"
    STATIC_MULTI = "STATIC_MULTI"
    DYNAMIC_MULTI = "DYNAMIC_MULTI"


class CaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    DRAFT = "DRAFT"


class TemplateType(StrEnum):
    USER_SIMULATOR = "USER_SIMULATOR"
    LLM_JUDGE = "LLM_JUDGE"


class TemplateStrategy(StrEnum):
    LATEST = "LATEST"
    PINNED = "PINNED"


class ModelRole(StrEnum):
    SIMULATOR = "SIMULATOR"
    JUDGE = "JUDGE"


class JudgeStrategy(StrEnum):
    AVG = "AVG"
    VETO = "VETO"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class ExecutionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    MAX_TURNS_EXCEEDED = "MAX_TURNS_EXCEEDED"
    REPETITION_ABORT = "REPETITION_ABORT"
    ABORTED = "ABORTED"


class TerminationStatus(StrEnum):
    GOAL_ACHIEVED = "GOAL_ACHIEVED"
    GIVE_UP = "GIVE_UP"
    REPETITION_ABORT = "REPETITION_ABORT"
    MAX_TURNS_EXCEEDED = "MAX_TURNS_EXCEEDED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class RevisionChangeType(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    IMPORT = "IMPORT"
    ROLLBACK = "ROLLBACK"


class ExecutionSource(StrEnum):
    BATCH = "BATCH"
    RETRY = "RETRY"
    DEBUG = "DEBUG"
    SANDBOX = "SANDBOX"
