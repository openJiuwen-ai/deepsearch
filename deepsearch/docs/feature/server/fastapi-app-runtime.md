# Server 应用运行时

## 维护范围

本文档覆盖主 FastAPI 应用、路由挂载、应用生命周期、数据库初始化、Runner 初始化、请求日志和跨进程取消监听。

不覆盖具体业务路由的请求/响应细节；见对应 server feature 文档。

## 功能目的

Server 运行时把后端 API、数据库、openJiuwen Runner、Checkpointer、取消总线和 CORS/日志中间件组合成可启动的 FastAPI 服务，为前端和外部调用方提供统一 HTTP 入口。

## 可见行为

- `server/main.py` 中的 FastAPI app 暴露 `/api/docs`、`/api/redoc` 和 `/api/openapi.json`。
- `/api/health` 返回服务健康状态，根路径返回欢迎信息和文档入口。
- 应用启动时初始化 Runner、启动 Redis 取消监听、创建业务表并执行数据库字段同步。
- 应用关闭时先停止取消监听，再关闭 Runner。
- HTTP 请求日志只记录方法、路径、路由模板、状态码、客户端地址和耗时，不读取 body 或 query。
- CORS 当前允许本地前端端口 `3000` 和 `3069`。

## 关键代码路径

- `server/main.py`
- `server/routers/register.py`
- `server/core/config.py`
- `server/core/database.py`
- `server/core/runner_init.py`
- `server/core/cancel_bus.py`
- `server/core/request_logging.py`
- `server/core/db_sync.py`
- `tests/server/test_request_logging.py`

## 核心流程

1. 启动时从项目根目录 `.env` 加载 server 配置。
2. `Settings` 校验数据库和 checkpointer 组合；Redis checkpointer 必须配合 MySQL。
3. `lifespan_func` 调用 `init_runner`，按 `CHECKPOINTER_TYPE` 配置 in-memory、persistence 或 redis checkpointer。
4. Redis checkpointer 模式下启动取消总线监听。
5. SQLAlchemy 创建业务表，SQLite 模式下会先重命名重复索引名。
6. `run_database_sync` 为部分模型补新增字段。
7. `router_register` 挂载 DeepSearch 和知识库路由。
8. 关闭阶段停止取消监听并释放 Runner。

## 数据契约与依赖

- `DB_TYPE` 支持 `mysql` 和 `sqlite`。
- SQLite 默认写入 `data/databases/agent.db`；MySQL 使用 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD` 和 `DEEPSEARCH_DB_NAME`。
- `CHECKPOINTER_TYPE` 支持 `in_memory`、`persistence`、`redis`。
- Redis checkpointer 使用 `REDIS_URL`、`REDIS_CLUSTER_MODE`、`REDIS_TTL` 和 `REDIS_REFRESH_ON_READ`。
- `uvicorn` 启动配置读取 `HOST`、`BACKEND_PORT` 和 `WORKER_NUM`。

## 边界与错误处理

- 不支持的 `DB_TYPE` 会在构建数据库 URL 时抛异常。
- 不支持的 `CHECKPOINTER_TYPE` 会阻止 Runner 初始化。
- `CHECKPOINTER_TYPE=redis` 且 `DB_TYPE` 不是 MySQL 时，`Settings` 校验失败。
- Redis 取消总线仅在 redis checkpointer 模式启用；非 redis 模式下发布取消会返回 `False`。
- 请求日志中间件捕获异常后记录失败日志并重新抛出，不吞掉业务异常。

## 测试与验证

- `uv run pytest tests/server/test_request_logging.py`
- 修改 Runner 或 checkpointer 初始化时，补充运行 `uv run pytest tests/server/test_deepsearch_run.py`。
- 修改数据库配置或模型同步时，应至少运行 `PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q server`。

## 相关文档

- [DeepSearch 运行与 SSE 流](./deepsearch-run-streaming.md)
- [Server 持久化与存储](./persistence-and-storage.md)
- [错误码、异常与公共常量](../common/error-and-common-contracts.md)
