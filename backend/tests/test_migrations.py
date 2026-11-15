"""迁移链路约束：全项目只有 Alembic 一条建表路径。"""

from __future__ import annotations

from pathlib import Path

from billing_qa.core.config import settings
from billing_qa.db.migrations import BACKEND_ROOT, alembic_config


def test_alembic_config_has_single_source() -> None:
    config = alembic_config()
    assert Path(config.get_main_option("script_location")) == BACKEND_ROOT / "migrations"
    assert config.get_main_option("sqlalchemy.url") == settings.sync_database_url


def test_init_db_uses_alembic_only() -> None:
    """防止回退到 create_all + stamp 的第二条建表链路。"""
    source = (BACKEND_ROOT / "billing_qa" / "db" / "init_db.py").read_text(encoding="utf-8")
    assert "upgrade_schema" in source
    assert "metadata.create_all" not in source
    assert "command.stamp" not in source
