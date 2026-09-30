# MCP 工具集成

## 维护范围

本文档覆盖 framework 层 MCP（Model Context Protocol）客户端模块、工具转换、contextvar 传递、
工作流生命周期接入与信息采集节点工具注入。

## 功能目的

把外部 MCP server 暴露的工具接入 DeepSearch 信息采集节点（`InfoRetrievalNode`），与网页搜索、
本地知识库检索、runtime API 工具协同工作。MCP 工具按 `type` 分类注入到对应采集分支，当前
仅 `search` 类型的工具会进入资料采集子图。

## 可见行为

- `DeepresearchAgent`（research 模式）、`DeepSearchAgent`（search 模式）、`SimpleReactSearchAgent`
  （react 模式）在 `run` 开始时读取 `agent_config.mcp_servers`，连接全部 MCP server 并把工具
  bundle 写入 `mcp_tool_context`；运行结束后关闭所有连接。
- MCP 客户端依赖经核心依赖 `openjiuwen==0.1.17` 传递引入（`openjiuwen` 直接要求 `mcp>=1.26.0`，
  其 `fastmcp` 依赖限制 `mcp<2.0`），正常 `uv sync` 即已安装。`pyproject` 的 `[mcp]` extra 为显式
  声明，装不装效果一致；`tools/__init__.py` 与 `workflow.py` 的 `try/except ImportError` 容错防御
  未来 `openjiuwen` 不再传递 `mcp` 的场景——缺失时延迟导入抛 `ImportError`，被 `logger.warning`
  捕获后该次运行不接入 MCP 工具，其余流程正常。
- 单个 MCP server 连接失败时，bundle 记 `warning` 后跳过该 server，不阻塞其他 server；调用方
  得到该 server 的空工具列表。
- MCP 工具名归一为 `mcp__{server}__{tool}`；清洗后碰撞的工具名追加序号后缀（如 `a_b_1`）并记
  `warning`，不会互相覆盖。
- 工具执行返回错误（SDK `isError=True`）或抛异常时，返回 `{"error": ...}` 并记 `warning`，不冒泡
  到采集循环。
- CLI 通过 `--mcp_server_url` / `--mcp_server_name` / `--mcp_server_type` 直接配置 MCP server，
  支持逗号分隔多个。
- Server 侧运行请求通过 `mcp_servers: List[McpServerConfig]`（`mcp_server_id` + `type`）引用已
  创建的 MCP server，由 `AgentManager._load_mcp_config` 查库解密 header 后拼装成
  `McpServerRuntimeConfig` 列表注入 `AgentConfig`。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/tools/mcp/client.py` — `McpClient`：单 server 连接封装
- `openjiuwen_deepsearch/framework/openjiuwen/tools/mcp/tool_builder.py` — `build_mcp_local_functions`：工具描述转 `LocalFunction`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/mcp/bundle.py` — `McpToolBundle`：多 server 生命周期与按 type 分类
- `openjiuwen_deepsearch/framework/openjiuwen/tools/__init__.py` — 可选依赖容错导入
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py` — `_initialize_mcp_context_from_agent_config` / `_close_mcp_bundle` 与三个 Agent 的生命周期接入
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/info_collector.py` — `InfoRetrievalNode` 工具读取与下传
- `openjiuwen_deepsearch/utils/constants_utils/session_contextvars.py` — `mcp_tool_context`
- `openjiuwen_deepsearch/config/config.py` — `McpServerRuntimeConfig` / `AgentConfig.mcp_servers`
- `main.py` — CLI 参数 `--mcp_server_url` / `--mcp_server_name` / `--mcp_server_type`
- `server/deepsearch/core/manager/agent.py` — `_load_mcp_config`：DB 查询解密拼装

## 核心流程

1. **配置来源**
   - CLI：`main.py` 解析 `--mcp_server_url`（逗号分隔多个），按 `--mcp_server_name` 一一对应（为空时
     按序号兜底），构造 `McpServerRuntimeConfig` 列表写入 `agent_config["mcp_servers"]`。
   - Server：`AgentManager` 收到请求的 `mcp_servers: List[McpServerConfig]` 后，调
     `_load_mcp_config(space_id, mcp_configs, db)`，经 `McpServerRepository.get_server_detail_by_id`
     查库并解密 header，拼装成 `McpServerRuntimeConfig` 列表。
