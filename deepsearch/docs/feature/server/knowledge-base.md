# 知识库管理

## 维护范围

本文档覆盖 `/api/kb` 下知识库创建、更新、删除、查询、文档上传、文档处理、状态查询和删除的总览。子能力细节见：

- [知识库文档上传与处理](./knowledge-base/document-upload-processing.md)
- [知识库索引、检索与存储](./knowledge-base/index-retrieval-storage.md)

不覆盖 framework 中 native local search 的运行时工具封装。

## 功能目的

知识库管理为本地检索提供 server 侧元数据、文件、对象存储和向量索引管理能力，使 DeepSearch 能在指定 `space_id` 下创建知识库、上传文档、构建索引并在研究运行时按知识库 ID 使用本地资料。

## 可见行为

- 知识库名称不能为空、不能前后带空格，且不能包含路径分隔符、通配符或控制字符。
- 同一 `space_id` 下知识库名称区分大小写查重。
- 创建知识库会生成无连字符 UUID 作为 `kb_id`。
- 知识库 config 会持久化 embedding 配置、LLM 配置和调用方扩展 config。
- 列表接口会返回知识库状态和是否存在图增强文档。
- 删除知识库会删除数据库记录、本地目录，并尽力删除 Milvus 中的 chunks/triples 索引。

## 关键代码路径

- `server/routers/knowledge_base.py`
- `server/schemas/knowledge_base.py`
- `server/local_retrieval/core/manager/knowledge_base.py`
- `server/local_retrieval/core/manager/repositories/knowledge_base_repository.py`
- `server/local_retrieval/models/knowledge_base.py`
- `server/local_retrieval/models/knowledge_base_document.py`
- `server/core/kb_obs_requirement.py`
- `server/local_retrieval/core/object/aioboto_storage_client.py`

## 核心流程

1. Router 使用 Pydantic schema 校验请求并调用 manager。
2. manager 检查知识库名称、数据库记录和索引服务连接。
3. repository 负责 SQLAlchemy 事务、分页、状态查询和记录写入。
4. 文档文件保存到本地路径，Redis 多实例模式可同步到 OBS。
5. 文档处理任务解析文件、分块、写入 Milvus，并更新文档状态。
6. DeepSearch 运行时通过 Agent 配置组装读取知识库 config，生成 native local search 配置。

## 数据契约与依赖

- 知识库表使用 `space_id` + `kb_id` 做主要查询路径。
- 文档表使用 `space_id`、`kb_id`、`doc_id` 标识文档。
- 文档状态包括 uploading、uploaded、processing、indexing、indexed、failed、deleted。
- 本地文件默认位于 `data/knowledge_base/<space_id>/<kb_id>/`。
- Milvus collection 命名为 `ds_kb_<kb_id>_chunks` 和 `ds_kb_<kb_id>_triples`。

## 边界与错误处理

- Milvus 连接检查失败时，创建知识库返回 503。
- 删除本地目录或索引失败不会回滚已删除的知识库数据库记录，只记录错误。
- 列表接口在数据库查询失败时返回空列表和 200，以保证前端列表页稳定。
- OBS 在 Redis checkpointer 模式下属于共享文件前提；配置缺失时文档上传返回 503。
- 当前知识库模块缺少集中测试文件，修改时应优先补充 server 侧测试。

## 测试与验证

- `PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q server/local_retrieval server/routers/knowledge_base.py`
- 修改 DeepSearch 使用知识库配置时，补充运行 `uv run pytest tests/server/test_agent_manager.py`。
- 修改 native retriever 契约时，补充运行 `uv run pytest tests/algorithm/search_tools/retrieval/test_knowledge_base_retriever.py`。

## 相关文档

- [DeepSearch Agent 配置组装](./deepsearch-agent-config.md)
- [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)
- [参数校验、安全目录与 URL 处理](../utils/validation-security-url.md)
