# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
import time

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def _get_route_path(request: Request) -> str:
    """获取 FastAPI 匹配到的路由模板。

    Args:
        request: 当前 HTTP 请求对象。

    Returns:
        str: 路由模板；无法获取时返回实际请求路径。
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def add_request_logging_middleware(app: FastAPI) -> None:
    """为 FastAPI 应用注册统一 HTTP 请求日志中间件。

    仅记录接口路径、方法、状态码、客户端地址与耗时，避免读取请求体或输出
    query/body 中可能包含的密钥、模板、报告正文等敏感或大字段。

    Args:
        app: 需要注册中间件的 FastAPI 应用。

    Returns:
        None.
    """
    if getattr(app.state, "request_logging_middleware_enabled", False):
        return
    app.state.request_logging_middleware_enabled = True

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        start_time = time.perf_counter()
        client_host = request.client.host if request.client else ""
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.exception(
                "HTTP request failed method=%s path=%s route=%s client=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                _get_route_path(request),
                client_host,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "HTTP request completed method=%s path=%s route=%s status_code=%s client=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            _get_route_path(request),
            response.status_code,
            client_host,
            duration_ms,
        )
        return response
