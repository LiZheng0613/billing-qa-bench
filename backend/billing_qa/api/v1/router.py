"""API 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from billing_qa.api.v1 import (
    auth,
    calibers,
    candidates,
    capabilities,
    cases,
    environments,
    executions,
    grader,
    models,
    prompts,
    stats,
    tasks,
    ws,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(environments.router)
api_router.include_router(models.router)
api_router.include_router(prompts.router)
api_router.include_router(cases.router)
api_router.include_router(capabilities.router)
api_router.include_router(candidates.router)
api_router.include_router(calibers.router)
api_router.include_router(grader.router)
api_router.include_router(stats.router)
api_router.include_router(tasks.router)
api_router.include_router(executions.router)
api_router.include_router(ws.router)
