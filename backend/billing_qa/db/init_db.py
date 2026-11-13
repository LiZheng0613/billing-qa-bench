"""数据库初始化：创建库、建表、写入种子数据。

用法（在 backend 目录下）：
    python -m billing_qa.db.init_db            # 建库 + 建表 + 内置模板
    python -m billing_qa.db.init_db --demo     # 额外写入演示环境/模型/用例
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger
from billing_qa.db.base import Base
from billing_qa.db.seed import seed_builtin_prompts
from billing_qa.db.session import SessionLocal, engine

logger = get_logger(__name__)


def ensure_database() -> None:
    """使用同步驱动创建数据库（若不存在）。"""
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_db}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        logger.info("数据库已就绪: %s", settings.mysql_db)
    finally:
        conn.close()


async def create_tables() -> None:
    # 导入模型以注册 metadata
    import billing_qa.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据表已就绪，共 %d 张", len(Base.metadata.tables))
    _stamp_alembic_head()


def _stamp_alembic_head() -> None:
    """把 create_all 建出的结构标记为最新迁移版本，保证 Alembic 状态一致。"""
    try:
        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        command.stamp(config, "head")
        logger.info("Alembic 版本已标记为 head")
    except Exception as exc:
        logger.warning("Alembic stamp 跳过: %s", exc)


async def init_database(with_demo: bool = False) -> None:
    ensure_database()
    await create_tables()
    async with SessionLocal() as db:
        created = await seed_builtin_prompts(db)
        if created:
            logger.info("已写入内置 Prompt 模板: %s", ", ".join(created))
        else:
            logger.info("内置 Prompt 模板已存在，跳过")
        if with_demo:
            from billing_qa.db.seed_demo import seed_demo_data

            await seed_demo_data(db)
            logger.info("演示数据已就绪")


async def _main() -> None:
    parser = argparse.ArgumentParser(description="初始化批量测试平台数据库")
    parser.add_argument("--demo", action="store_true", help="同时写入演示环境/模型/用例")
    args = parser.parse_args()
    await init_database(with_demo=args.demo)
    # 校验连接
    async with SessionLocal() as db:
        value = (await db.execute(text("SELECT 1"))).scalar_one()
        logger.info("连通性校验通过: SELECT 1 -> %s", value)
    await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
