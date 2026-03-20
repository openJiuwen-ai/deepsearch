# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from fastapi import FastAPI, APIRouter

from server.routers import deepsearch_run, report, report_template, web_search_engine_router, knowledge_base

api_router = APIRouter()


def router_register(app: FastAPI):
    """Register API routers to FastAPI app."""

    # Add health check endpoint directly to api_router (not v1_router)
    @api_router.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "service": "OpenJiuwen DeepSearch Server",
            "version": "1.0.0"
        }

    app.include_router(api_router, prefix="/api")

    deepsearch_router = register_deepsearch_router()
    app.include_router(deepsearch_router)
    knowledge_base_router = register_knowledge_base_router()
    app.include_router(knowledge_base_router)

    @app.get("/")
    async def root():
        return {
            "message": "Welcome to OpenJiuwen DeepSearch Server",
            "docs": "/api/docs",
            "health": "/api/health"
        }


def register_deepsearch_router():
    """Register sub routers to deepsearch routers."""
    deepsearch_router = APIRouter(prefix="/api/v1/agent/deepsearch")
    deepsearch_router.include_router(deepsearch_run.run_router, prefix="/run", tags=["Run"])
    deepsearch_router.include_router(web_search_engine_router.router, prefix="/web_search", tags=["Web Search Engine"])
    deepsearch_router.include_router(report.reports_router, prefix="/reports", tags=["Reports"])
    deepsearch_router.include_router(report_template.router, prefix="/template", tags=["Report Template"])
    return deepsearch_router


def register_knowledge_base_router():
    """Register knowledge base router."""
    knowledge_base_router = APIRouter(prefix="/api/kb")
    knowledge_base_router.include_router(knowledge_base.knowledge_base_router, tags=["Knowledge Base"])
    return knowledge_base_router