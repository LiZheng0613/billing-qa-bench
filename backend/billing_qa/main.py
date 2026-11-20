"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from billing_qa.api.v1.router import api_router
from billing_qa.core.config import settings
from billing_qa.core.control_store import control_store
from billing_qa.core.exceptions import AppError
from billing_qa.core.logging import get_logger, setup_logging
from billing_qa.db.session import dispose_engine
from billing_qa.services.engine.schedule import schedule_dispatcher
from billing_qa.services.engine.scheduler import scheduler

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("%s 启动中 (env=%s)", settings.app_name, settings.app_env)
    await control_store.connect()
    recovered = await scheduler.recover_interrupted_tasks()
    if recovered:
        logger.warning(
            "检测到 %d 个中断任务：执行中的已自动续做，终止中的已收口为已终止",
            recovered,
        )
    await schedule_dispatcher.start()
    logger.info("服务已就绪: http://%s:%s%s/docs", settings.host, settings.port, settings.api_prefix)
    try:
        yield
    finally:
        await schedule_dispatcher.stop()
        await scheduler.shutdown()
        await control_store.close()
        await dispose_engine()
        logger.info("服务已关闭")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="问账智能客服批量测试平台：批量回归、多轮对话模拟、白盒链路追踪与混合裁决。",
    lifespan=lifespan,
    docs_url=f"{settings.api_prefix}/docs",
    redoc_url=f"{settings.api_prefix}/redoc",
    openapi_url=f"{settings.api_prefix}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "path": request.url.path},
    )


def _json_safe(value: Any) -> Any:
    """把校验错误里的异常对象转成字符串，避免 JSONResponse 序列化失败。

    pydantic v2 的 ``ctx`` 会携带原始 ``ValueError`` 实例，直接放进响应会触发
    ``TypeError: Object of type ValueError is not JSON serializable``，最终被
    兜底处理器改写成 500，掩盖真正的 422 语义。
    """
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "参数校验失败",
            "detail": _json_safe(exc.errors()),
            "path": request.url.path,
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"},
    )


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/docs", include_in_schema=False)
async def docs_redirect() -> RedirectResponse:
    """兼容旧链接：Swagger 文档已迁移到 /api/v1/docs。"""
    return RedirectResponse(url=f"{settings.api_prefix}/docs")


@app.get("/redoc", include_in_schema=False)
async def redoc_redirect() -> RedirectResponse:
    """兼容旧链接：ReDoc 文档已迁移到 /api/v1/redoc。"""
    return RedirectResponse(url=f"{settings.api_prefix}/redoc")


@app.get("/health", tags=["系统"], summary="健康检查")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "redis": "up" if control_store.using_redis else "fallback",
        "logger": logging.getLevelName(logger.level),
    }
