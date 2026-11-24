"""全局配置：基于 pydantic-settings，从环境变量 / .env 读取。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# 后端根目录（billing_qa/core/config.py 向上两级），用于定位 .env，
# 避免从仓库根目录或其它工作目录启动时读不到 backend/.env。
_BACKEND_DIR = Path(__file__).resolve().parents[2]
# 先读 backend/.env，再用当前工作目录的 .env 覆盖；真实环境变量始终优先。
_ENV_FILES = (str(_BACKEND_DIR / ".env"), ".env")


def _with_driver(url: str, driver: str) -> str:
    """把 PostgreSQL 连接串统一成指定驱动的形式。"""
    target = f"postgresql+{driver}://"
    if url.startswith(target):
        return url
    if url.startswith("postgresql://"):
        return target + url[len("postgresql://") :]
    for current in ("asyncpg", "psycopg", "psycopg2"):
        prefix = f"postgresql+{current}://"
        if url.startswith(prefix):
            return target + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    """应用配置。所有字段均可通过同名（大小写不敏感）环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
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

    # ---------- PostgreSQL ----------
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "batch_test"
    postgres_maintenance_db: str = "postgres"
    # 连接池默认 20（峰值 20+40=60，仍低于 PG 默认 max_connections=100）：
    # 除页面请求外还有自动化平台经 API 调用、调度器 worker 的短会话借还。
    postgres_pool_size: int = 20
    postgres_pool_recycle: int = 1800
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
    # 统计接口（/stats/facts、/stats/overview）默认时间窗（天）：不传时间参数时
    # 只统计近 N 天，避免全库无界聚合随执行量线性变慢；显式传 since/until
    # 或 all_time=true 可覆盖（前端提供「全部」选项）。
    stats_default_window_days: int = 30
    schedule_poll_interval_seconds: float = 15.0
    # 任务运行锁 TTL：多实例下实例崩溃后锁自然过期，允许其他实例接管续做
    task_run_lock_ttl_seconds: float = 60.0

    # ---------- LLM JSON 输出修复重试 ----------
    # 模型返回 200 但内容不是约定的标准化 JSON（Markdown 代码块 / 噪声 / 缺字段）时，
    # 额外携带纠错反馈重试的次数；总调用次数 = 1 + llm_json_retry_attempts。
    llm_json_retry_attempts: int = 2
    # 每次重试前的基准退避秒数（指数递增并带随机抖动，上限 8s）
    llm_json_retry_backoff_seconds: float = 1.0

    # ---------- Mock ----------
    mock_llm_base_url: str = "http://127.0.0.1:8001/v1"
    mock_target_base_url: str = "http://127.0.0.1:8002"

    # ---------- 报表导出 ----------
    # Excel 报表中「执行号 / Trace UID」超链接的平台基址（前端站点根地址）。
    # 链接形如 {report_base_url}/trace?execution={id}；留空则不生成超链接。
    report_base_url: str = "http://localhost:5173"
    # 压线通过阈值：判分通过但总分低于该值的用例进入问题清单（风险类型=压线通过）。
    report_borderline_score: float = 70.0
    # 「近 N 次通过率(全库)」窗口：该用例在所有任务中的最近 N 次执行。
    report_near_history_count: int = 10

    # ---------- 派生属性 ----------
    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def async_database_url(self) -> str:
        """应用运行时使用的异步 URL（asyncpg）。"""
        if self.database_url:
            return _with_driver(self.database_url, "asyncpg")
        password = quote_plus(self.postgres_password)
        user = quote_plus(self.postgres_user)
        return (
            f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Alembic / 建库脚本使用的同步 URL（psycopg 3）。"""
        if self.database_url:
            return _with_driver(self.database_url, "psycopg")
        password = quote_plus(self.postgres_password)
        user = quote_plus(self.postgres_user)
        return (
            f"postgresql+psycopg://{user}:{password}@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
