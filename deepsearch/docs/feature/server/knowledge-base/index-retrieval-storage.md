# 知识库索引、检索与存储

## 维护范围

本文档覆盖知识库索引构建、Milvus collection 管理、图增强 triples 索引、文档/知识库删除时的索引清理、本地文件与 OBS 存储约束。

不覆盖 DeepSearch 运行时检索工具的 prompt 和工具调用细节。

## 功能目的

索引与存储层把解析后的文档写入可检索的向量索引，并在删除知识库或文档时尽力清理相关数据，保证本地检索配置能被 framework native local search 使用。

## 可见行为

- `INDEX_MANAGER_TYPE=milvus` 时会检查 Milvus 连接并创建 Milvus indexer/vector store。
- 文档索引进入写入阶段时，状态更新为 `indexing`。
- 默认写入 chunks collection；启用图增强时额外写入 triples collection。
- 如果已存在 collection 的向量维度与当前 embedding 输出维度不同，会先 drop 旧 collection。
- 删除文档会删除本地文件、OBS 对象和 chunks/triples 索引。
- 删除知识库会分页获取所有文档并逐个清理索引。

## 关键代码路径

- `server/local_retrieval/core/manager/knowledge_base.py`
- `server/local_retrieval/core/object/base_storage_client.py`
- `server/local_retrieval/core/object/aioboto_storage_client.py`
- `server/core/kb_obs_requirement.py`
- `server/local_retrieval/core/manager/repositories/knowledge_base_repository.py`
- `server/local_retrieval/models/knowledge_base.py`
- `server/local_retrieval/models/knowledge_base_document.py`
- `tests/algorithm/search_tools/retrieval/test_knowledge_base_retriever.py`

## 核心流程

1. 创建知识库时检查索引服务连接。
2. 文档处理阶段创建 embedding model、可选 LLM client、chunker、Milvus indexer 和 vector store。
3. 根据 `enable_graph_enhancement` 选择 simple 或 graph knowledge base。
4. 调用 `add_documents` 写入索引。
5. 记录 chunk index、可选 triple index 和估算 chunk 数。
6. 更新文档状态为 `indexed`，保存 `es_index_name`、`chunk_count` 和 `process_info.index_result`。
7. 删除路径按 doc_id 从 chunks/triples collection 清理索引。

## 数据契约与依赖

- Milvus 连接读取 `MILVUS_HOST`、`MILVUS_PORT` 和 `MILVUS_TOKEN`。
- `INDEX_MANAGER_TYPE` 当前主要支持 `milvus`。
- chunks collection 名称为 `ds_kb_<kb_id>_chunks`。
- triples collection 名称为 `ds_kb_<kb_id>_triples`。
- 知识库持久化 config 中必须包含 `embed_model_config`；图增强需要有效 LLM 配置。
- OBS 读取 `OBS_BUCKET`、`OBS_SERVER`、`OBS_REGION`、`OBS_ACCESS_KEY_ID` 和 `OBS_SECRET_ACCESS_KEY`。

## 边界与错误处理

- Milvus 未安装或无法连接时，创建知识库返回服务不可用。
- collection 维度不一致时会 drop 旧 collection，这是破坏性操作，修改前要确认业务预期。
- 删除索引时 collection 或 doc_id 不存在视为成功。
- 索引清理失败会记录 warning，但文档/知识库删除流程尽量继续。
- Redis checkpointer 多实例模式下，OBS 配置缺失会阻止上传，以避免其他 worker 找不到文件。

## 测试与验证

- `uv run pytest tests/algorithm/search_tools/retrieval/test_knowledge_base_retriever.py`
- 修改 Milvus/OBS 配置读取时，补充 server 侧单测或集成验证。
- 修改删除清理逻辑时，验证 chunks 和 triples 两类索引。

## 相关文档

- [知识库管理](../knowledge-base.md)
- [知识库文档上传与处理](./document-upload-processing.md)
- [DeepSearch Agent 配置组装](../deepsearch-agent-config.md)
