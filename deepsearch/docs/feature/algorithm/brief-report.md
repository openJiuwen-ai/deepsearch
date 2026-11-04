# Brief 精简版报告工作流

## 维护范围

## 功能目的

Brief 面向需要快速获得有引用、可决策结论的场景。它以报告级搜索替代专业版逐章节研究子图，减少调用次数和长上下文传递，同时保持用户约束、搜索结果、正文和最终引用之间的可追溯关系。

报告类型有三种来源：`/run` 请求的 `report_type` 入参（或 `/run_brief` 薄封装，强制 `brief`）、用户澄清反馈中明确指定的“精简版”，以及意图识别从原始问题中的判定。API 指定值优先级最高：意图识别不再判定类型，反馈不可覆盖，且仅首轮生效、会话内不可变；未指定时保持原有判定与澄清机制。

## 可见行为

- Brief 与专业版共用意图识别和入口预搜索。入口预搜索结果用于既有查询理解/澄清流程，但**不会**写入 Brief 的首轮证据集合。
- Brief 大纲的目标为 3～5 个章节；运行时会清洗无效内容，并接受至少 2 个有效章节作为兼容降级。每章包含 2～4 个可验证研究步骤。
- 首轮由 LLM 生成报告级 Query，按 web/local 搜索方式并发检索；只使用搜索接口已返回的标题、URL、来源、时间和摘要/片段，不抓取网页正文。
- 搜索结果按 URL 和近似镜像去重，应用时间范围限制；同一来源 URL 在全报告中只注册一个内部证据编号。
- 每个章节独立、并行评估候选证据和研究步骤覆盖状态。评估失败只降级对应章节，不会中断其他章节。
- 首轮评估后，证据审阅节点只生成内部写作策略和分章写作指引；它不修改大纲、章节 ID、研究步骤或证据。只有审阅确认的阻断缺口才会触发补搜，且整份 Brief 最多补搜一次；补搜后直接写作，不再二次审阅。
- 各章节并行写作；正文正常只发起一次生成调用，但既有重试和上下文缩减会在空响应、格式错误或上下文超限时重试。核心摘要是一个独立生成阶段，不生成专业版的章节过渡或独立结论。
- 正文写作不输出 Mermaid；Brief 不进入 `VLMChartGeneratorNode`。`visualization_enable` 对 Brief 不生效，仅控制专业版报告的既有图表流程。

## 关键代码路径

- 编排与节点：`openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`、`openjiuwen_deepsearch/framework/openjiuwen/agent/brief_nodes.py`
- 领域实现：`openjiuwen_deepsearch/algorithm/brief_report/`
- 入口路由：`openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- 模型槽位：`openjiuwen_deepsearch/framework/openjiuwen/llm/llm_adapter.py`
- 数据状态：`openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`

相关 Prompt：

- `brief_outliner/`
- `brief_collector_query_generation/`
- `brief_doc_evaluator/`
- `brief_evidence_review/`
- `brief_sub_reporter/`
- `brief_reporter/`
- `brief_html_reporter/`
- `brief_html_common/`
- `brief_html_section/`

## 核心流程

`BRIEF_REPORTER` 调用 LLM 生成核心摘要后，拼装标题、核心摘要和各章，清除来源 URL 和引用标记，不追加参考文献。随后直接进入 `BRIEF_HTML_REPORTER`，转为自包含 HTML。

`visualization_enable` 对 Brief 不生效，仅控制专业版报告的图表流程。

1. 意图识别生成 `ResearchIntent`、语言和报告类型策略；web/all 搜索方式仍执行共享入口预搜索。
2. `BriefOutlineNode` 根据用户请求、意图、时间范围、模板、受众和语气生成精简大纲。
3. `BriefInfoCollectorNode` 首轮生成合法且未执行过的 Query，调用已配置的 web/local 搜索接口，过滤、去重并按章节路由候选。
4. `BriefDocEvaluator` 并行选择每章最小证据集，输出已选来源及每个步骤的 `covered`、`weak`、`missing` 或 `unknown` 覆盖状态。上下文超限时先拆分候选；无法恢复时仅该章使用确定性降级。
5. `BriefEvidenceReviewer` 校验 LLM 审阅结果，只保留现有章节/步骤上的有效阻断缺口。审阅调用失败时，从首轮评估结果确定性提取阻断缺口。
6. 如需补搜，使用首轮已执行 Query 去重，只重评受补搜 Query 影响的章节；补搜 Query 为空时保留首轮证据并直接写作。
7. 章节写作从最终证据中按覆盖状态和评估排名组装上下文；摘要仅消费实际保留的章节文本及其可见引用。

## 数据契约与依赖

- `SearchContext.brief_state` 保存 `BriefWorkflowState`，其主要字段为 `outline`、`collection`、`collection_context`、`evidence_review`、`chapters` 和 `executive_summary`。它不复用专业版的 `current_outline` 或章节 Plan。
- `BriefCollectionContext` 仅保存运行期的 `executed_queries` 与规范化 `search_results`，供审阅后的唯一一次补搜使用。
- `BriefCollectionResult.section_evidence` 按章节保存 `selected_docs` 和步骤覆盖；`citation_registry` 按规范化 URL 注册 `index/title/url/original_content`。
- `BriefWritingGuidance` 是审阅产生的内部编辑指引，不是事实或引用来源。章节可获得总策略和本章指引；核心摘要只获得总策略。
- `source_id` 用于搜索、评估和章节路由；`citation` 编号只来自最终引用注册表。正文和摘要只能保留实际传入证据对应的引用编号。
- 不新增 LLM、Brief 或 Token 配置。Brief 复用 `plan_understanding`（大纲）、`info_collecting`（Query、评估、审阅）和 `writing_checking`（章节、摘要）槽位；槽位缺失时沿用既有 `general` 回退。
- `execution_method=parallel`、`dependency_driving` 或 `hybrid` 不改变 Brief 的后续节点链。hybrid 仍会在入口计算大纲执行方式，但 Brief 不消费该结果。

## 边界与错误处理

- 补搜次数固定为一次，但 Query 数量不使用“最多四条”的 Brief 专用硬编码；合法 Query 的生成、重试和去重使用既有信息采集重试配置。
- Brief 不读取或预估模型上下文窗口。各阶段先发送完整当前输入；仅在模型实际返回上下文超限时，评估递归拆分候选、章节缩减证据、摘要逐级压缩。
- 章节写作在既有重试耗尽后会记录该章失败并排除其正文；其他并行章节继续完成。摘要无法继续压缩时使用既有章节首段降级，而不伪造失败章节正文。
- 搜索 Query、大纲和审阅结果均在代码侧校验；模型生成未知章节、步骤、来源或重复 Query 时会被删除，而不会进入搜索或写作上下文。

## 测试与验证

## 相关文档

- [查询理解](./query-understanding.md)
- [用户素材](./user-materials.md)
- [报告研究主工作流](../framework/research-workflow.md)
- [搜索上下文与数据契约](../framework/search-context.md)
- [Agent 与服务运行配置](../config/agent-and-service-config.md)
- [Brief 大纲升级专业版报告](../framework/brief-outline-upgrade.md)
- [报告生成](./report-generation.md)
- [图表生成](./chart-generation.md)
