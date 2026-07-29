# Search Index

## 维护范围

本文档覆盖 DeepSearch 搜索智能体中的固定语料检索索引能力，包括索引创建、文本切分、tokenizer chunking、向量检索和 retriever 工具消费。

本文档不覆盖 web search/fetch 工具和 run action 状态机。

## 功能目的

Search index 用于让 DeepSearch 在固定语料或预构建网页集合上执行 retrieve-only 搜索。它将文档切分为可嵌入片段，建立检索结构，并供 retriever 工具按自然语言 query 返回相关片段。

## 可见行为

- 文本会按 splitter 或 tokenizer chunker 切分为片段。
- 索引工具负责构建或加载可检索结构。
- retriever 工具按 query 返回相关片段。
- retrieve-only 模式下 run action 只允许调用 `retrieve`。

## 关键代码路径

- 索引创建：`openjiuwen_deepsearch/algorithm/search_index/create_browsecompplus_index.py`
- 索引工具：`openjiuwen_deepsearch/algorithm/search_index/index_utils.py`
- 文本切分：`openjiuwen_deepsearch/algorithm/search_index/splitter.py`
- 文本切分：`openjiuwen_deepsearch/algorithm/search_index/text_splitter.py`
- tokenizer chunking：`openjiuwen_deepsearch/algorithm/search_index/tokenizer_chunker.py`
- retriever：`openjiuwen_deepsearch/algorithm/search_tools/retrieval/retriever.py`
- retriever tool：`openjiuwen_deepsearch/algorithm/search_tools/retriever_tool.py`

主要测试：

- `tests/search_agent/test_run_action.py`
- `tests/search_agent/test_config_matrix.py`
- `tests/search_agent/test_deep_search_agent_smoke.py`

## 核心流程

1. 文档或固定语料被切分为 chunk。
2. chunk 通过 embedder 转为向量或检索表示。
3. 索引被保存或加载。
4. retriever 工具接收 query。
5. 检索结果以片段形式返回 run action。
6. run action 用检索片段继续推理状态或答案。

## 数据契约与依赖

输入：

- 原始语料文本或预构建数据集。
- chunk size / tokenizer 配置。
- retrieve query。

输出：

- chunk 列表。
- 检索结果片段。

## 边界与错误处理

- chunk 过长会影响嵌入和上下文长度，应通过 splitter 控制。
- retrieve-only 模式不应暴露 web search/fetch。
- 索引不存在或加载失败时应在工具层返回明确错误。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/search_agent/test_run_action.py
uv run pytest tests/search_agent/test_config_matrix.py
```

## 相关文档

- [DeepSearch 搜索智能体总览](../search-agent.md)
- [Search Tools](./search-tools.md)
- [Search Nodes](./search-nodes.md)
