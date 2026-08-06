# 资料采集

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/research_collector/` 下的资料采集后处理能力，包括工具调用结果处理、证据 ID 生成、内容去重、compact evidence 构建和工具日志。

本文档不覆盖 `framework/openjiuwen/agent/collector_graph/` 中的图编排，也不覆盖搜索工具自身的实现。

## 功能目的

资料采集把搜索、本地检索和运行时工具返回的原始结果整理为后续报告生成可使用的证据。它负责过滤排除域名、保留来源身份、生成稳定 doc/source id、提取关键片段、构造 compact evidence。

## 可见行为

- web search、local search 和运行时 API 工具结果会被归一化为记录列表。
- `exclude_domains`、`exclude_url`、`exclude_titles` 三类排除约束仅作用于 Web 搜索结果（tavily/google/common 三条路径的统一入口），本地知识库检索（local search）结果不在其过滤范围内，行为不变。
- `exclude_domains` 按命中域名及其子域名过滤；`exclude_url` 按命中禁引链接过滤（归一化 host+path 精确匹配）；`exclude_titles` 按命中禁引标题的来源过滤（归一化精确匹配，或剥离枚举聚合站后缀后精确匹配），用于拦截同一文献在 Web 上的镜像变体。
- 每个文档或证据片段会获得稳定的 `doc_id` / `source_id`，用于后续引用、去重和 source store 回查。
- 采集阶段的说明性结构化字段遵循报告语言；搜索 `queries` 和 `next_queries` 不强制遵循报告语言，可以选择更容易召回权威证据的源语言或混合语言。

## 关键代码路径

- 工具调用处理：`openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`
- 证据结构：`openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`
- 工具日志：`openjiuwen_deepsearch/algorithm/research_collector/tool_log.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/collector.md`
- `openjiuwen_deepsearch/algorithm/prompts/collector_final.md`
- `openjiuwen_deepsearch/algorithm/prompts/collector_gen_query.md`
- `openjiuwen_deepsearch/algorithm/prompts/collector_supervisor.md`

主要测试：

- `tests/info_collector/algorithm/test_collector_function.py`
- `tests/info_collector/algorithm/test_collector_evidence.py`
- `tests/info_collector/algorithm/test_tool_log.py`
- `tests/info_collector/test_info_collector.py`
- `tests/info_collector/test_collector_execution_service.py`
- `tests/info_collector/test_collector_query_prompts.py`

## 核心流程

1. collector 接收计划步骤和可用工具字典。
2. LLM 选择工具后，`collector_function` 校验工具名并执行工具调用。
3. 工具返回结果按来源类型写入 web、本地或其他工具记录。
4. 证据层生成 `doc_id`、`source_id`、正文 hash 和 `content_ref`。
5. compact evidence 提取标题、URL、来源、关键片段、发布时间和评分。
6. 下游报告生成和溯源使用整理后的 `doc_infos`、source store 和搜索记录。

## 数据契约与依赖

关键输入：

- `agent_input.messages`
- `agent_input.web_page_search_record`
- `agent_input.local_text_search_record`
- `agent_input.other_tool_record`
- `research_intent.exclude_domains`
- `research_intent.exclude_url`
- `research_intent.exclude_titles`

关键输出：

- `doc_id`：文档级稳定 ID。
- `source_id`：证据片段级稳定 ID。
- `content_ref`：指向 source store 或 legacy doc info 的正文引用。
- `scores`：`authority`、`relevance`、`answerability`。

## 边界与错误处理

- 工具名不在 tool dict 中时跳过本次工具调用，不应伪造结果。
- 工具调用异常会记录错误并返回空结果，避免中断整个采集流程。
- URL 去重会移除常见跟踪参数，但不替换报告展示和引用使用的原始 URL。
- 敏感日志模式下不应输出完整网页正文、检索 query 或工具响应。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/info_collector/algorithm
uv run pytest tests/info_collector/test_info_collector.py
uv run pytest tests/info_collector/test_collector_execution_service.py
```

## 相关文档

- [查询理解](./query-understanding.md)
- [DeepSearch 搜索智能体](./search-agent.md)
- [报告生成](./report-generation.md)
- [全局溯源](./source-trace.md)
