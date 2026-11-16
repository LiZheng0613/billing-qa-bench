"""数据源与数据能力（取数资产，供后台事实认证使用）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from billing_qa.db.base import Base, TimestampMixin


class DataSource(Base, TimestampMixin):
    """取数通道：Cypher 执行器工具 / 只读 SQL / 业务系统查询接口。"""

    __tablename__ = "data_source"
    __table_args__ = (
        Index("ix_data_source_code", "code", unique=True),
        Index("ix_data_source_kind", "kind", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="数据源编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="CYPHER_TOOL", comment="CYPHER_TOOL/SQL_DB/HTTP_API"
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(512), comment="执行器或接口地址")
    description: Mapped[str | None] = mapped_column(Text)
    auth_config: Mapped[dict | None] = mapped_column(JSON, comment="鉴权配置（加密值由应用层解密）")
    timeout_seconds: Mapped[float] = mapped_column(Float, default=10.0, comment="单次取数超时")
    max_rows: Mapped[int] = mapped_column(Integer, default=200, comment="单次返回行数上限")
    tool_version: Mapped[str | None] = mapped_column(String(32), comment="执行器版本")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    last_probe_status: Mapped[str | None] = mapped_column(String(16))
    last_probe_latency_ms: Mapped[float | None] = mapped_column(Float)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_probe_message: Mapped[str | None] = mapped_column(Text)


class Capability(Base, TimestampMixin):
    """参数化的取数能力模板，对录用例的人员完全不可见。"""

    __tablename__ = "capability"
    __table_args__ = (
        Index("ix_capability_key", "key", unique=True),
        Index("ix_capability_status", "status", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, comment="能力键，如 bill_current")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="业务名称")
    kind: Mapped[str] = mapped_column(String(16), default="CYPHER", comment="CYPHER/SQL/API")
    description: Mapped[str | None] = mapped_column(Text, comment="业务语义描述，供路由分类")
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_source.id", ondelete="SET NULL"), nullable=True
    )
    query_text: Mapped[str | None] = mapped_column(Text, comment="参数化查询模板，$param 占位")
    params_schema: Mapped[list | None] = mapped_column(
        JSON, comment='[{"name":"group_id","label":"集团客户","type":"entity","required":true}]'
    )
    param_binding: Mapped[dict | None] = mapped_column(
        JSON, comment="context 槽位 → 查询参数 的机械映射"
    )
    intent_patterns: Mapped[list | None] = mapped_column(
        JSON, comment='["套餐","订购"]，用于 L1 意图路由'
    )
    lookup_queries: Mapped[dict | None] = mapped_column(
        JSON, comment="实体下拉数据来源，如 {'entity:group_id': 'MATCH ...'}"
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(16), default="DRAFT", comment="DRAFT/CERTIFIED/DEPRECATED"
    )
    cert_result: Mapped[dict | None] = mapped_column(JSON, comment="认证报告")
    certified_at: Mapped[datetime | None] = mapped_column(DateTime)
    certified_by: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
