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
    CASE_AUTHOR = "CASE_AUTHOR"


class TemplateStrategy(StrEnum):
    LATEST = "LATEST"
    PINNED = "PINNED"


class ModelRole(StrEnum):
    SIMULATOR = "SIMULATOR"
    JUDGE = "JUDGE"
    CASE_AUTHOR = "CASE_AUTHOR"


class ModelConnectionType(StrEnum):
    """模型接入方式。"""

    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    OPENCODE_SERVE = "OPENCODE_SERVE"


class JudgeStrategy(StrEnum):
    AVG = "AVG"
    VETO = "VETO"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"
    SCHEDULED = "SCHEDULED"


class TaskScheduleType(StrEnum):
    """批量任务的执行方式。"""

    IMMEDIATE = "IMMEDIATE"
    SCHEDULED = "SCHEDULED"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


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


class GradingMode(StrEnum):
    """判分模式：存量兼容 / 答案契约。"""

    LEGACY = "LEGACY"
    ANSWER_CONTRACT = "ANSWER_CONTRACT"


class InputType(StrEnum):
    """用户输入形态，决定需要判定的是「事实」还是「行为」。"""

    CLEAR = "CLEAR"
    UNDERSPECIFIED = "UNDERSPECIFIED"
    AMBIGUOUS_REF = "AMBIGUOUS_REF"
    MULTI_INTENT = "MULTI_INTENT"
    VAGUE = "VAGUE"
    UNANSWERABLE = "UNANSWERABLE"
    CONTRADICTORY = "CONTRADICTORY"
    UNAUTHORIZED = "UNAUTHORIZED"


class FactKind(StrEnum):
    SCALAR = "scalar"
    SET = "set"
    TOPK = "topk"
    DATE = "date"
    BOOLEAN = "boolean"
    TEXT = "text"


class FactStatus(StrEnum):
    """用例事实的取证状态。"""

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    CONFLICT = "CONFLICT"
    NO_EVIDENCE = "NO_EVIDENCE"
    WAIVED = "WAIVED"
    STALE = "STALE"


class FactProvenance(StrEnum):
    CAPABILITY = "CAPABILITY"
    CAPABILITY_AUTO = "CAPABILITY_AUTO"
    AGENT_TRACE = "AGENT_TRACE"
    UPLOAD = "UPLOAD"
    MANUAL = "MANUAL"


class ToolKind(StrEnum):
    CYPHER = "CYPHER"
    SQL = "SQL"
    API = "API"
    CALC = "CALC"


class DataSourceKind(StrEnum):
    CYPHER_TOOL = "CYPHER_TOOL"
    SQL_DB = "SQL_DB"
    HTTP_API = "HTTP_API"


class CapabilityStatus(StrEnum):
    DRAFT = "DRAFT"
    CERTIFIED = "CERTIFIED"
    DEPRECATED = "DEPRECATED"


class GoldOrigin(StrEnum):
    CAPABILITY = "CAPABILITY"
    CAPABILITY_AUTO = "CAPABILITY_AUTO"
    AGENT_TRACE = "AGENT_TRACE"
    UPLOAD = "UPLOAD"
    MANUAL = "MANUAL"


class CandidateStatus(StrEnum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class ReviewStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ArbitrationVerdict(StrEnum):
    """事实仲裁结论。"""

    EXPECTATION_WRONG = "EXPECTATION_WRONG"
    EVIDENCE_WRONG = "EVIDENCE_WRONG"
    CALIBER_CONFLICT = "CALIBER_CONFLICT"
    CANNOT_ARBITRATE = "CANNOT_ARBITRATE"


class RootCause(StrEnum):
    """失败根因（仅诊断，不影响 pass）。"""

    RETRIEVAL = "RETRIEVAL"
    SYNTHESIS = "SYNTHESIS"
    HALLUCINATION = "HALLUCINATION"
    BEHAVIOR = "BEHAVIOR"
    UNKNOWN = "UNKNOWN"
