"""把旧 MySQL 库中的业务数据迁移到 PostgreSQL。

前置条件：
1. 目标 PostgreSQL 库已创建，并已通过 ``alembic upgrade head`` 建好表结构；
2. 安装可选依赖：``pip install -e ".[mysql-import]"``。

用法（在 backend 目录下）：
    set MYSQL_SOURCE_URL=mysql+pymysql://root:root@127.0.0.1:3306/batch_test?charset=utf8mb4
    python scripts/migrate_mysql_to_postgres.py --truncate

脚本按外键依赖顺序复制数据、保留原主键，并重置 PostgreSQL 自增序列。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import Integer, create_engine, func, insert, select, text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

import billing_qa.models  # noqa: E402, F401  # 注册全部表结构
from billing_qa.core.config import settings  # noqa: E402
from billing_qa.db.base import Base  # noqa: E402


def _truncate_target(conn: Connection) -> None:
    """清空目标库业务表（保留 alembic_version）。"""
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


def _copy_table(source: Connection, target: Connection, table) -> int:
    rows = [dict(row._mapping) for row in source.execute(select(table))]
    if not rows:
        return 0
    target.execute(insert(table), rows)
    return len(rows)


def _reset_sequence(conn: Connection, table) -> None:
    """把 SERIAL 序列推进到当前最大主键之后。"""
    pk_columns = list(table.primary_key.columns)
    if len(pk_columns) != 1 or not isinstance(pk_columns[0].type, Integer):
        return
    column = pk_columns[0]
    max_id = conn.execute(select(func.max(column))).scalar() or 0
    conn.execute(
        text(
            "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
            ":next_value, false)"
        ),
        {
            "table_name": table.name,
            "column_name": column.name,
            "next_value": max_id + 1,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="MySQL -> PostgreSQL 数据迁移")
    parser.add_argument(
        "--source-url",
        default=os.getenv("MYSQL_SOURCE_URL"),
        help="源 MySQL 同步连接串，也可通过 MYSQL_SOURCE_URL 环境变量提供",
    )
    parser.add_argument(
        "--target-url",
        default=settings.sync_database_url,
        help="目标 PostgreSQL 同步连接串，默认取应用配置",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="导入前清空目标库业务表（适用于覆盖式迁移）",
    )
    args = parser.parse_args()

    if not args.source_url:
        parser.error("请通过 --source-url 或 MYSQL_SOURCE_URL 提供 MySQL 连接串")
    if not args.source_url.startswith("mysql"):
        parser.error("--source-url 必须是 MySQL 连接串，例如 mysql+pymysql://...")

    source_engine = create_engine(args.source_url, pool_pre_ping=True)
    target_engine = create_engine(args.target_url, pool_pre_ping=True)
    tables = list(Base.metadata.sorted_tables)

    if args.truncate:
        with target_engine.begin() as target:
            _truncate_target(target)

    total = 0
    with source_engine.connect() as source, target_engine.begin() as target:
        for table in tables:
            count = _copy_table(source, target, table)
            total += count
            print(f"{table.name}: {count}")
        for table in tables:
            _reset_sequence(target, table)

    print(f"迁移完成，共 {total} 行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
