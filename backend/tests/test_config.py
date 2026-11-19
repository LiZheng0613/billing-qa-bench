"""数据库配置单元测试（不依赖真实 PostgreSQL）。"""

from __future__ import annotations

from pathlib import Path

from billing_qa.core.config import Settings


def test_env_file_falls_back_to_backend_directory() -> None:
    """从任意工作目录启动都应能读到 backend/.env。"""
    env_files = Settings.model_config["env_file"]
    assert isinstance(env_files, tuple)
    backend_env = Path(env_files[0])
    assert backend_env.name == ".env"
    assert backend_env.parent.name == "backend"


def test_default_urls_use_postgresql_drivers() -> None:
    settings = Settings(
        postgres_host="127.0.0.1",
        postgres_port=5432,
        postgres_user="u",
        postgres_password="p",
        postgres_db="db",
        database_url=None,
    )
    assert settings.async_database_url == "postgresql+asyncpg://u:p@127.0.0.1:5432/db"
    assert settings.sync_database_url == "postgresql+psycopg://u:p@127.0.0.1:5432/db"


def test_database_url_override_normalizes_driver() -> None:
    settings = Settings(database_url="postgresql://u:p@db.example:5432/qa")
    assert settings.async_database_url == "postgresql+asyncpg://u:p@db.example:5432/qa"
    assert settings.sync_database_url == "postgresql+psycopg://u:p@db.example:5432/qa"


def test_password_is_url_encoded() -> None:
    settings = Settings(
        postgres_user="user",
        postgres_password="p@ss/word",
        postgres_db="batch_test",
        database_url=None,
    )
    assert settings.async_database_url.startswith(
        "postgresql+asyncpg://user:p%40ss%2Fword@"
    )