2. **Agent.run 初始化 MCP 上下文**
   - `_initialize_mcp_context_from_agent_config(agent_config)` 读取 `agent_config.mcp_servers`，为空
     时直接返回 `None`。
    - 函数内延迟导入 `McpToolBundle`（当前 `openjiuwen==0.1.17` 已传递 `mcp`，正常 `uv sync`
      不会触发；未来 `openjiuwen` 不再传递时抛 `ImportError` 由调用方捕获）。
   - `bundle.connect_and_build_tools(servers_dict_list)` 连接所有 server、列举工具、包装
     `LocalFunction`、按 `type` 分类。
   - 返回 `mcp_tool_context.set(bundle)` 的 token 供 finally reset。
3. **bundle 连接与工具包装**
   - 对每个 server_config 构造 `McpClient`，`connect()` 建立 MCP 会话（`ClientSession.initialize()`）。
   - `build_mcp_local_functions(client, server_name)` 调 `list_tools()`，为每个 MCP Tool 构造
     `LocalFunction`（`ToolCard` + `_invoke` 闭包）。
   - 工具按 `server_config.get("type", "search")` 分类存入 `_tools_by_type`。
4. **InfoRetrievalNode 读取工具**
   - `_pre_handle` 从 `mcp_tool_context.get()` 读 bundle（`LookupError` 时视为空），调
     `get_tools_by_type("search")` 得到 `mcp_tools`，写入 `state["mcp_tools"]`。
   - `_do_invoke` 为每条 query 构造 `sub_state` 时**必须**下传 `mcp_tools`（与禁引约束开关同等要求），
     否则 `_collector_main` / `_prepare_collector_tool` 读到 `[]`，采集模型看不到
     `mcp__{server}__{tool}`。
   - `_prepare_collector_tool` 把 `mcp_tools` 与 web/local/api tools 合并进 `tool_list` /
     `tool_dict`，统一暴露给采集模型。
5. **工具调用**
   - 采集模型选择 `mcp__{server}__{tool}` 工具后，`_invoke` 闭包调 `client.call_tool(name, kwargs)`，
     读 `CallToolResult.isError` 判断成败。
6. **运行结束关闭**
   - `_close_mcp_bundle(mcp_token)` 在三个 Agent 的 `try` / `except` / `finally` 三条路径（成功、
     失败、取消）上被调用，`bundle.close_all()` 关闭全部 `McpClient` 并 reset contextvar。

## 数据契约与依赖

### `McpServerRuntimeConfig`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `server_name` | `str` | — | MCP server 名称 |
| `server_url` | `str` | — | MCP server URL |
| `transport_type` | `str` | `"streamable_http"` | 传输类型，`sse` 或 `streamable_http` |
| `headers` | `dict` | `{}` | 请求头字典；Server 侧已解密，CLI 侧为空 |
| `timeout` | `float` | `30.0` | 连接/读取超时（秒） |
| `type` | `str` | `"search"` | 用途类型，决定工具注入到哪个采集分支 |

### `McpClient` 协议

- `connect()`：用 `AsyncExitStack` 管理传输层与 `ClientSession`。`sse` 走 `sse_client(url, headers,
  timeout)`；`streamable_http` 因 `streamable_http_client` 不接收 `headers`/`timeout`，改为经
  `httpx.AsyncClient(headers, timeout)` 注入。
- `list_tools()`：未连接时抛 `RuntimeError`。
- `call_tool(name, arguments)`：未连接时抛 `RuntimeError`。
- `close()`：`aclose` 传输栈并清空 session。

### 工具名归一

- 格式：`mcp__{_clean_name_segment(server_name)}__{_clean_name_segment(tool_name)}`。
- `_clean_name_segment` 把非 `[a-zA-Z0-9_-]` 字符替换为 `_`，合并连续 `_`，两端剥离；空串兜底为
  `mcp_tool`。
- 碰撞处理：同 server 下两个原始名清洗后相同（如 `a.b` 与 `a b` 均成 `a_b`），后一个追加
  `_{len(seen_names)}` 序号并记 `warning`。

### 工具执行结果

- 读 SDK 的 `CallToolResult.isError`（非 `is_error`）判断错误。
- 成功时从 `result.content` 提取所有 `TextContent.text` 拼接，`json.loads` 解析；解析失败返回
  `{"mcp_raw_output": text}`（key 不用 `content` 以避免与工具自身 JSON 结构碰撞）。
