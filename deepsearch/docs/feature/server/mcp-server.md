# MCP Server 管理

## 概述

DeepSearch 支持接入外部 MCP（Model Context Protocol）服务器，将其工具注入到信息采集节点（`InfoRetrievalNode`），与网页搜索、本地知识库检索协同工作。本文档说明安装方式、字段含义、失败行为与 API 用法。

## 安装

MCP 客户端依赖经核心依赖 `openjiuwen==0.1.17` 传递引入，正常 `uv sync` 即已安装，无需额外操作：

```bash
uv sync --group dev                    # SDK + 开发依赖
uv sync --group backend --group dev    # 后端 + 开发依赖
```

传递版本约束为 `mcp>=1.26.0,<2.0`（`openjiuwen 0.1.17` 直接要求 `mcp>=1.26.0`，其 `fastmcp` 依赖限制 `mcp<2.0`）：

- **下限 `>=1.26.0`**：由核心依赖 `openjiuwen==0.1.17` 直接要求。
- **上限 `<2.0`**：v2.0 将底层 HTTP 库从 `httpx` 迁移为 `httpx2`。本仓库 `client.py` 通过 `httpx.AsyncClient(headers, timeout)` 注入鉴权头与超时（`streamable_http_client` 在所有版本均不接受 `headers`/`timeout` 参数），2.x 的 `http_client` 形参类型为 `httpx2.AsyncClient`，传入 `httpx.AsyncClient` 会导致运行时不兼容。

pyproject 的 `[mcp]` extra（`uv sync --extra mcp`）为显式声明，装不装效果一致。`tools/__init__.py` 与 `workflow.py` 仍保留 `try/except ImportError` 容错，防御未来 openjiuwen 不再传递 mcp 的场景。

## 字段含义

### `McpServerCreateRequestDTO`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `space_id` | `str` | — | 用户空间 ID |
| `server_name` | `str` | — | MCP server 名称，同一空间内唯一 |
| `server_url` | `str` | — | MCP server URL |
| `transport_type` | `Literal["sse","streamable_http"]` | `"streamable_http"` | 传输类型 |
| `headers` | `dict \| None` | `None` | 请求头字典；敏感 header value 加密存储 |
| `timeout` | `float` | `30.0` | 连接/读取超时（秒），须大于 0 |
| `type` | `str` | `"search"` | MCP server 用途类型，决定工具注入到哪个采集分支 |
| `extension` | `dict \| None` | `None` | 扩展配置 |
| `is_active` | `bool \| None` | `None` | 是否激活 |

### 敏感 header 加密

以下 header key 的 value 会在落库前用 `SecurityUtils.encrypt_api_key` 加密（AES-GCM）：

- `authorization`、`proxy-authorization`
- `x-api-key`、`api-key`、`apikey`
- `x-auth-token`、`x-custom-token`、`x-auth`、`bearer-token`
- `x-secret`、`secret`
- `x-goog-api-key`、`x-goog-cloud-api-key`、`anthropic-api-key`、`x-deepseek-api-key`
- `cookie`、`set-cookie`

加密需配置环境变量 `SERVER_AES_MASTER_KEY_ENV`（32 字节 base64）。**未配置主密钥时，创建/更新带敏感 header 的 MCP server 会被显式拒绝**（抛 `ValidationError`），不会静默存明文。

## 失败行为

- **单个 server 连接失败**：`McpToolBundle` 记 `warning` 日志后跳过该 server，不阻塞其他 server。调用方得到空工具列表（该 server 的），不会看到连接错误冒泡。
- **MCP 包缺失（未来 `openjiuwen` 不再传递 `mcp` 时）**：工作流初始化时延迟导入抛 `ImportError`，被 `logger.warning` 捕获后该次运行不接入 MCP 工具，其余流程正常。当前 `openjiuwen==0.1.17` 已传递引入 `mcp`，正常 `uv sync` 不会触发此分支。
- **工具名碰撞**：不同原始工具名清洗后若碰撞（如 `a.b` 与 `a b` 均成 `a_b`），后一个会被追加序号后缀（如 `a_b_1`），并记 `warning`，不会互相覆盖。

## API 用法

MCP server CRUD 端点注册在 `server/routers/mcp_server_router.py`：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/mcp_server/create` | 创建 MCP server |
| POST | `/mcp_server/get` | 获取指定 MCP server |
| POST | `/mcp_server/list` | 获取 MCP server 列表 |
| POST | `/mcp_server/delete` | 删除指定 MCP server |
| POST | `/mcp_server/update` | 更新指定 MCP server |

运行请求中通过 `mcp_servers: List[McpServerConfig]` 引用已创建的 MCP server（按 `mcp_server_id` + `type`）。

## 工作流集成

`DeepresearchAgent`（research 模式）、`DeepSearchAgent`（search 模式）、`SimpleReactSearchAgent`（react 模式）在 `tool_map == "search_fetch"` 时均会调用 `_initialize_mcp_context_from_agent_config` 连接 MCP servers，将工具注册到 `mcp_tool_context` contextvar，并在运行结束后 `close_all` 关闭连接。`InfoRetrievalNode._pre_handle` 从 contextvar 读取工具，`_do_invoke` 构造的 `sub_state` 会把 `mcp_tools` 下传到 `_collector_main` / `_prepare_collector_tool`。
