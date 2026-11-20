"""normalize request_variables numeric values to strings

请求变量里的数字（手机号、订单号、金额等）历史上可能被写成 JSON number，
超过 JS 安全整数或带小数时会丢精度、变科学计数法。本迁移把当前用例、
版本快照与执行快照中的数字统一转成定点字符串，保证后续编辑/回滚不再写回 number。

Revision ID: a1c4e7b9d2f5
Revises: d1e4f7a9b2c6
Create Date: 2026-11-20 16:10:00.000000
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7b9d2f5"
down_revision: str | None = "d1e4f7a9b2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _number_to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return str(value)
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _number_to_text(value)
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value


def _normalize_variables(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {str(key): _normalize_value(item) for key, item in value.items()}


def upgrade() -> None:
    bind = op.get_bind()

    case_table = sa.table(
        "test_case",
        sa.column("id", sa.Integer),
        sa.column("request_variables", sa.JSON),
    )
    rows = bind.execute(
        sa.select(case_table.c.id, case_table.c.request_variables).where(
            case_table.c.request_variables.isnot(None)
        )
    ).fetchall()
    for case_id, current in rows:
        normalized = _normalize_variables(current)
        if normalized != current:
            bind.execute(
                case_table.update()
                .where(case_table.c.id == case_id)
                .values(request_variables=normalized)
            )

    revision_table = sa.table(
        "test_case_revision",
        sa.column("id", sa.Integer),
        sa.column("snapshot", sa.JSON),
    )
    rows = bind.execute(
        sa.select(revision_table.c.id, revision_table.c.snapshot).where(
            revision_table.c.snapshot.isnot(None)
        )
    ).fetchall()
    for revision_id, snapshot in rows:
        if not isinstance(snapshot, dict) or "request_variables" not in snapshot:
            continue
        normalized = {
            **snapshot,
            "request_variables": _normalize_variables(snapshot.get("request_variables")),
        }
        if normalized != snapshot:
            bind.execute(
                revision_table.update()
                .where(revision_table.c.id == revision_id)
                .values(snapshot=normalized)
            )

    execution_table = sa.table(
        "execution_record",
        sa.column("id", sa.Integer),
        sa.column("case_snapshot", sa.JSON),
    )
    rows = bind.execute(
        sa.select(execution_table.c.id, execution_table.c.case_snapshot).where(
            execution_table.c.case_snapshot.isnot(None)
        )
    ).fetchall()
    for record_id, snapshot in rows:
        if not isinstance(snapshot, dict) or "request_variables" not in snapshot:
            continue
        normalized = {
            **snapshot,
            "request_variables": _normalize_variables(snapshot.get("request_variables")),
        }
        if normalized != snapshot:
            bind.execute(
                execution_table.update()
                .where(execution_table.c.id == record_id)
                .values(case_snapshot=normalized)
            )


def downgrade() -> None:
    # 无损升级：字符串无法可靠还原成原来的 number，且数字形态本身有精度风险。
    pass
