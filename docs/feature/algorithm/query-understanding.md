# 查询理解

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/query_understanding/` 下的查询理解能力，包括意图识别、报告类型策略、初始搜索、大纲生成和章节研究计划生成。

本文档不覆盖后续资料采集、DeepSearch action 执行、报告正文生成和服务端 API 编排。

## 功能目的

查询理解用于把用户原始问题转换为后续研究流程可消费的结构化输入。它保留原始 query，同时抽取研究主题、语言、报告约束、包含或排除的 URL / 域名、章节数量、受众、语气和任务类型，并生成报告大纲与每个章节的研究计划。

## 可见行为

- 意图识别会输出 `IntentRecognitionResult`，其中包含 `original_query`、`research_query`、`research_intent`、`lang` 和可选入口搜索结果。
- 报告类型只接受明确的 `professional` 或 `brief`；未知值保持为空，由下游澄清或默认策略处理。
- 大纲生成要求章节标题不带编号，并在代码侧修复章节 ID、依赖关系和 parent/relationship 一致性。
- 计划生成按章节生成信息采集步骤，依赖驱动模式会保留 step id、parent ids 和关系描述。

## 关键代码路径

- 意图识别：`openjiuwen_deepsearch/algorithm/query_understanding/intent_recognition.py`
- 查询解释：`openjiuwen_deepsearch/algorithm/query_understanding/interpreter.py`
- 大纲生成：`openjiuwen_deepsearch/algorithm/query_understanding/outliner.py`
- 计划生成：`openjiuwen_deepsearch/algorithm/query_understanding/planner.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/intent_recognition.md`
- `openjiuwen_deepsearch/algorithm/prompts/intent_recognition_entry.md`
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
2. 代码侧归一化 report type、task type、URL、域名和列表字段。
3. 如果需要入口搜索，查询理解阶段可以执行初始网络搜索并把结果放入 intent 结果。
4. 大纲生成根据研究主题、报告约束和目标章节数生成 `Outline`。
5. 大纲校验修复章节 ID、parent ids、relationships 和反向依赖。
6. 计划生成按章节产出 `Plan`，作为后续资料采集步骤输入。

## 数据契约与依赖

关键输出契约：

- `ResearchIntent`：承载任务类型、分析维度、对比对象、章节数、受众、语气、报告类型和域名过滤规则。
- `Outline` / `Section`：章节标题、描述、核心章节标记、section id、依赖关系和分析 focus。
- `Plan` / `Step`：章节研究步骤，依赖驱动模式下包含 step id 与依赖关系。

外部依赖：

- `llm_context` 中的查询理解相关模型。
- `runtime_api` 工具合并逻辑，用于允许查询理解阶段调用运行时工具。
- 初始 web search 配置和搜索引擎。

## 边界与错误处理

- LLM 输出的 JSON 或 tool call 参数必须经过代码侧解析和校验，不能直接信任。
- 章节标题不应包含编号；代码侧会清理和修复，但 Prompt 变量变化仍需同步测试。
- 无法修复的大纲依赖会降级清空依赖关系，避免后续图流程被非法依赖阻塞。
- 敏感日志模式下不应输出完整 query、Prompt 内容或 LLM 返回正文。

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
