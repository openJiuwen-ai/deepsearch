# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""FastAPI 服务入口。

启动方式（三种等价）：
  * whl / 镜像：`codesearch-server`（console script，不依赖源码树）
  * 源码：`python start_backend.py`
  * 自定义 uvicorn 参数：`uvicorn openjiuwen_codesearch.server.main:app`

服务层位于包内而非仓库顶层，是为了让 wheel 也能起服务——顶层 `server/`
不会被打包，且顶层模块名 `server` 过于通用，与同环境的其他产品有冲突风险。
"""

import logging

from fastapi import FastAPI

from openjiuwen_codesearch.server.core.config import settings
from openjiuwen_codesearch.server.routers.api import api_router, shutdown_retrievers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="openJiuwen-CodeSearch",
        description="Agentic code retrieval service",
        docs_url="/docs",
    )
    app.include_router(api_router, prefix="/api")

    @app.on_event("shutdown")
    async def _close_retrievers() -> None:  # 释放跨请求复用的 Milvus 连接别名
        await shutdown_retrievers()

    return app


app = create_app()


def _warn_if_index_roots_unset() -> None:
    """未配置白名单时索引接口会 403：启动期明确打出，避免被当成服务故障。"""
    if settings.allowed_index_roots():
        return
    logger.warning(
        "CODESEARCH_INDEX_ROOTS is empty: POST /api/v1/index is disabled "
        "(returns 403). Set it to a path whitelist to enable indexing. "
        "This service has no authentication — deploy on a trusted network "
        "or behind an access-controlled gateway."
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    _warn_if_index_roots_unset()
    logger.info("Health check: http://%s:%d/api/health", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
