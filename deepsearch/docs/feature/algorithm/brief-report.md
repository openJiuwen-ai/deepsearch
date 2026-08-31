# Brief 精简版报告工作流

## 维护范围

本文档覆盖 `report_type=brief` 在研究主图中的独立分支：精简大纲、报告级搜索与证据评估、证据审阅、一次补搜、并行章节写作、核心摘要、受控 Mermaid 插图和最终引用校验。

本文档不覆盖专业版的章节计划、EditorTeam、依赖驱动编辑团队、VLM 图表生成、推理链溯源和报告后用户反馈；这些能力不进入 Brief 分支。

## 功能目的

Brief 面向需要快速获得有引用、可决策结论的场景。它以报告级搜索替代专业版逐章节研究子图，减少调用次数和长上下文传递，同时保持用户约束、搜索结果、正文和最终引用之间的可追溯关系。

用户可在请求或澄清反馈中明确指定“精简版”或 `brief`。报告类型由意图识别归一化为 `research_intent.report_type`，不是一个新增的运行配置项。当用户原始 Query 未明示且澄清环节也未选择时，`resolve_report_type_policy(None)` 默认解析为 `brief`，路由进入 Brief 分支；用户明确选择 `professional` 时才进入专业版主链。

## 可见行为

- Brief 与专业版共用意图识别和入口预搜索。入口预搜索结果用于既有查询理解/澄清流程，但**不会**写入 Brief 的首轮证据集合。
- Brief 大纲的目标为 3～5 个章节；运行时会清洗无效内容，并接受至少 2 个有效章节作为兼容降级。每章包含 2～4 个可验证研究步骤。
- 首轮由 LLM 生成报告级 Query，按 web/local 搜索方式并发检索；只使用搜索接口已返回的标题、URL、来源、时间和摘要/片段，不抓取网页正文。
- 搜索结果按 URL 和近似镜像去重，应用用户的 URL、标题、域名和时间范围限制；同一来源 URL 在全报告中只注册一个引用编号。
- 每个章节独立、并行评估候选证据和研究步骤覆盖状态。评估失败只降级对应章节，不会中断其他章节。
- 首轮评估后，证据审阅节点只生成内部写作策略和分章写作指引；它不修改大纲、章节 ID、研究步骤或证据。只有审阅确认的阻断缺口才会触发补搜，且整份 Brief 最多补搜一次；补搜后直接写作，不再二次审阅。
- 各章节并行写作；正文正常只发起一次生成调用，但既有重试和上下文缩减会在空响应、格式错误或上下文超限时重试。核心摘要是一个独立生成阶段，不生成专业版的章节过渡或独立结论。
- 正文写作不输出 Mermaid。`visualization_enable=True` 时，后续受控 Mermaid 阶段复用专业版的图表提取、合规校验和插入能力；图表失败只保留原章节。Brief 不进入 `VLMChartGeneratorNode`。
- 最终使用共享 `SourceTracerNode` 做引用校验和前端 citation 数据整理，但该节点的下一跳是 `End`，不会进入 `SourceTracerInferNode` 或报告后用户反馈链。

## 关键代码路径

- 编排与节点：`openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`、`openjiuwen_deepsearch/framework/openjiuwen/agent/brief_nodes.py`
- 领域实现：`openjiuwen_deepsearch/algorithm/brief_report/`
- 入口路由：`openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- 模型槽位：`openjiuwen_deepsearch/framework/openjiuwen/llm/llm_adapter.py`
- 数据状态：`openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`

相关 Prompt：

- `brief_outliner.md`
- `brief_collector_query_generation.md`
- `brief_doc_evaluator.md`
- `brief_evidence_review.md`
- `brief_sub_reporter.md`
- `brief_reporter.md`

主要测试：`tests/brief_report/`、`tests/source_tracer/test_extract_message_prompt.py`。

## 核心流程

```text
IntentRecognition / 可选澄清
  → BriefOutline
  → BriefInfoCollector（首轮：Query → 搜索 → 分章评估）
  → BriefEvidenceReviewer
      ├─ 无阻断缺口 → BriefSubReporter（并行章节写作）
      └─ 有阻断缺口 → BriefInfoCollector（唯一一次补搜）
                           → BriefSubReporter
  → BriefReporter（核心摘要）
  → BriefMermaidGenerator（可选）
  → BriefSourceTracer
  → End
