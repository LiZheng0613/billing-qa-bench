"""全局配置：基于 pydantic-settings，从环境变量 / .env 读取。"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。所有字段均可通过同名（大小写不敏感）环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 应用 ----------
    app_name: str = "问账智能客服批量测试平台"
    app_env: str = "dev"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------- MySQL ----------
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "root"
    mysql_db: str = "batch_test"
    mysql_pool_size: int = 10
    mysql_pool_recycle: int = 1800
    database_url: str | None = None

    # ---------- Redis ----------
    redis_url: str = "redis://127.0.0.1:6379/1"
    redis_key_prefix: str = "batchtest:"

    # ---------- 安全 ----------
    secret_key: str = "change-me-to-a-long-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # ---------- 引擎 ----------
    default_concurrency: int = 3
    default_qps_limit: float = 5.0
    pass_threshold: float = 60.0
    http_timeout_seconds: float = 60.0
    max_turns_hard_limit: int = 20
    repetition_similarity_threshold: float = 0.9
    task_retention_days: int = 30

    # ---------- Mock ----------
    mock_llm_base_url: str = "http://127.0.0.1:8001/v1"
    mock_target_base_url: str = "http://127.0.0.1:8002"

    # ---------- 派生属性 ----------
    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def async_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        password = quote_plus(self.mysql_password)
        user = quote_plus(self.mysql_user)
        return (
            f"mysql+aiomysql://{user}:{password}@{self.mysql_host}:{self.mysql_port}"
            f"/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        """Alembic / 建库脚本使用的同步 URL。"""
        if self.database_url:
            return self.database_url.replace("+aiomysql", "+pymysql")
        password = quote_plus(self.mysql_password)
        user = quote_plus(self.mysql_user)
        return (
            f"mysql+pymysql://{user}:{password}@{self.mysql_host}:{self.mysql_port}"
            f"/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def server_database_url(self) -> str:
        """不带库名的同步 URL，用于 CREATE DATABASE。"""
        return self.sync_database_url.rsplit("/", 1)[0] + "/"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
