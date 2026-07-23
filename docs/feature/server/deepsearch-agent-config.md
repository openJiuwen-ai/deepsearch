# DeepSearch Agent 配置组装

## 维护范围

本文档覆盖 server 层如何从 `DeepSearchRequest`、数据库中的联网搜索配置、知识库配置和模板记录组装 framework 所需的 `agent_config`，以及 Agent 实例缓存和会话清理。

不覆盖 Agent 内部执行；见 framework 和 algorithm 文档。

## 功能目的

Agent 配置组装把前端/HTTP 请求转换为 `AgentFactory` 可校验的配置字典，同时保证搜索引擎、知识库、runtime API 工具、模板和敏感字段在进入 framework 前符合当前仓库契约。

## 可见行为

- 同一个配置缓存 key 命中时复用 Agent 实例。
- 同一 `conversation_id` 且配置缓存 key 相同时，连续 `get_or_create_agent` 返回同一实例。
- DeepSearch `search` / `react` Agent 复用是安全的：每次 `run` 的可变状态在 `DeepSearchRunContext` 中，不挂在实例字段上。
- 缓存 key 排除 `message` 和 `interrupt_feedback`，保留影响 Agent 构建的字段。
- `llm_config` 可以是单模型形态，也可以是按槽位嵌套形态；单模型会包成 `general`。
- `info_collector_search_method` 不是 `local` 时必须提供 web 搜索配置；不是 `web` 时必须提供 local 搜索配置。
- `info_collector_webpage_enrich_enable` 从请求透传到 Agent 配置，用于控制 DeepResearch 信息采集阶段是否启用网页正文增强节点。
- `template_id>0` 会标记 `has_template=True`，运行上下文会加载模板正文。
- 请求中的 `tools` 会同时转换为 query understanding 和 collector 的 runtime API 工具配置。

## 关键代码路径

- `server/deepsearch/core/manager/agent.py`
- `server/schemas/deepsearch_run.py`
- `server/deepsearch/core/manager/repositories/web_search_engine_repository.py`
- `server/local_retrieval/core/manager/repositories/knowledge_base_repository.py`
- `server/deepsearch/core/manager/repositories/report_template_repository.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/agent_factory.py`
- `tests/server/test_agent_manager.py`

## 核心流程

1. `build_agent_config` 读取请求基础字段，写入 search mode、execution method、HITL、溯源、用户反馈、LLM 统计、图表开关和网页正文增强开关。
2. 读取 web 搜索引擎记录，解密 API key，生成 `web_search_engine_config`。
3. 读取 local 知识库记录，校验所有 `local_search_config_ids` 属于当前 `space_id`。
4. 从知识库 config 中提取 embedding 配置，并为每个知识库生成 native local search 的 `knowledge_base_configs`。
5. 将 runtime API tool 请求转换为 `ApiToolsConfig` 形态。
6. `get_or_create_agent` 使用缓存 key 查找或通过 `AgentFactory.create_agent` 创建 Agent。
7. `cleanup_session_cache` 按会话驱逐缓存并请求 checkpointer release。

## 数据契约与依赖

- runtime API HTTP method 数字映射：1 get、2 post、3 put、4 delete；字符串 method 支持 get、post、put、delete、patch、head、options。
- runtime API 参数发送方式数字映射：0 none、1 header、2 query、3 body。
- local search server 配置固定使用 `search_engine_name=native`。
- native local search 的向量 collection 名为 `ds_kb_<kb_id>_chunks`。
- Milvus URI 由 `MILVUS_HOST` 和 `MILVUS_PORT` 组成，token 来自 `MILVUS_TOKEN`。
- web search API key 从数据库读取后以 `bytearray` 传入 framework。
- `DeepSearchRequest.info_collector_webpage_enrich_enable` 默认 `False`，透传为 `AgentConfig.info_collector_webpage_enrich_enable`。

## 边界与错误处理

- 缺少必要 web/local 搜索配置时抛 `SearchEngineConfigException`。
- web 搜索记录不存在或解密失败会包装为 web search config 获取异常。
- local 知识库不存在、跨 space 或缺少 `embed_model_config` 时会抛搜索配置异常。
- `cleanup_session_cache` 的 checkpointer release 失败只记录 warning，不阻断上层清理。
- 新增影响 Agent 构建的请求字段时，必须确认缓存 key 是否应包含该字段。

## 测试与验证

- `uv run pytest tests/server/test_agent_manager.py`
- 修改缓存、清理或超时透传逻辑时，补充针对 `DeepSearchAgentManager` 的 server 单测。

## 相关文档

- [DeepSearch 运行与 SSE 流](./deepsearch-run-streaming.md)
- [DeepSearch 搜索子工作流](../framework/deepsearch-sub-workflows.md)
- [Agent 与服务运行配置](../config/agent-and-service-config.md)
- [Runtime API 工具配置](../config/runtime-api-tool-config.md)
- [知识库管理](./knowledge-base.md)
