"""API 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from billing_qa.api.v1 import auth, cases, environments, executions, models, prompts, tasks, ws

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(environments.router)
api_router.include_router(models.router)
api_router.include_router(prompts.router)
api_router.include_router(cases.router)
api_router.include_router(tasks.router)
api_router.include_router(executions.router)
api_router.include_router(ws.router)
