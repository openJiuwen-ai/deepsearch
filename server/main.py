# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import io
import os
import sys
from contextlib import asynccontextmanager

from server.deepsearch.core.models.report_template import ReportTemplateDB
from server.deepsearch.core.models.web_search_engine_model import WebSearchEngineModel

# 添加项目根目录到 Python 路径，以便直接运行时能找到所有模块
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables from project root (上级目录)
project_root = backend_dir
load_dotenv(os.path.join(project_root, '.env'))

from server.routers import register
from server.core.database import engine, Base
from server.core.db_sync import run_database_sync

from openjiuwen.core.common.logging import logger

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


@asynccontextmanager
async def lifespan_func(input_app: FastAPI):
    # Startup
    logger.info("🚀 Starting openJiuwen-DeepSearch Server...")

    target_tables = [
        # Deepsearch table
        WebSearchEngineModel.__table__,
        ReportTemplateDB.__table__,
    ]

    if engine.url.drivername == "sqlite":
        renamed_count = 0
        for table in target_tables:
            # Skip if table has no index attribute
            if not hasattr(table, "indexes"):
                logger.warning(f"Table {table.name} has no indexes attribute, skipping...")
                continue
                # Iterate all indexes of the table
            for idx in table.indexes:
                old_idx_name = idx.name
                idx.name = f"{old_idx_name}_{table.name}"
                logger.info(f"{table.name}: Renamed index: {old_idx_name} ---> {idx.name}")
                renamed_count += 1
        logger.info(f"Duplicate index renaming completed. Total renamed indexes: {renamed_count}")

    # Create database tables with checkfirst=True to avoid creating existing indexes
    Base.metadata.create_all(
        bind=engine,
        tables=target_tables,
        checkfirst=True
    )

    # 运行数据库字段同步（添加新字段）
    run_database_sync()
    logger.info("✅ Database field sync completed")

    yield

    # Shutdown
    logger.info("🛑 Shutting down openJiuwen-DeepSearch Server...")


# Create FastAPI app
app = FastAPI(
    title="openJiuwen-DeepSearch API",
    description="Backend API for openJiuwen-DeepSearch",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan_func,
    # 添加Swagger UI的OAuth2配置
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": True,
        "appName": "openJiuwen-DeepSearch API",
        "clientId": "swagger-ui",
    }
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3069", "http://127.0.0.1:3069"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "message": "Welcome to openJiuwen-DeepSearch Server",
        "docs": "/api/docs",
        "health": "/api/health"
    }


register.router_register(app)


def main():
    # Development configuration
    config = {
        "host": os.getenv("HOST", "0.0.0.0"),
        "port": int(os.getenv("BACKEND_PORT", "8000")),
        "reload": False,
        "log_level": "info",
        "access_log": True,
        "workers": int(os.getenv("WORKER_NUM", 1)),
    }

    logger.info("🚀 Starting openJiuwen-DeepSearch Server in development mode...")
    logger.info(f"📍 Server will be available at: http://{config['host']}:{config['port']}")
    logger.info(f"📚 API Documentation: http://{config['host']}:{config['port']}/api/docs")
    logger.info(f"🔍 Health Check: http://{config['host']}:{config['port']}/api/health")
    logger.info("🔄 Auto-reload enabled for development")
    logger.info("⏹️  Press Ctrl+C to stop the server")
    logger.info("-" * 60)

    # Start the server；force asyncio loop to avoid uvloop + nest_asyncio conflict
    uvicorn.run(
        "server.main:app",
        loop="asyncio",
        **config
    )


if __name__ == "__main__":
    main()