- `isError=True` 时返回 `{"error": error_text}`，`error_text` 为空时兜底 `MCP tool execution failed`。
- 执行抛异常时 `logger.warning`（带 `tool_name` + `server_name`）。

### contextvar

- `mcp_tool_context`：`ContextVar("mcp_tool_bundle", default=None)`。
- bundle 持有持久连接（`McpClient` 含 `AsyncExitStack` 与 `httpx.AsyncClient`），不可被框架日志的
  `dataclasses.asdict` deep-copy，故与 `tool_context` 同样用 contextvar 传递，不进入 workflow inputs。
- token 必须在所有退出路径 reset。

## 边界与错误处理

- **mcp 包缺失（未来 `openjiuwen` 不再传递 `mcp` 时）**：`tools/__init__.py` 用 `try/except ImportError`
  包裹 `mcp` 子模块导入，缺失时 `McpClient` / `McpToolBundle` / `build_mcp_local_functions` 置 `None`。
  `workflow.py` 的 `_initialize_mcp_context_from_agent_config` 内延迟导入，缺失时抛 `ImportError`
  被调用方 `logger.warning` 捕获，该次运行不接入 MCP 工具。当前 `openjiuwen==0.1.17` 已传递引入
  `mcp`，正常 `uv sync` 不会触发此分支。
- **单 server 连接失败**：`bundle` 记 `warning`（含 `server_name`）后 `client.close()` 跳过，不阻塞
  其他 server。调用方得到空工具列表（该 server 的），不会看到连接错误冒泡。
- **工具名碰撞**：追加序号后缀 + `warning`，不互相覆盖。
- **工具执行失败**：`isError` 或异常时返回 `{"error": ...}` + `warning`，不冒泡到采集循环。
- **contextvar reset**：三个 Agent 的 `try` / `except` / `finally` 三条路径（成功、失败、取消）
  均调用 `_close_mcp_bundle`；`token is None` 时直接返回。
- **streamable_http headers 限制**：`mcp` 库的 `streamable_http_client` 不接收 `headers`/`timeout`
  参数，经 `httpx.AsyncClient(headers, timeout)` 注入。CLI 注释提示：Tavily 等需通过 URL query
  param 传 API key。
- **sse 传输**：直接经 `sse_client(url, headers, timeout)` 传递，行为不变。

## 测试与验证

```bash
uv run pytest tests/tools/mcp/test_mcp_client.py
uv run pytest tests/tools/mcp/test_bundle.py
uv run pytest tests/tools/mcp/test_tool_builder.py
uv run pytest tests/workflow/test_mcp_workflow_integration.py
uv run pytest tests/info_collector/test_mcp_info_collector.py
uv run pytest tests/server/test_agent_manager_mcp.py
uv run pytest tests/server/test_mcp_server_repository.py
uv run pytest tests/server/test_mcp_server_router.py
```

必须覆盖的场景：

- `McpClient` 的 `connect`/`list_tools`/`call_tool`/`close` 在已连接与未连接状态下的行为；`streamable_http`
  分支 headers/timeout 经 `httpx.AsyncClient` 注入。
- `McpToolBundle` 单 server 失败不阻塞其他 server；`close_all` 清空状态。
- `tool_builder` 读取 SDK 的 `isError` 字段（非 `is_error`）；工具名碰撞追加序号；非 JSON 输出走
  `mcp_raw_output` 兜底。
- `_initialize_mcp_context_from_agent_config` 与 `_close_mcp_bundle` 在三个 Agent 的成功、失败、取消
  路径都被调用。
- `InfoRetrievalNode._pre_handle` 从 contextvar 读 mcp_tools；`_do_invoke` 构造的 `sub_state` 把
  `mcp_tools` 下传到 `_run_retrieval_query`。
- `_load_mcp_config` 在 server 不存在时抛对应异常。

## 相关文档

- [MCP Server 管理](../server/mcp-server.md) — server 侧 CRUD、敏感 header 加密、API 用法
- [搜索工具注册与运行时 API 工具](./search-tool-registration.md) — 同类工具注册模式
- [信息采集子图](./info-collector-subgraph.md)
- [报告研究主工作流](./research-workflow.md)
- [节点基类与会话上下文](./base-node-and-session-context.md) — contextvar 机制
