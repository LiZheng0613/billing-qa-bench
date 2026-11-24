"""执行记录、轮次轨迹与裁判结果。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from billing_qa.db.base import Base, now


class ExecutionRecord(Base):
    """单条用例的一次执行（绑定执行时的 case_version 快照）。"""

    __tablename__ = "execution_record"
    __table_args__ = (
        Index("ix_execution_case_started", "case_id", "started_at"),
        Index("ix_execution_task_status", "task_id", "status"),
        Index("ix_execution_trace_uid", "trace_uid", unique=True),
        # 「每用例最新一次」（max(id) GROUP BY case_id）与单用例 top-1 的
        # 覆盖索引：尾列 id 让聚合与 ORDER BY id DESC 走 index-only scan
        Index("ix_execution_task_case", "task_id", "case_id", "id"),
        # 大盘通过率 / 时延 / 趋势等全窗口聚合按时间窗过滤
        Index("ix_execution_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_uid: Mapped[str] = mapped_column(String(36), nullable=False, comment="追踪 UUID")
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_task.id", ondelete="CASCADE"), nullable=True
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), nullable=False
    )
    case_version: Mapped[int] = mapped_column(Integer, nullable=False, comment="执行时的用例版本")
    case_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, comment="执行时用例全量快照")
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environment.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), default="BATCH")
    status: Mapped[str] = mapped_column(String(32), default="ERROR")
    attempt_no: Mapped[int] = mapped_column(
        Integer, default=1, comment="第几次尝试（环境类失败自动重试时递增）"
    )
    error_kind: Mapped[str | None] = mapped_column(
        String(16), comment="错误分类：ENV/BUSINESS，空表示无异常"
    )
    pass_flag: Mapped[bool | None] = mapped_column(Boolean)
    total_score: Mapped[float | None] = mapped_column(Float)
    termination_status: Mapped[str | None] = mapped_column(String(32))
    turns_used: Mapped[int] = mapped_column(Integer, default=0)
    max_turns: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float, comment="整体耗时")
    ttft_ms: Mapped[float | None] = mapped_column(Float, comment="平均首字延迟")
    simulator_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_tokens: Mapped[int] = mapped_column(Integer, default=0)
    simulator_template_version: Mapped[int | None] = mapped_column(Integer)
    judge_template_version: Mapped[int | None] = mapped_column(Integer)
    rule_result: Mapped[dict | None] = mapped_column(JSON, comment="规则硬校验结果")
    judge_result: Mapped[dict | None] = mapped_column(JSON, comment="聚合裁决结果")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    # ---- 答案契约判分结果（P0）----
    grading_mode: Mapped[str | None] = mapped_column(String(16), comment="执行时的判分模式")
    answer_grading: Mapped[dict | None] = mapped_column(JSON, comment="答案契约判分明细")
    fact_results: Mapped[list | None] = mapped_column(JSON, comment="事实级结果冗余副本")
    diagnostics: Mapped[dict | None] = mapped_column(JSON, comment="轨迹诊断（0 权重）")
    lucky_pass: Mapped[bool | None] = mapped_column(Boolean, comment="答案对但路径无效")
    root_cause: Mapped[str | None] = mapped_column(String(32), comment="失败根因（仅诊断）")
    grader_version: Mapped[str | None] = mapped_column(String(32), comment="判分器版本")
    as_of: Mapped[str | None] = mapped_column(String(32), comment="执行时冻结时间")
    # ---- 失败追问自检（LLM 裁决失败后追问被测 Agent 的自检结论）----
    self_check: Mapped[dict | None] = mapped_column(
        JSON, comment="追问自检结果（状态/触发原因/会话/Prompt/结论）"
    )

    turns: Mapped[list[TurnTrace]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TurnTrace.turn_index",
    )
    judges: Mapped[list[JudgeResult]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TurnTrace(Base):
    """轮次级全链路轨迹（输入/白盒/回复/校验）。"""

    __tablename__ = "turn_trace"
    __table_args__ = (
        Index("ix_turn_trace_exec_turn", "execution_id", "turn_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("execution_record.id", ondelete="CASCADE"), nullable=False
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # ---- 输入层 ----
    query: Mapped[str] = mapped_column(Text, nullable=False, comment="本轮发送给被测系统的输入")
    input_source: Mapped[str] = mapped_column(String(16), default="STATIC", comment="STATIC/SIMULATOR/INITIAL")
    simulator_thought: Mapped[str | None] = mapped_column(Text, comment="模拟器思考轨迹")
    simulator_status: Mapped[str | None] = mapped_column(String(16), comment="CONTINUE/GOAL_ACHIEVED/GIVE_UP")
    hidden_context_released: Mapped[list | None] = mapped_column(JSON, comment="本轮释放的隐藏信息")
    rendered_prompt: Mapped[str | None] = mapped_column(Text, comment="渲染后的模拟器 Prompt")

    # ---- 请求/响应 ----
    request_payload: Mapped[dict | None] = mapped_column(JSON, comment="最终请求报文")
    response_raw: Mapped[dict | None] = mapped_column(JSON, comment="原始响应报文")
    final_answer: Mapped[str | None] = mapped_column(Text, comment="被测系统最终回复")
    session_id: Mapped[str | None] = mapped_column(String(128))

    # ---- 白盒 ----
    intent: Mapped[str | None] = mapped_column(String(256), comment="识别意图")
    generated_sql: Mapped[str | None] = mapped_column(Text, comment="生成 SQL")
    api_params: Mapped[dict | None] = mapped_column(JSON, comment="业务接口入参")
    raw_db_data: Mapped[dict | None] = mapped_column(JSON, comment="底层原始取数结果")
    tool_calls: Mapped[list | None] = mapped_column(
        JSON, comment="本轮全部工具调用（Cypher/SQL/API），仅诊断用途"
    )
    evidence: Mapped[list | None] = mapped_column(
        JSON, comment="事实账本：fact → value → 调用引用"
    )

    # ---- 性能与校验 ----
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    rule_check: Mapped[dict | None] = mapped_column(JSON, comment="单轮规则校验结果")
    simulator_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    simulator_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    execution: Mapped[ExecutionRecord] = relationship(back_populates="turns")


class JudgeResult(Base):
    """单裁判评分明细（支持多裁判交叉裁决）。"""

    __tablename__ = "judge_result"
    __table_args__ = (
        Index("ix_judge_result_execution", "execution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("execution_record.id", ondelete="CASCADE"), nullable=False
    )
    model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_config.id", ondelete="SET NULL"), nullable=True
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    pass_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    dimension_scores: Mapped[dict | None] = mapped_column(JSON)
    error_tags: Mapped[list | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text, comment="裁判归因说明（CoT）")
    raw_response: Mapped[dict | None] = mapped_column(JSON)
    judge_prompt: Mapped[str | None] = mapped_column(Text, comment="渲染后的裁判 Prompt")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)

    execution: Mapped[ExecutionRecord] = relationship(back_populates="judges")
