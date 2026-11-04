# 报告生成

## 维护范围

本文档覆盖专业版使用的 `openjiuwen_deepsearch/algorithm/report/` 下的报告生成能力，包括子报告生成、信息维度矩阵段落选择、候选文档预筛、Markdown 标题清理、表格标题归一、可视化 Mermaid 片段生成和最终报告拼接。

- [子报告生成](./report-generation/sub-report-generation.md)
- [信息维度矩阵段落选择](./report-generation/coverage-matrix-doc-selection.md)
- [候选文档预筛](./report-generation/doc-prefilter.md)
- [表格 Caption](./report-generation/table-caption.md)
- [Markdown 可视化](./report-generation/visualization-markdown.md)

## 功能目的

报告生成将查询理解和资料采集阶段产出的 outline、plans、doc infos 和 classified contents 转换为可展示的 Markdown 报告。它负责控制章节结构、分类资料输入、子报告生成、摘要/结论生成和部分可视化内容插入。

## 可见行为

- 检索不再提取或应用文献排除名单；候选资料仍按相关性、时间范围、去重和质量规则筛选。
- 不提供专门的禁引文献开关、来源黑名单提取、匹配或过滤功能。`Section.format_requirements` 仅保留非空字符串并清理首尾空白，不解释或删除指令内容；提示词不注入来源黑名单专项规则。用户原文仍作为普通任务上下文传给模型，代码不保证模型会忽略其中的来源偏好或限制。
- 最终 Markdown 不输出引用标记和来源 URL，也不生成参考文献章节。链接文字、图片和目录锚点保留。
- 报告完成后直接进入图表生成和用户反馈节点，无引用校验、引用溯源或溯源推理阶段；相关配置开关和算法已移除。

- 报告正文以 Markdown 输出，并清理标题编号和过深标题。
- 最终报告在标题与摘要之间生成一级目录；目录以无项目符号的独立链接行列出正文一级章节，不展开子标题，也不包含摘要、结论和参考文章。
- 报告生成时在正文每个一级章节标题之后插入独立 `<a id="chapter-N"></a>` HTML 锚点行（编号与目录 `#chapter-N` 链接一一对应），保证原生 Markdown 报告的目录可点击跳转；锚点位于 H1 行之后的独立行，不污染标题文本；导出层转换时会清理这些锚点并改用 `{#chapter-N}` 属性。
- 子报告根据章节计划和候选资料生成，失败时使用统一错误格式。
- 候选资料会先去重、按 step 分桶和按评分均衡筛选，再进入 LLM 分类。
- 表格 caption 会被标准化为稳定的“表 N”或英文对应格式，避免引用错位。
- 可视化抽取和单位归一化输出必须通过 schema 校验。
- 全文抽取阶段会将 fulltext 截断到 500 字符用于子大纲生成 Prompt；完整 fulltext 仍保留给子报告写作 Prompt。

## 关键代码路径

- 报告编排：`openjiuwen_deepsearch/algorithm/report/report.py`
- 子报告写作和后处理：`openjiuwen_deepsearch/algorithm/report/report_parts.py`
- 证据组织：`openjiuwen_deepsearch/algorithm/report/evidence.py`
- 输出清理：`openjiuwen_deepsearch/algorithm/report/report_utils.py` 的 `strip_report_source_links`
- 工作流连接与最终正文写回：`openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`、`workflow.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/rationale_generator/`
- `openjiuwen_deepsearch/algorithm/prompts/passages_extractor/`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_markdown/`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline/`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_sidecar/`
- `openjiuwen_deepsearch/algorithm/prompts/report_abstract_markdown/`
- `openjiuwen_deepsearch/algorithm/prompts/report_conclusion_markdown/`
- `openjiuwen_deepsearch/algorithm/prompts/report_implications_and_recommendations_markdown/`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_content/`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_normalize_units/`

主要测试：

- `tests/report/test_general_report.py`
- `tests/report/test_sub_report.py`
- `tests/report/test_doc_selection.py`
- `tests/report/test_report_rationale_fulltext.py`
- `tests/report/test_doc_selection_debug_export.py`
- `tests/report/test_step_summaries.py`
- `tests/report/test_doc_prefilter.py`
- `tests/report/test_chapter_sidecar.py`
- `tests/report/test_tools_in_report.py`
- `tests/report/test_evidence.py`
- `tests/report/test_markdown_utils.py`
- `tests/report/test_sub_section_outline.py`
- `tests/report/test_visualization.py`
- `tests/report/test_visualization_insertion.py`
- `tests/report/test_type_safety.py`

## 核心流程

1. Reporter 读取 outline、章节计划、采集结果和报告语言。
2. 候选资料通过 doc prefilter 去重、评分、分桶和批处理。
3. 信息维度矩阵段落选择：rationale 生成 → 段落抽取+评分（coverage 按 rationale 排序，reliability/data_density 按段落整体评估）→ 按 coverage 分 top-k 选择 → L1/L2 过滤 → URL 频次 top-10 全文抽取 → 覆盖校验（详见 [信息维度矩阵段落选择](./report-generation/coverage-matrix-doc-selection.md)）。段落选择只依据 coverage 分；reliability/data_density 虽被评估但不参与选文，仅用于可视化选取和 Prompt 证据增强。
4. 子报告 Prompt 根据章节契约、选中文档和历史上下文生成 Markdown。
5. 报告工具清理标题编号、规范化表格标题，并按报告类型生成摘要、结论或建议。
6. 可视化内容如需插入，先抽取结构化数据并校验 schema，再生成 Mermaid 或交给图表模块。

## 数据契约与依赖

关键输入：

- `Outline` 和 `Section`
- 章节 `Plan`
- `doc_infos` / `classified_content`
- `ChapterSidecar`
- `ResearchIntent` 和 section-local contract

关键输出：

- `response_content`：Markdown 报告正文。
- 子报告 sidecar：章节摘要、资料映射和局部契约。

## 边界与错误处理

- 子报告和总报告错误使用 `REPORT_GENERATE_ERROR` 或 `SUB_REPORT_GENERATE_ERROR` 格式化。
- 候选资料过多时必须经过 prefilter，避免把完整资料集合直接送入 Prompt。
- 可视化抽取输出不满足 schema 时应丢弃或降级，不应生成错误图表。
- 表格 caption 更新要避免破坏 Markdown table 和周边正文。
- 敏感日志模式下不应输出完整资料、报告正文或 Prompt 输入。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report
```

如果只改候选资料预筛，可运行：

```bash
uv run pytest tests/report/test_doc_prefilter.py
```

## 相关文档

- [子报告生成](./report-generation/sub-report-generation.md)
- [信息维度矩阵段落选择](./report-generation/coverage-matrix-doc-selection.md)
- [候选文档预筛](./report-generation/doc-prefilter.md)
- [表格 Caption](./report-generation/table-caption.md)
- [Markdown 可视化](./report-generation/visualization-markdown.md)
- [查询理解](./query-understanding.md)
- [资料采集](./research-collector.md)
- [图表生成](./chart-generation.md)
- [报告模板生成](./report-template.md)
- [Brief 精简版报告工作流](./brief-report.md)