```

1. 意图识别生成 `ResearchIntent`、语言和报告类型策略；web/all 搜索方式仍执行共享入口预搜索。
2. `BriefOutlineNode` 根据用户请求、意图、时间范围、模板、受众和语气生成精简大纲。
3. `BriefInfoCollectorNode` 首轮生成合法且未执行过的 Query，调用已配置的 web/local 搜索接口，过滤、去重并按章节路由候选。
4. `BriefDocEvaluator` 并行选择每章最小证据集，输出已选来源及每个步骤的 `covered`、`weak`、`missing` 或 `unknown` 覆盖状态。上下文超限时先拆分候选；无法恢复时仅该章使用确定性降级。
5. `BriefEvidenceReviewer` 校验 LLM 审阅结果，只保留现有章节/步骤上的有效阻断缺口。审阅调用失败时，从首轮评估结果确定性提取阻断缺口。
6. 如需补搜，使用首轮已执行 Query 去重，只重评受补搜 Query 影响的章节；补搜 Query 为空时保留首轮证据并直接写作。
7. 章节写作从最终证据中按覆盖状态和评估排名组装上下文；摘要仅消费实际保留的章节文本及其可见引用。
8. 最终装配报告后，引用校验将内部 `[citation:N]` 处理为对外报告和 citation messages。

## 数据契约与依赖

- `SearchContext.brief_state` 保存 `BriefWorkflowState`，其主要字段为 `outline`、`collection`、`collection_context`、`evidence_review`、`chapters` 和 `executive_summary`。它不复用专业版的 `current_outline` 或章节 Plan。
- `BriefCollectionContext` 仅保存运行期的 `executed_queries` 与规范化 `search_results`，供审阅后的唯一一次补搜使用。
- `BriefCollectionResult.section_evidence` 按章节保存 `selected_docs` 和步骤覆盖；`citation_registry` 按规范化 URL 注册 `index/title/url/original_content`。
- `BriefWritingGuidance` 是审阅产生的内部编辑指引，不是事实或引用来源。章节可获得总策略和本章指引；核心摘要只获得总策略。
- `source_id` 用于搜索、评估和章节路由；`citation` 编号只来自最终引用注册表。正文和摘要只能保留实际传入证据对应的引用编号。
- 不新增 LLM、Brief 或 Token 配置。Brief 复用 `plan_understanding`（大纲）、`info_collecting`（Query、评估、审阅）和 `writing_checking`（章节、摘要）槽位；槽位缺失时沿用既有 `general` 回退。
- `execution_method=parallel`、`dependency_driving` 或 `hybrid` 不改变 Brief 的后续节点链。hybrid 仍会在入口计算大纲执行方式，但 Brief 不消费该结果。

## 边界与错误处理

- Brief 不生成或修改专业版 Outline/Plan，不进入 OutlineInteraction、EditorTeam、DependencyEditorTeam、VLM 图表、推理链溯源或报告后反馈处理。
- 补搜次数固定为一次，但 Query 数量不使用“最多四条”的 Brief 专用硬编码；合法 Query 的生成、重试和去重使用既有信息采集重试配置。
- Brief 不读取或预估模型上下文窗口。各阶段先发送完整当前输入；仅在模型实际返回上下文超限时，评估递归拆分候选、章节缩减证据、摘要逐级压缩。
- 章节写作在既有重试耗尽后会记录该章失败并排除其正文；其他并行章节继续完成。摘要无法继续压缩时使用既有章节首段降级，而不伪造失败章节正文。
- 搜索 Query、大纲和审阅结果均在代码侧校验；模型生成未知章节、步骤、来源或重复 Query 时会被删除，而不会进入搜索或写作上下文。
- `source_tracer_research_trace_source_switch=False` 时最终溯源直接跳过并保留装配后的报告；图表生成或插入失败时不会中断 Brief。

## 测试与验证

```bash
uv run pytest tests/brief_report
uv run pytest tests/source_tracer/test_extract_message_prompt.py
```

修改 Brief 与专业版共享的 Mermaid 或引用校验逻辑时，还应运行对应的 `tests/report/` 或 `tests/source_tracer/` 测试。

## 相关文档

- [查询理解](./query-understanding.md)
- [报告研究主工作流](../framework/research-workflow.md)
- [搜索上下文与数据契约](../framework/search-context.md)
- [Agent 与服务运行配置](../config/agent-and-service-config.md)
- [报告生成](./report-generation.md)
- [全局溯源](./source-trace.md)
- [图表生成](./chart-generation.md)
