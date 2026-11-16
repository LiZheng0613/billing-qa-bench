"""add answer contract grading (capability / gold query / fact result / candidate)

Revision ID: e9f2b6c1a3d4
Revises: d8e1a4c2b7f3
Create Date: 2026-11-16 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f2b6c1a3d4"
down_revision: str | None = "d8e1a4c2b7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间"),
    ]


def upgrade() -> None:
    # ---------- test_case：答案契约字段 ----------
    op.add_column(
        "test_case",
        sa.Column(
            "grading_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'LEGACY'"),
            comment="LEGACY / ANSWER_CONTRACT",
        ),
    )
    op.alter_column("test_case", "grading_mode", server_default=None)
    op.add_column(
        "test_case",
        sa.Column(
            "input_type",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'CLEAR'"),
            comment="输入形态，见 InputType",
        ),
    )
    op.alter_column("test_case", "input_type", server_default=None)
    op.add_column(
        "test_case",
        sa.Column(
            "as_of",
            sa.String(length=32),
            nullable=True,
            comment="冻结时间（账期/日期），保证时间相关问题可复现",
        ),
    )
    op.add_column(
        "test_case",
        sa.Column("snapshot_id", sa.String(length=64), nullable=True, comment="数据快照标识"),
    )
    op.add_column(
        "test_case",
        sa.Column(
            "answer_envelope",
            sa.JSON(),
            nullable=True,
            comment="Must / May / MustNot 答案接受包络",
        ),
    )
    op.add_column(
        "test_case",
        sa.Column("response_contract", sa.JSON(), nullable=True, comment="非清晰输入的允许行为契约"),
    )
    op.add_column(
        "test_case",
        sa.Column("plan_spec", sa.JSON(), nullable=True, comment="能力集合与预算（仅诊断）"),
    )
    op.add_column(
        "test_case",
        sa.Column(
            "risk_level",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'LOW'"),
            comment="LOW / HIGH",
        ),
    )
    op.alter_column("test_case", "risk_level", server_default=None)
    op.add_column(
        "test_case", sa.Column("owner", sa.String(length=64), nullable=True, comment="用例负责人")
    )
    op.add_column(
        "test_case",
        sa.Column(
            "review_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'NOT_REQUIRED'"),
            comment="审核状态",
        ),
    )
    op.alter_column("test_case", "review_status", server_default=None)
    op.add_column(
        "test_case", sa.Column("reviewed_by", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "test_case",
        sa.Column("dataset_version", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "test_case",
        sa.Column("dedupe_hash", sa.String(length=64), nullable=True, comment="去重指纹"),
    )
    op.create_index(
        "ix_test_case_grading",
        "test_case",
        ["grading_mode", "input_type", "review_status"],
    )

    # ---------- execution_record：契约判分结果 ----------
    op.add_column(
        "execution_record",
        sa.Column("grading_mode", sa.String(length=16), nullable=True, comment="执行时的判分模式"),
    )
    op.add_column(
        "execution_record",
        sa.Column("answer_grading", sa.JSON(), nullable=True, comment="答案契约判分明细"),
    )
    op.add_column(
        "execution_record",
        sa.Column("fact_results", sa.JSON(), nullable=True, comment="事实级结果冗余副本"),
    )
    op.add_column(
        "execution_record",
        sa.Column("diagnostics", sa.JSON(), nullable=True, comment="轨迹诊断（0 权重）"),
    )
    op.add_column(
        "execution_record",
        sa.Column("lucky_pass", sa.Boolean(), nullable=True, comment="答案对但路径无效"),
    )
    op.add_column(
        "execution_record",
        sa.Column("root_cause", sa.String(length=32), nullable=True, comment="失败根因（仅诊断）"),
    )
    op.add_column(
        "execution_record",
        sa.Column("grader_version", sa.String(length=32), nullable=True, comment="判分器版本"),
    )
    op.add_column(
        "execution_record",
        sa.Column("as_of", sa.String(length=32), nullable=True, comment="执行时冻结时间"),
    )

    # ---------- turn_trace：工具轨迹 ----------
    op.add_column(
        "turn_trace",
        sa.Column(
            "tool_calls",
            sa.JSON(),
            nullable=True,
            comment="本轮全部工具调用（Cypher/SQL/API），仅诊断用途",
        ),
    )
    op.add_column(
        "turn_trace",
        sa.Column(
            "evidence", sa.JSON(), nullable=True, comment="事实账本：fact → value → 调用引用"
        ),
    )

    # ---------- data_source ----------
    op.create_table(
        "data_source",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="数据源编码"),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'CYPHER_TOOL'"),
            comment="CYPHER_TOOL/SQL_DB/HTTP_API",
        ),
        sa.Column(
            "endpoint_url",
            sa.String(length=512),
            nullable=True,
            comment="执行器或接口地址",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("auth_config", sa.JSON(), nullable=True, comment="鉴权配置（加密值由应用层解密）"),
        sa.Column(
            "timeout_seconds", sa.Float(), nullable=False, server_default=sa.text("10.0"),
            comment="单次取数超时",
        ),
        sa.Column(
            "max_rows", sa.Integer(), nullable=False, server_default=sa.text("200"),
            comment="单次返回行数上限",
        ),
        sa.Column("tool_version", sa.String(length=32), nullable=True, comment="执行器版本"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_probe_status", sa.String(length=16), nullable=True),
        sa.Column("last_probe_latency_ms", sa.Float(), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(), nullable=True),
        sa.Column("last_probe_message", sa.Text(), nullable=True),
        *_timestamp_columns(),
    )
    op.alter_column("data_source", "kind", server_default=None)
    op.alter_column("data_source", "timeout_seconds", server_default=None)
    op.alter_column("data_source", "max_rows", server_default=None)
    op.alter_column("data_source", "is_active", server_default=None)
    op.create_index("ix_data_source_code", "data_source", ["code"], unique=True)
    op.create_index("ix_data_source_kind", "data_source", ["kind", "is_active"])

    # ---------- capability ----------
    op.create_table(
        "capability",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=64), nullable=False, comment="能力键，如 bill_current"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="业务名称"),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'CYPHER'"),
            comment="CYPHER/SQL/API",
        ),
        sa.Column("description", sa.Text(), nullable=True, comment="业务语义描述，供路由分类"),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("data_source.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "query_text", sa.Text(), nullable=True, comment="参数化查询模板，$param 占位"
        ),
        sa.Column(
            "params_schema",
            sa.JSON(),
            nullable=True,
            comment='[{"name":"group_id","label":"集团客户","type":"entity","required":true}]',
        ),
        sa.Column(
            "param_binding", sa.JSON(), nullable=True, comment="context 槽位 → 查询参数 的机械映射"
        ),
        sa.Column(
            "intent_patterns", sa.JSON(), nullable=True, comment='["套餐","订购"]，用于 L1 意图路由'
        ),
        sa.Column(
            "lookup_queries",
            sa.JSON(),
            nullable=True,
            comment="实体下拉数据来源，如 {'entity:group_id': 'MATCH ...'}",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'DRAFT'"),
            comment="DRAFT/CERTIFIED/DEPRECATED",
        ),
        sa.Column("cert_result", sa.JSON(), nullable=True, comment="认证报告"),
        sa.Column("certified_at", sa.DateTime(), nullable=True),
        sa.Column("certified_by", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamp_columns(),
    )
    op.alter_column("capability", "kind", server_default=None)
    op.alter_column("capability", "version", server_default=None)
    op.alter_column("capability", "status", server_default=None)
    op.alter_column("capability", "is_active", server_default=None)
    op.create_index("ix_capability_key", "capability", ["key"], unique=True)
    op.create_index("ix_capability_status", "capability", ["status", "is_active"])

    # ---------- gold_query ----------
    op.create_table(
        "gold_query",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("test_case.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "capability_id",
            sa.Integer(),
            sa.ForeignKey("capability.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("capability_version", sa.Integer(), nullable=True),
        sa.Column(
            "origin",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'CAPABILITY'"),
            comment="CAPABILITY/CAPABILITY_AUTO/AGENT_TRACE/...",
        ),
        sa.Column("fact_ids", sa.JSON(), nullable=True, comment="本次取证覆盖的事实 id"),
        sa.Column("template_params", sa.JSON(), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("columns", sa.JSON(), nullable=True),
        sa.Column("rows", sa.JSON(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("executor_version", sa.String(length=32), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("gold_query", "origin", server_default=None)
    op.alter_column("gold_query", "row_count", server_default=None)
    op.alter_column("gold_query", "truncated", server_default=None)
    op.alter_column("gold_query", "is_active", server_default=None)
    op.create_index("ix_gold_query_case", "gold_query", ["case_id", "is_active"])
    op.create_index("ix_gold_query_capability", "gold_query", ["capability_id"])

    # ---------- fact_result ----------
    op.create_table(
        "fact_result",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "execution_id",
            sa.Integer(),
            sa.ForeignKey("execution_record.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_id", sa.String(length=64), nullable=False),
        sa.Column(
            "kind", sa.String(length=16), nullable=False, server_default=sa.text("'scalar'")
        ),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("expected", sa.JSON(), nullable=True),
        sa.Column("actual", sa.JSON(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("evidence_ref", sa.String(length=256), nullable=True),
        sa.Column("is_gate", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.alter_column("fact_result", "kind", server_default=None)
    op.alter_column("fact_result", "passed", server_default=None)
    op.alter_column("fact_result", "is_gate", server_default=None)
    op.create_index("ix_fact_result_execution", "fact_result", ["execution_id"])
    op.create_index("ix_fact_result_fact", "fact_result", ["fact_id", "passed"])

    # ---------- case_candidate ----------
    op.create_table(
        "case_candidate",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'MANUAL'"),
            comment="PRODUCTION/TRACE/LLM/MANUAL",
        ),
        sa.Column("raw_question", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True, comment="环境/客户/账期等标准槽位"),
        sa.Column("trace_uid", sa.String(length=36), nullable=True),
        sa.Column(
            "environment_id",
            sa.Integer(),
            sa.ForeignKey("environment.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("draft", sa.JSON(), nullable=True, comment="预填的用例草稿"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'NEW'"),
            comment="NEW/TRIAGED/PROMOTED/REJECTED",
        ),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column(
            "promoted_case_id",
            sa.Integer(),
            sa.ForeignKey("test_case.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamp_columns(),
    )
    op.alter_column("case_candidate", "source", server_default=None)
    op.alter_column("case_candidate", "status", server_default=None)
    op.create_index(
        "ix_case_candidate_status", "case_candidate", ["status", "created_at"]
    )
    op.create_index("ix_case_candidate_dedupe", "case_candidate", ["dedupe_hash"])

    # ---------- grader_eval ----------
    op.create_table(
        "grader_eval",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dataset_version", sa.String(length=32), nullable=True),
        sa.Column("grader_version", sa.String(length=32), nullable=False),
        sa.Column("mutant_kind", sa.String(length=32), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("detected", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("false_kills", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("run_at", sa.DateTime(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
    )
    op.alter_column("grader_eval", "total", server_default=None)
    op.alter_column("grader_eval", "detected", server_default=None)
    op.alter_column("grader_eval", "false_kills", server_default=None)
    op.create_index("ix_grader_eval_version", "grader_eval", ["grader_version", "run_at"])

    # ---------- caliber ----------
    op.create_table(
        "caliber",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("definition", sa.Text(), nullable=True),
        sa.Column(
            "canonical_source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'BUSINESS_UI'"),
            comment="缺省基准来源",
        ),
        sa.Column("owner", sa.String(length=64), nullable=True),
        *_timestamp_columns(),
    )
    op.alter_column("caliber", "canonical_source", server_default=None)
    op.create_index("ix_caliber_key", "caliber", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_caliber_key", table_name="caliber")
    op.drop_table("caliber")

    op.drop_index("ix_grader_eval_version", table_name="grader_eval")
    op.drop_table("grader_eval")

    op.drop_index("ix_case_candidate_dedupe", table_name="case_candidate")
    op.drop_index("ix_case_candidate_status", table_name="case_candidate")
    op.drop_table("case_candidate")

    op.drop_index("ix_fact_result_fact", table_name="fact_result")
    op.drop_index("ix_fact_result_execution", table_name="fact_result")
    op.drop_table("fact_result")

    op.drop_index("ix_gold_query_capability", table_name="gold_query")
    op.drop_index("ix_gold_query_case", table_name="gold_query")
    op.drop_table("gold_query")

    op.drop_index("ix_capability_status", table_name="capability")
    op.drop_index("ix_capability_key", table_name="capability")
    op.drop_table("capability")

    op.drop_index("ix_data_source_kind", table_name="data_source")
    op.drop_index("ix_data_source_code", table_name="data_source")
    op.drop_table("data_source")

    op.drop_column("turn_trace", "evidence")
    op.drop_column("turn_trace", "tool_calls")

    op.drop_column("execution_record", "as_of")
    op.drop_column("execution_record", "grader_version")
    op.drop_column("execution_record", "root_cause")
    op.drop_column("execution_record", "lucky_pass")
    op.drop_column("execution_record", "diagnostics")
    op.drop_column("execution_record", "fact_results")
    op.drop_column("execution_record", "answer_grading")
    op.drop_column("execution_record", "grading_mode")

    op.drop_index("ix_test_case_grading", table_name="test_case")
    op.drop_column("test_case", "dedupe_hash")
    op.drop_column("test_case", "dataset_version")
    op.drop_column("test_case", "reviewed_by")
    op.drop_column("test_case", "review_status")
    op.drop_column("test_case", "owner")
    op.drop_column("test_case", "risk_level")
    op.drop_column("test_case", "plan_spec")
    op.drop_column("test_case", "response_contract")
    op.drop_column("test_case", "answer_envelope")
    op.drop_column("test_case", "snapshot_id")
    op.drop_column("test_case", "as_of")
    op.drop_column("test_case", "input_type")
    op.drop_column("test_case", "grading_mode")
