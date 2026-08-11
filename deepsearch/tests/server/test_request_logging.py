import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.core.request_logging import add_request_logging_middleware


def test_request_logging_middleware_logs_endpoint_and_status(caplog):
    """验证请求日志中间件记录接口、状态码和耗时。

    Args:
        caplog: pytest 日志捕获工具。

    Returns:
        None.
    """
    app = FastAPI()
    add_request_logging_middleware(app)

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    caplog.set_level(logging.INFO, logger="server.core.request_logging")

    response = TestClient(app).get("/probe")

    assert response.status_code == 200
    assert any(
        "HTTP request completed" in record.message
        and "method=GET" in record.message
        and "path=/probe" in record.message
        and "status_code=200" in record.message
        and "duration_ms=" in record.message
        for record in caplog.records
    )
