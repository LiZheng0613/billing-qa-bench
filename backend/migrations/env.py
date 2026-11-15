"""Alembic 迁移环境：复用应用配置与 ORM 元数据。

本文件是 `alembic` CLI 的配置入口；程序内调用统一走
`billing_qa.db.migrations.upgrade_schema()`，两条路径最终都执行同一套
`migrations/versions` 脚本与同一个 URL 来源，不存在旁路建表。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证 alembic 命令在任意目录执行时都能导入 billing_qa
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import billing_qa.models  # noqa: F401  # 注册全部表结构
from billing_qa.core.config import settings
from billing_qa.db.base import Base

config = context.config
# 命令行执行时按 alembic.ini 配置日志；程序内调用（init_db / 部署脚本）沿用应用日志。
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 数据库 URL 的唯一来源：应用配置（同步 psycopg 驱动）
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
