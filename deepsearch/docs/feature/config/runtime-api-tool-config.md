# Runtime API 工具配置

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/config/runtime_api_models.py` 中的 runtime API 工具配置模型，以及 `AgentConfig.api_tools_config` 被查询理解和资料采集流程消费的当前契约。

不覆盖 runtime API HTTP 调用实现的完整细节；工具注册、URL 安全和响应限制见相关 framework/utils 文档。

## 功能目的

Runtime API 工具配置允许调用方在运行时声明外部 HTTP 工具，并把这些工具接入查询理解、规划、大纲生成和资料采集流程，减少为每个外部服务新增固定代码 wrapper 的成本。

## 可见行为

- `AgentConfig.api_tools_config.query_understanding_tools` 会注入查询理解相关工具集合，并与默认工具按名称合并。
- `AgentConfig.api_tools_config.collector_tools` 会注入资料采集工具集合，采集结果可被归一化为 collector 可消费的搜索结果。
- runtime API 工具会动态生成 openJiuwen `LocalFunction` 的 tool card 和输入 schema。
- 工具名会经过 `sanitize_tool_name` 归一化；与默认工具或已有 runtime 工具重名时保留已有工具并记录 warning。
- `response_wrapper=search_result` 且未要求 `response_model` 时，会把常见搜索返回结构转换为 `search_engine` 和 `search_results`。

## 关键代码路径

- `openjiuwen_deepsearch/config/runtime_api_models.py`
- `openjiuwen_deepsearch/config/config.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/runtime_api/runtime_api.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/runtime_api/api_wrapper.py`
- `openjiuwen_deepsearch/algorithm/query_understanding/outliner.py`
- `openjiuwen_deepsearch/algorithm/query_understanding/planner.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/info_collector.py`
- `openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`
- `tests/tools/test_runtime_api.py`
- `tests/report/test_tools_in_report.py`

## 核心流程

1. 调用方在 `agent_config.api_tools_config` 中传入 query understanding 或 collector 工具列表。
2. `StartNode` 把 `api_tools_config` 写入 session `config`。
3. 查询理解、规划或采集节点读取对应工具列表。
4. `build_runtime_api_tools` 把配置模型或字典转换为 `LocalFunction`。
5. `merge_runtime_api_tools` 将 runtime 工具合并进默认工具集合。
6. 工具调用时按配置拆分 header、query 和 JSON body 参数，发起 HTTP 请求并读取 JSON 响应。
7. collector 路径可将 runtime API 返回值归一化为搜索结果 payload，再进入资料采集处理。

## 数据契约与依赖

- `RuntimeApiToolConfig.tool_id` 用作工具卡 id，缺省时使用 `name`。
- `RuntimeApiToolConfig.name` 是工具名称，也是重名合并的主要依据。
- `path` 可以是完整 URL；也可以配合 `base_url` 拼接。
- `http_method` 支持 get、post、put、delete、patch、head、options，默认 post。
- `request_params` 和 `response_params` 使用 `RuntimeApiToolParamConfig`；当前响应参数预留给未来映射。
- `param_type` 映射 JSON schema：1 为 string，2 为 integer，3 为 number，4 为 boolean；未知值按 string 处理。
- `send_method` 支持 none、header、query、body；`none` 会进入 body，但不参与 required 发送校验。
- `headers` 是静态请求头，运行时同名 header 参数可以覆盖静态值。
- `response_wrapper` 当前支持 `search_result`，未知值回退为原始 payload。

## 边界与错误处理

- runtime API 请求 URL 必须通过 `validate_runtime_request_url`，默认禁止不安全 scheme、localhost、私有地址和保留地址。
- 必填参数缺失且 `send_method` 不是 none 时，返回 `PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR`。
- HTTP 响应会先检查状态码，再限制响应体最大 2 MiB、JSON 深度最大 20、单个对象或数组最多 1000 项。
- 搜索结果归一化最多保留 20 条，并按公共常量裁剪标题、URL 和内容长度。
- runtime API 工具是运行时扩展面，新增字段时需要同时考虑 Pydantic 契约、工具 schema、HTTP 请求组装和测试覆盖。

## 测试与验证

- `uv run pytest tests/tools/test_runtime_api.py`
- 修改报告工具透传时，补充运行 `uv run pytest tests/report/test_tools_in_report.py`。
- 修改查询理解工具注入时，补充运行相关 query understanding 测试。

## 相关文档

- [Agent 与服务运行配置](./agent-and-service-config.md)
- [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)
- [资料采集](../algorithm/research-collector.md)
- [查询理解](../algorithm/query-understanding.md)
- [参数校验、安全目录与 URL 处理](../utils/validation-security-url.md)
