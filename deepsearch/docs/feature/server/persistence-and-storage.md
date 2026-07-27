# Server 持久化与存储

## 维护范围

本文档覆盖 server 层数据库配置、SQLAlchemy session、业务模型、数据库字段同步、repository 约定、API key 加密以及知识库对象存储约束。

不覆盖各业务接口的完整流程。

## 功能目的

持久化与存储层为 server API 提供多租户业务数据、配置记录、知识库元数据和文件共享能力，同时在启动时尽量自动补齐新增数据库字段，降低本地开发和轻量部署的迁移成本。

## 可见行为

- Server 支持 MySQL 和 SQLite 两种业务数据库。
- FastAPI 请求通过 `get_db` 获取 SQLAlchemy session，请求结束后关闭。
- 启动时会创建报告模板、联网搜索引擎、知识库和知识库文档表。
- `run_database_sync` 会对部分模型补新增字段，并尝试处理字段类型差异。
- Web search API key 入库前加密，运行读取时解密。
- Redis checkpointer 多实例模式要求共享 MySQL，并要求知识库文件通过 OBS 共享。

## 关键代码路径

- `server/core/config.py`
- `server/core/database.py`
- `server/core/db_sync.py`
- `server/core/kb_obs_requirement.py`
- `server/core/manager/model_manager/utils/security_utils.py`
- `server/deepsearch/core/models/report_template.py`
- `server/deepsearch/core/models/web_search_engine_model.py`
- `server/local_retrieval/models/knowledge_base.py`
- `server/local_retrieval/models/knowledge_base_document.py`
- `server/deepsearch/core/manager/repositories/report_template_repository.py`
- `server/deepsearch/core/manager/repositories/web_search_engine_repository.py`
- `server/local_retrieval/core/manager/repositories/knowledge_base_repository.py`

## 核心流程

1. `Settings` 从 `.env` 读取数据库、checkpointer 和 Redis 配置。
2. `get_database_url` 根据 `DB_TYPE` 构造 SQLAlchemy URL。
3. `engine` 配置 SQLite 线程参数或 MySQL 连接池 pre-ping/recycle。
4. 应用启动时 `Base.metadata.create_all` 创建目标业务表。
5. `DatabaseSync` 检查模型列和数据库列，补缺失列并尝试同步类型。
6. repository 负责按 `space_id` 限定查询、提交、回滚和异常包装。
7. 多实例模式下知识库文件通过 OBS 客户端上传/下载/删除。

## 数据契约与依赖

- `report_template` 使用 `space_id + template_name` 唯一约束。
- `web_search_engine` 使用自增 `web_search_engine_id`。
- `knowledge_base` 使用全局唯一 `kb_id`。
- `knowledge_base_document` 使用全局唯一 `doc_id`，并通过 `space_id + kb_id` 关联知识库。
- `ResponseModel` 是知识库 repository 和 router 的通用响应壳，包含 `code`、`message` 和可选 `data`。

## 边界与错误处理

- SQLite 不支持直接修改列类型，字段同步会记录 warning 并跳过。
- 字段同步单个模型失败会继续同步其他模型；顶层失败会抛出。
- Redis checkpointer 与 SQLite 组合会在配置校验阶段失败，避免多实例业务数据不共享。
- repository 创建临时 session 时会在异常后 rollback 并关闭 session。
- OBS secret 通过 `SecurityUtils.get_decrypted_secret` 获取；不要在日志或文档复制明文。

## 测试与验证

- `PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q server`
- 修改 repository 或数据库模型时，补充运行相关 server/router 测试。
- 修改 web search engine 持久化时，运行 `uv run pytest tests/server/test_web_search_engine_schema.py`。

## 相关文档

- [Server 应用运行时](./fastapi-app-runtime.md)
- [知识库管理](./knowledge-base.md)
- [模板与联网搜索引擎管理](./template-and-web-search-engine-management.md)
