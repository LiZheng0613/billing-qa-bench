"""Alembic 数据库迁移的唯一入口。

所有建表与升级动作（`alembic` CLI、`python -m billing_qa.db.init_db`、部署脚本）
都通过本模块调用 Alembic；业务代码与测试不得再直接使用
``Base.metadata.create_all`` 或 ``alembic stamp``，避免出现第二套建表链路。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from billing_qa.core.config import settings
from billing_qa.core.logging import get_logger

logger = get_logger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    """构造 Alembic 配置：脚本目录固定为 backend/migrations，URL 取自应用配置。"""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.sync_database_url)
    # 程序内调用不覆写应用日志配置，env.py 会读取该标记
    config.attributes["skip_logging_config"] = True
    return config


def upgrade_schema(revision: str = "head") -> None:
    """升级数据库结构到指定版本，空库会按迁移脚本从零建表。"""
    command.upgrade(alembic_config(), revision)
    logger.info("数据库结构已升级到 %s", revision)
