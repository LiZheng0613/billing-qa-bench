"""数据库初始化：创建库、升级表结构、写入种子数据。

表结构只由 Alembic 维护，本脚本内部调用 ``billing_qa.db.migrations.upgrade_schema``，
不再使用 ``create_all`` 与 ``alembic stamp``，保证全项目只有一条建表链路。

用法（在 backend 目录下）：
    python -m billing_qa.db.init_db            # 建库 + 升级表结构 + 内置模板
    python -m billing_qa.db.init_db --demo     # 额外写入演示环境/模型/用例
    python -m billing_qa.db.init_db --demo --reset  # 清空全部用例后重建演示数据
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import psycopg
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger
from billing_qa.db.migrations import upgrade_schema
from billing_qa.db.seed import seed_builtin_prompts
from billing_qa.db.session import SessionLocal, engine

logger = get_logger(__name__)


def ensure_database() -> None:
    """连接维护库，创建目标数据库（若不存在）。"""
    target = make_url(settings.sync_database_url)
    target_db = target.database or settings.postgres_db
    maintenance = target.set(
        drivername="postgresql", database=settings.postgres_maintenance_db
    )
    try:
        with psycopg.connect(
            maintenance.render_as_string(hide_password=False), autocommit=True
        ) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            if cur.fetchone() is None:
                cur.execute(
                    sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(
                        sql.Identifier(target_db)
                    )
                )
                logger.info("数据库已创建: %s", target_db)
            else:
                logger.info("数据库已存在: %s", target_db)
    except psycopg.errors.DuplicateDatabase:
        logger.info("数据库已存在: %s", target_db)
    except psycopg.errors.InsufficientPrivilege as exc:
        raise RuntimeError(
            f"当前 PostgreSQL 账号无权创建数据库，请先手动创建 {target_db!r}，"
            "或改用具备 CREATEDB 权限的账号。"
        ) from exc
    except psycopg.OperationalError as exc:
        raise RuntimeError(
            f"无法连接 PostgreSQL {target.host or settings.postgres_host}:"
            f"{target.port or settings.postgres_port}，请确认服务已启动、账号密码正确。"
        ) from exc


async def upgrade_database() -> None:
    """统一走 Alembic 升级表结构（空库会按迁移脚本从零建表）。"""
    import billing_qa.models  # noqa: F401  # 注册全部表结构
    from billing_qa.db.base import Base

    await asyncio.to_thread(upgrade_schema)
    logger.info("数据表已就绪，共 %d 张", len(Base.metadata.tables))


async def init_database(with_demo: bool = False, reset: bool = False) -> None:
    ensure_database()
    await upgrade_database()
    async with SessionLocal() as db:
        created = await seed_builtin_prompts(db)
        if created:
            logger.info("已写入内置 Prompt 模板: %s", ", ".join(created))
        else:
            logger.info("内置 Prompt 模板已存在，跳过")
        if with_demo:
            from billing_qa.db.seed_demo import seed_demo_data

            await seed_demo_data(db, reset=reset)
            logger.info("演示数据已就绪%s", "（已先清空全部用例）" if reset else "")


async def _main() -> None:
    parser = argparse.ArgumentParser(description="初始化批量测试平台数据库")
    parser.add_argument("--demo", action="store_true", help="同时写入演示环境/模型/用例")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清空全部用例、执行记录与候选池，再重建演示数据（需配合 --demo）",
    )
    args = parser.parse_args()
    await init_database(with_demo=args.demo, reset=args.reset)
    # 校验连接
    async with SessionLocal() as db:
        value = (await db.execute(text("SELECT 1"))).scalar_one()
        logger.info("连通性校验通过: SELECT 1 -> %s", value)
    await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
