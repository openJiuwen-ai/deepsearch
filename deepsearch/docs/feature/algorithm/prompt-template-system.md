# Prompt 模板系统

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/prompts/` 下的 Prompt 模板资源、DeepResearch 的 `message_builder.py` 及兼容旧搜索链路的 `template.py`，包括文本 Prompt、系统消息构造、VLM 消息构造和 Prompt 变量契约。

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
- DeepResearch 消息构造：`openjiuwen_deepsearch/algorithm/prompts/message_builder.py`
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

DeepResearch 调用方传入模板目录名、context 和消息选项。`message_builder.py` 读取静态 `system.md` 与包含运行时变量的 `user.md`，按 system、历史消息、当前 user 的顺序组装；图片附加到当前 user 消息。system 不允许运行时变量。旧搜索链路继续通过 `template.py` 加载单文件模板。调用方执行 LLM 后，按业务契约解析输出。

## 数据契约与依赖

DeepResearch 以模板目录名引用 Prompt，目录内分别放置 `system.md` 和 `user.md`；兼容旧搜索链路的单文件模板仍以文件名解析。`context_vars` 的 key 必须与 Prompt 中引用的变量一致。

Prompt 契约至少包含：

- 输入变量名称和含义。
- 输出格式，尤其是 JSON、tool call、Markdown 或纯文本片段。
- 是否允许空输出。
- 解析失败时的 fallback 或重试策略。

研究任务相关边界：

- 交互式大纲的 `max_section_num` 是章节数上限，不是要求补足的目标；依赖式初始大纲的 `section_num` 是目标章节数。
- 普通初始大纲在用户未指定章节结构时匹配 `section_num`，用户明确指定结构时遵循用户要求；没有用户反馈时不渲染反馈块。用户改稿后的大纲标题、描述和思考字段使用指定语言。
- 网页证据压缩的 `max_content_length` 限制 `original_content` 字符数，不限制整个 JSON 输出长度。
- 两种章节规划器显式接收 `report_task`（最终总报告任务）、`original_query`（原始请求）、`section_task`（当前章节）和 `section_description`。章节任务为空时兼容使用 `query`；字段分别成行，不能用章节标题替代总任务。
- 模板大纲只以 `reference_report_template` 标签内的内容为模板，原始请求、反馈、检索结果属于独立上下文。
- API 已指定报告类型时，意图解析不输出 `report_type`；只有未指定时才从请求/澄清反馈提取。
- collector 在本地证据足够时不再查网页；不足或本地工具不可用时，可使用可用的网页工具。检索监督遵守时间边界、阻塞证据缺口和查询上限，不为了填满数量或来源多样性继续搜索。

章节正文与章节大纲的重试边界：LLM 请求调用抛异常时可以复用相同消息；调用已返回但内容为空、内容校验失败或后处理抛异常时，下一轮重新构建带失败反馈的消息。正文使用受控错误字段，不将原始服务异常文本放进提示词。这一约定不改变 planner 的重试机制。

## 边界与错误处理

- 不应在 feature 文档复制 Prompt 全文，只记录变量和输出契约。
- Prompt 文件重命名会影响所有通过名称引用的调用点。
- 目录模板构建失败时保留现有 Prompt 错误码；缺失模板错误指出实际的 `system.md` 或 include 路径，其他构建错误指出模板目录或具体文件，保留键错误会列出冲突键。兼容旧链路的单文件模板仍使用原有 `.md` 错误文案。
- Prompt 输出格式变化必须同步解析代码和测试。
- 多模态 Prompt 必须保证图片 base64 列表和文本内容结构符合模型适配层要求。
- 敏感日志模式下不应输出完整 Prompt、用户输入或模型返回正文。

## 测试与验证

修改 Prompt 加载规则或研究任务字段契约时，运行对应模板契约与业务测试：

```bash
uv run pytest tests/algorithm/prompts
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
