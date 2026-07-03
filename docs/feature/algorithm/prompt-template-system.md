# Prompt 模板系统

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/prompts/` 下的 Prompt 模板资源和 `template.py` 的加载能力，包括文本 Prompt、系统消息构造、VLM 消息构造和 Prompt 变量契约。

本文档不逐个解释 Prompt 全文。具体业务 Prompt 的输入输出约定由对应 feature 文档维护。

## 功能目的

Prompt 模板系统为 algorithm 层各功能提供统一的 Prompt 文件加载和变量替换能力。它让查询理解、资料采集、DeepSearch、报告生成、溯源、图表和用户反馈处理都能通过文件名引用 Prompt，而不是在代码中内联长提示词。

## 可见行为

- 调用方通过 Prompt 文件名加载 `algorithm/prompts/` 下的 Markdown 模板。
- 模板变量来自 `context_vars`，渲染后组装为 LLM messages。
- 普通文本 Prompt 使用 system/user message 结构。
- VLM Prompt 会把图片 base64 列表放入多模态消息内容。
- Prompt 修改属于行为变更，需要同步变量契约、解析逻辑、测试和 feature 文档。

## 关键代码路径

- Prompt 加载：`openjiuwen_deepsearch/algorithm/prompts/template.py`
- Prompt 文件目录：`openjiuwen_deepsearch/algorithm/prompts/`

主要使用方：

- 查询理解：`openjiuwen_deepsearch/algorithm/query_understanding/`
- 资料采集：`openjiuwen_deepsearch/algorithm/research_collector/`
- DeepSearch：`openjiuwen_deepsearch/algorithm/search_nodes/`
- 报告生成：`openjiuwen_deepsearch/algorithm/report/`
- 报告模板：`openjiuwen_deepsearch/algorithm/report_template/`
- 全局溯源：`openjiuwen_deepsearch/algorithm/source_trace/`
- 推理链溯源：`openjiuwen_deepsearch/algorithm/source_tracer_infer/`
- 图表生成：`openjiuwen_deepsearch/algorithm/chart_generation/`
- 用户反馈处理：`openjiuwen_deepsearch/algorithm/user_feedback_processor/`

主要测试：

- 各业务域测试分别覆盖对应 Prompt 契约。
- Prompt 模板加载变更应至少运行受影响业务域的 targeted tests。

## 核心流程

1. 调用方传入 Prompt 文件名和 `context_vars`。
2. `template.py` 读取对应 Markdown Prompt。
3. 模板变量被替换为当前上下文。
4. 普通 Prompt 组装为 LLM message 列表。
5. VLM Prompt 额外拼接图片 base64。
6. 调用方执行 LLM，并按各自 feature 文档中的契约解析输出。

## 数据契约与依赖

Prompt 文件名不带 `.md` 时由调用方和模板系统约定解析。`context_vars` 的 key 必须与 Prompt 中引用的变量一致。

Prompt 契约至少包含：

- 输入变量名称和含义。
- 输出格式，尤其是 JSON、tool call、Markdown 或纯文本片段。
- 是否允许空输出。
- 解析失败时的 fallback 或重试策略。

## 边界与错误处理

- 不应在 feature 文档复制 Prompt 全文，只记录变量和输出契约。
- Prompt 文件重命名会影响所有通过名称引用的调用点。
- Prompt 输出格式变化必须同步解析代码和测试。
- 多模态 Prompt 必须保证图片 base64 列表和文本内容结构符合模型适配层要求。
- 敏感日志模式下不应输出完整 Prompt、用户输入或模型返回正文。

## 测试与验证

Prompt 系统本身没有独立的大量测试入口。修改 `template.py` 或跨域 Prompt 加载规则时，建议运行：

```bash
uv run pytest tests/algorithm/query_understanding
uv run pytest tests/report
uv run pytest tests/source_tracer
uv run pytest tests/user_feedback_processor
```

如果只修改某个 Prompt 文件，运行对应 feature 文档中列出的测试。

## 相关文档

- [查询理解](./query-understanding.md)
- [资料采集](./research-collector.md)
- [DeepSearch 搜索智能体](./search-agent.md)
- [报告生成](./report-generation.md)
- [报告模板生成](./report-template.md)
- [全局溯源](./source-trace.md)
- [推理链溯源](./source-tracer-infer.md)
- [图表生成](./chart-generation.md)
- [用户反馈处理](./user-feedback-processor.md)
