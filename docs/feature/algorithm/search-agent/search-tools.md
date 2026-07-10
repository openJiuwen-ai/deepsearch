# Search Tools

## 维护范围

本文档覆盖 DeepSearch 搜索智能体使用的搜索工具封装，包括 web search、web fetch、本地或固定语料检索工具，以及 runtime tool 与 native tool 的边界。

本文档不覆盖搜索节点状态机和索引构建细节。

## 功能目的

Search tools 为 run action 节点提供可控的信息获取能力。它们把 LLM tool call 转换为实际 web search、网页抓取或本地语料检索，并把结果整理为搜索节点可解析的工具消息。DeepSearch 的 `web_search` adapter 复用 framework 层已注册的 web search wrapper，并把 wrapper 返回的标准化搜索结果重新格式化为搜索循环已兼容的文本输出。

## 可见行为

- 普通搜索模式暴露 `web_search` 和 `web_fetch`。
- retrieve-only 模式只暴露 `retrieve`。
- 工具名会归一化并校验白名单。
- web search 支持多 query，一次调用返回合并结果。
- search/react 模式的 `web_search` 使用 `web_search_engine_config` 初始化并注册的活动搜索引擎，而不是单独构造 Serper 专用客户端。
- web fetch 接收 URL 和 goal，返回与目标相关的网页摘要或证据。

## 关键代码路径

- web search 工具：`openjiuwen_deepsearch/algorithm/search_tools/web_search_tool.py`
- web fetch 工具：`openjiuwen_deepsearch/algorithm/search_tools/web_fetch_tool.py`
- retriever 工具：`openjiuwen_deepsearch/algorithm/search_tools/retriever_tool.py`
- retriever 实现：`openjiuwen_deepsearch/algorithm/search_tools/retrieval/retriever.py`
- embedder：`openjiuwen_deepsearch/algorithm/search_tools/retrieval/embedder.py`
- run action 工具定义：`openjiuwen_deepsearch/algorithm/search_nodes/run_action.py`
- 搜索引擎注册与调用：`openjiuwen_deepsearch/framework/openjiuwen/tools/web_search.py`

主要测试：

- `tests/tools/test_web_search.py`
- `tests/tools/search_api/test_jina.py`
- `tests/tools/search_api/test_local_search.py`
- `tests/tools/search_api/test_native_local_search.py`
- `tests/search_agent/test_jina_reader_endpoints.py`
- `tests/search_agent/test_run_action.py`

## 核心流程

1. run action 根据 retrieval mode 选择允许的工具定义。
2. LLM 生成 tool call。
3. 工具名被归一化到 canonical name。
4. 对应工具执行 web search、web fetch 或 retrieve。
5. 工具结果被写回 LLM messages。
6. run action 继续解析最终状态或答案。

## 数据契约与依赖

工具输入：

- `web_search.query`
- `web_fetch.url`
- `web_fetch.goal`
- `retrieve.query`

工具输出：

- web search 返回历史兼容的格式化文本，文本内容来自活动 wrapper 的标准化搜索结果。
- 网页摘要或证据。
- 检索片段。

## 边界与错误处理

- 非白名单工具名不执行。
- retrieve 模式最多接受配置允许数量的 query。
- 工具返回错误时应作为工具结果处理，不应直接破坏搜索循环。
- web search 的 provider 选择由 framework 已注册的活动 wrapper 决定；DeepSearch adapter 负责文本格式归一化、缓存和日志。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/tools/test_web_search.py
uv run pytest tests/search_agent/test_run_action.py
```

如果改动搜索 API 适配，可运行：

```bash
uv run pytest tests/tools/search_api
```

## 相关文档

- [DeepSearch 搜索智能体总览](../search-agent.md)
- [Search Nodes](./search-nodes.md)
- [Search Index](./search-index.md)
