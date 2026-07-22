# Agent 与服务运行配置

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/config/config.py` 中的 `Config`、`AgentConfig`、`ServiceConfig`、LLM 配置、web/local 搜索配置、自定义搜索配置，以及 `ExecutionMethod`、`SearchMode` 运行模式枚举。

不覆盖 DeepSearch 搜索工作流内部参数和 runtime API 工具 schema；见相关文档。

## 功能目的

运行配置为 SDK、server 和 framework 层提供统一的 Pydantic 契约，把模型、搜索、报告、溯源、用户反馈、图表生成、超时、统计和调试开关集中成可校验的输入。

## 可见行为

- `AgentFactory.create_agent` 会先校验 `agent_config` 必填字段，再用 `AgentConfig.model_validate` 做类型、枚举和范围校验。
- `search_mode` 决定运行研究报告、DeepSearch 搜索图或简单 ReAct 搜索；`search_mode=research` 时再由 `execution_method` 决定并行或依赖驱动研究工作流。
- `llm_config` 按模型槽位传入，运行时至少需要 `general` 槽位；部分节点可使用 `plan_understanding`、`info_collecting`、`writing_checking` 或 `vlm_chart_generating`。
- `StartNode` 只把本次运行需要的 `agent_config` 字段合并进 session，再叠加默认 `ServiceConfig`，形成节点读取的 `config` 全局状态。
- `ServiceConfig` 提供 SDK 内部默认参数，包括 workflow 超时、重试次数、采集循环、报告生成、溯源并发、LLM 默认超时、thinking 模式、节点调试和中间结果导出。
- `ServiceConfig.info_collector_max_search_query_count` 控制单轮 query 生成硬上限；
  `ServiceConfig.info_collector_max_research_loops` 控制信息采集 research loop 硬上限；
  `ServiceConfig.info_collector_max_tool_call_turns_per_query` 控制单个检索 query 内的工具调用轮次，三者独立生效。
- `info_collector_webpage_enrich_enable=True` 时，DeepResearch 信息采集子图会启用网页正文增强节点；默认关闭。
- `vlm_chart_generator_enable=True` 时会关闭 Mermaid 图文并茂插入；如果缺少 VLM 模型配置，入口校验会自动关闭 VLM 迭代图生成。
- LLM、搜索和 embedding 密钥字段使用 `bytearray`，调用方和消费代码应沿用现有 `zero_secret` 清理策略。

## 关键代码路径

- `openjiuwen_deepsearch/config/config.py`
- `openjiuwen_deepsearch/config/method.py`
- `openjiuwen_deepsearch/config/search_mode.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/agent_factory.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/utils/validation_utils/field_validation.py`
- `tests/server/test_deepsearch_run.py`
- `tests/server/test_agent_manager.py`
- `tests/node/test_agent_node.py`
- `tests/user_feedback_processor/test_user_feedback_processor_node.py`

## 核心流程

1. SDK 或 server 入口接收 `agent_config` 字典。
2. `validate_agent_required_field` 校验必填字段和搜索配置存在性。
3. `AgentConfig.model_validate` 校验枚举、范围和嵌套模型。
4. `AgentFactory` 根据 `search_mode` 和 `execution_method` 选择具体 agent 类。
5. 研究工作流启动时，`StartNode` 把运行输入转成 `SearchContext` 和 session `config`。
6. 下游节点通过 `session.get_global_state("config.*")` 读取配置，或者通过 `Config().service_config` 读取默认服务配置。

## 数据契约与依赖

- `Config` 由 `agent_config` 和 `service_config` 两部分组成。
- `AgentConfig.execute_mode` 取值为 `commercial` 或 `general`。
- `AgentConfig.execution_method` 取值为 `parallel` 或 `dependency_driving`，只在 `search_mode=research` 时选择研究工作流实现。
- `AgentConfig.search_mode` 取值为 `research`、`search`、`react`。
- `AgentConfig.info_collector_webpage_enrich_enable` 控制信息采集阶段是否启用网页正文增强节点，默认 `False`。
- `WebSearchEngineConfig.search_engine_name` 支持 tavily、google、xunfei、petal、custom、bocha、jina、perplexity、serper。
- `WebFetchProviderConfig` 通过 `AgentConfig.web_fetch_provider_config` 显式选择 DeepSearch 网页抓取 provider；当前有效 `provider_name` 为 `jina`。
- 顶层 `jina_api_key` / `serper_api_key` 已退役；传入时 `AgentConfig` 校验失败，应改用 `web_fetch_provider_config` 与 `web_search_engine_config`。
- `LocalSearchEngineConfig.search_engine_name` 支持 openapi、custom、native；native 模式依赖 `knowledge_base_configs`。
- web/local 最大搜索结果数均限制在 1 到 10。
- `outliner_max_section_num` 范围为 1 到 `OUTLINER_SECTION_NUM_MAX`，当前最大值为 15。
- `outline_interaction_max_rounds` 和 `user_feedback_processor_max_interactions` 范围为 1 到 100。
- `vlm_chart_generator_max_iterations` 范围为 1 到 3。
- `ServiceConfig.info_collector_webpage_enrich_max_urls` 默认 3，限制单轮最多增强的 URL 数量。
- `ServiceConfig.info_collector_webpage_enrich_fetch_timeout_seconds` 默认 45，限制单个 URL 抓取超时时间。

## 边界与错误处理

- `agent_config` 为空、缺少 `execute_mode`、`llm_config`、`info_collector_search_method`，或同时缺少 web/local 搜索配置时，会抛参数校验异常。
- Pydantic 校验失败时，普通模式返回 `PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR`；敏感日志模式下返回不打印细节的错误码。
- `search_mode` 或 `execution_method` 不在枚举内时，返回 `WORKFLOW_TYPE_NOT_EXIST_ERROR`。
- 运行期缺少 `llm_config.general` 时，返回 `LLM_CONFIG_NONE`。
- 不要在文档或日志中复制密钥值；新增密钥字段时应保持 `bytearray` 和清零约定。

## 测试与验证

- `uv run pytest tests/server/test_deepsearch_run.py`
- `uv run pytest tests/server/test_agent_manager.py`
- `uv run pytest tests/node/test_agent_node.py`
- 修改用户反馈配置时，补充运行 `uv run pytest tests/user_feedback_processor/test_user_feedback_processor_node.py`。
- 修改 LLM 超时或模型槽位时，补充运行 `uv run pytest tests/utils/test_llm_utils.py`。

## 相关文档

- [DeepSearch 搜索工作流配置](./search-workflow-config.md)
- [Runtime API 工具配置](./runtime-api-tool-config.md)
- [DeepSearch 网页抓取 Provider 注册](../framework/web-fetch-provider-registry.md)
- [Agent 工厂与运行模式](../framework/agent-factory.md)
- [报告研究主工作流](../framework/research-workflow.md)
- [LLM 运行时封装](../llm/llm-runtime.md)
- [参数校验、安全目录与 URL 处理](../utils/validation-security-url.md)
