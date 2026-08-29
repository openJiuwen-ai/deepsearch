# 查询理解

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/query_understanding/` 下的查询理解能力，包括意图识别、报告类型策略、初始搜索，以及专业版的大纲生成和章节研究计划生成。

本文档不覆盖后续资料采集、DeepSearch action 执行、报告正文生成和服务端 API 编排。

## 功能目的

查询理解用于把用户原始问题转换为后续研究流程可消费的结构化输入。它保留原始 query，同时抽取研究主题、语言、报告约束、包含或排除的 URL / 域名、章节数量、受众、语气、任务类型和可选时间范围，并生成报告大纲与每个章节的研究计划。

## 可见行为

- 意图识别会输出 `IntentRecognitionResult`，其中包含 `original_query`、`research_query`、`research_intent`、`lang`、`needs_clarification` 和可选入口搜索结果。`needs_clarification` 由 LLM 判断用户输入是否充足：当 `workflow_human_in_the_loop=True` 时，仅 `needs_clarification=True` 才进入问题澄清流程；`workflow_human_in_the_loop=False` 时忽略该字段，一律不澄清。LLM 未输出该字段或意图识别调用失败时，`needs_clarification` 默认为 `False`（不澄清）。
- 意图识别会提取用户指定的来源排除约束：文章级排除进入 `exclude_url`（链接）与 `exclude_titles`（标题，逐字提取，用于识别同文献镜像变体），站点级排除才进入 `exclude_domains`；禁引的 URL 即使同属一个域名也不得归纳为整域排除。提取结果非空时输出 `[EXCLUDE_INTENT]` 观测日志（敏感模式下只记字段计数）。
- 入口预搜索（web 模式）结果在写入 `search_context.entry_search_results` 前会按 `exclude_url`/`exclude_titles` 过滤（与本地知识库检索无关），过滤后的结果供大纲与问题生成消费；纯本地模式无入口预搜索，不受影响。
- 报告类型只接受明确的 `professional` 或 `brief`；未知值保持为空，由下游澄清或默认策略处理。
- API 已指定 `report_type`（`config.report_type` 非 `None`）时三层抑制：意图识别工具 schema 移除 `report_type` 字段、意图 Prompt 完全不渲染相关指令、意图识别节点用 API 值覆盖 LLM 意外输出；反馈轮重解析同样被抑制，锁定值不可被用户反馈覆盖。
- `brief` 只在意图识别后改变主图路由：它使用独立的 Brief 大纲和报告级证据工作流，不生成专业版 `Outline` 或章节 `Plan`。入口预搜索仍按当前搜索方式执行，但其结果不直接并入 Brief 证据集合。
- 大纲生成要求章节标题不带编号，并在代码侧修复章节 ID、依赖关系和 parent/relationship 一致性。
- 用户显式指定顶层结构时，大纲生成按用户给出的主要章节数量、标题和顺序组织，不为了默认章节数、brief 摘要或维度覆盖规则额外新增顶层章节。
- 计划生成按章节生成信息采集步骤，依赖驱动模式会保留 step id、parent ids 和关系描述。
- 用户明确提出资料或事实时间范围时，同一次意图识别 LLM 调用会输出 `source_date_scope` 和/或
  `content_date_scope` 两个可选子对象（同一 query 两类可并存）。`source_date_scope` 限制资料发表时间，
  `content_date_scope` 限制事实或数据时间；日期边界均为包含关系。
- “年初”“年中”“年底”分别归一化为 3 月 31 日、6 月 30 日和 12 月 31 日；“某年之前”“截至某年”及
  “某月之前”按意图 Prompt 约定归一化为包含边界。非法或不完整的时间对象只会降级为无时间约束，不丢失其他意图字段。
- 时间上下文不传入普通、依赖驱动、Hybrid、模板或用户修订 outliner，也不传入普通或依赖驱动 planner。
- 新生成的依赖驱动大纲与普通大纲采用相同的章节输出契约：每个章节都必须提供
  `format_requirements`、非空 `section_focus` 和非空 `focus_dimensions`。表格、精确列名及顺序、
  指定行、逐项枚举、篇幅/样式和来源限制写入 `format_requirements`，研究范围与依赖关系保留在
  `description`。没有章节级格式要求时显式使用空数组 `[]`。

## 关键代码路径

- 意图识别：`openjiuwen_deepsearch/algorithm/query_understanding/intent_recognition.py`
- 查询解释：`openjiuwen_deepsearch/algorithm/query_understanding/interpreter.py`
- 大纲生成：`openjiuwen_deepsearch/algorithm/query_understanding/outliner.py`
- 计划生成：`openjiuwen_deepsearch/algorithm/query_understanding/planner.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/intent_recognition.md`
- `openjiuwen_deepsearch/algorithm/prompts/query_rewrite.md`
- `openjiuwen_deepsearch/algorithm/prompts/outliner.md`
- `openjiuwen_deepsearch/algorithm/prompts/outliner_interaction.md`
- `openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner.md`
- `openjiuwen_deepsearch/algorithm/prompts/planner.md`
- `openjiuwen_deepsearch/algorithm/prompts/dep_driving_planner.md`

主要测试：

- `tests/algorithm/query_understanding/test_intent_recognition.py`
- `tests/algorithm/query_understanding/test_research_intent_contract.py`
- `tests/algorithm/query_understanding/test_report_type_policy.py`
- `tests/algorithm/query_understanding/test_outliner.py`
- `tests/algorithm/query_understanding/test_dependency_outliner.py`
- `tests/algorithm/query_understanding/test_planner.py`
- `tests/algorithm/query_understanding/test_dependency_planner.py`

## 核心流程

1. 意图识别读取用户原始输入，调用 Prompt 和 tool call 产出结构化报告约束。
2. 代码侧归一化 report type、task type、URL、域名、列表字段和 LLM 输出的时间对象；时间提取不使用正则 fallback。
3. 如果需要入口搜索，查询理解阶段可以执行初始网络搜索并把结果放入 intent 结果。入口 `research_query`、搜索请求和结果
   不应用时间范围；入口搜索完成后才为后续 collector 配置可安全下推的原生搜索开始日期参数。
4. `professional` 进入本模块的大纲生成，根据研究主题、报告约束和目标章节数生成 `Outline`；`brief` 转入 Brief 独立大纲节点。
5. 专业版大纲校验修复章节 ID、parent ids、relationships 和反向依赖。
6. 专业版计划生成按章节产出 `Plan`，作为后续资料采集步骤输入；Brief 的研究步骤位于其独立大纲中。

## 数据契约与依赖

关键输出契约：

- `ResearchIntent`：承载任务类型、分析维度、对比对象、章节数、受众、语气、报告类型、来源排除规则和可空 `source_date_scope`/`content_date_scope` 双时间约束（旧 `temporal_scope` 字段 deprecated，仅供旧 state 输入路由）。来源排除中，`include_url`/`exclude_url` 为链接级，`exclude_titles` 为文章标题级，`include_domains`/`exclude_domains` 为站点级；文章级排除只走链接与标题字段，不得派生为整域排除。
- `ResearchIntent.target_papers`：表达用户明确或隐式指定的目标论文约束。每项只包含 `title`、`pmid`、`doi`、`arxiv_id`、`dataset`、`data_year`、`topic` 七个可空字符串字段；显式标识与隐式指纹共用该结构，至少一个字段非空。意图识别只提取用户提供的信息，不搜索、不生成或持久化 `search_terms`。
- `target_papers.data_year` 是论文所用数据的年份，不等同于资料发表时间，也不得据此生成时间约束。目标论文的查询翻译、垂域路由与检索全部由 collector 执行。
- `TemporalScope`：`constraint_type` 为 `source_date` 或 `content_date`；`start_date` / `end_date` 为可空 ISO 日期，
  但至少存在一个边界，且开始日期不得晚于结束日期。意图 tool schema 提供两个可选子对象 `source_date_scope`/`content_date_scope`，均不含 `constraint_type`（类型由字段名隐含）；归一化由 `_normalize_date_scope` 按 kind 注入枚举构造 `TemporalScope`。
- 意图 tool schema 仅使用基础字段约束；模型输出会经过 `TemporalScope` 二次校验，非法或缺少日期边界的对象按既定兼容策略降级为空约束。
- `Outline` / `Section`：章节标题、描述、核心章节标记、section id、依赖关系和分析 focus。
- 历史 `Outline` / `Section` 缺少新章节契约字段时仍按模型默认值加载；必填约束仅作用于新生成的
  普通或依赖驱动 tool call。
- `Plan` / `Step`：章节研究步骤，依赖驱动模式下包含 step id 与依赖关系。
- Brief 的章节、研究步骤和证据契约见 [Brief 精简版报告工作流](./brief-report.md)，不属于本模块的 `Outline` / `Plan` 契约。

外部依赖：

- `llm_context` 中的查询理解相关模型。
- `runtime_api` 工具合并逻辑，用于允许查询理解阶段调用运行时工具。
- 初始 web search 配置和搜索引擎。

## 边界与错误处理

- LLM 输出的 JSON 或 tool call 参数必须经过代码侧解析和校验，不能直接信任。
- 章节标题不应包含编号；代码侧会清理和修复，但 Prompt 变量变化仍需同步测试。
- 无法修复的大纲依赖会降级清空依赖关系，避免后续图流程被非法依赖阻塞。
- 敏感日志模式下不应输出完整 query、Prompt 内容或 LLM 返回正文。
- 时间范围只参与查询理解和信息采集，不进入 outliner、planner、sub-report、Reporter 或最终报告 Prompt。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/algorithm/query_understanding
```

如果只改意图识别或报告类型策略，可优先运行：

```bash
uv run pytest tests/algorithm/query_understanding/test_intent_recognition.py
uv run pytest tests/algorithm/query_understanding/test_report_type_policy.py
uv run pytest tests/algorithm/query_understanding/test_research_intent_contract.py
```

## 相关文档

- [Prompt 模板系统](./prompt-template-system.md)
- [资料采集](./research-collector.md)
- [DeepSearch 搜索智能体](./search-agent.md)
- [报告生成](./report-generation.md)
- [Brief 精简版报告工作流](./brief-report.md)
