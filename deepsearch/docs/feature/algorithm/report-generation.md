# 报告生成

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/report/` 下的报告生成能力，包括子报告生成、信息维度矩阵段落选择、候选文档预筛、Markdown 标题清理、表格标题归一、可视化 Mermaid 片段生成和最终报告拼接。

本文档不覆盖报告模板上传解析、服务端报告格式转换、全局溯源后处理和 VLM 图表文件生成。子能力细节见：

- [子报告生成](./report-generation/sub-report-generation.md)
- [信息维度矩阵段落选择](./report-generation/coverage-matrix-doc-selection.md)
- [候选文档预筛](./report-generation/doc-prefilter.md)
- [表格 Caption](./report-generation/table-caption.md)
- [Markdown 可视化](./report-generation/visualization-markdown.md)

## 功能目的

报告生成将查询理解和资料采集阶段产出的 outline、plans、doc infos 和 classified contents 转换为可展示的 Markdown 报告。它负责控制章节结构、分类资料输入、子报告生成、摘要/结论/参考资料生成和部分可视化内容插入。

## 可见行为

- 报告正文以 Markdown 输出，并清理标题编号和过深标题。
- 子报告根据章节计划和候选资料生成，失败时使用统一错误格式。
- 候选资料会先去重、按 step 分桶和按评分均衡筛选，再进入 LLM 分类。
- 表格 caption 会被标准化为稳定的“表 N”或英文对应格式，避免引用错位。
- 可视化抽取和单位归一化输出必须通过 schema 校验。

## 关键代码路径

- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`
- 报告配置：`openjiuwen_deepsearch/algorithm/report/config.py`
- 文档预筛：`openjiuwen_deepsearch/algorithm/report/doc_prefilter.py`
- compact doc info：`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py`
- 报告工具：`openjiuwen_deepsearch/algorithm/report/report_utils.py`
- 表格 caption：`openjiuwen_deepsearch/algorithm/report/table_caption_utils.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/rationale_generator.md`
- `openjiuwen_deepsearch/algorithm/prompts/passages_extractor.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_brief_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_sidecar.md`
- `openjiuwen_deepsearch/algorithm/prompts/report_abstract_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/report_conclusion_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/report_implications_and_recommendations_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_content.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_normalize_units.md`

主要测试：

- `tests/report/test_general_report.py`
- `tests/report/test_sub_report.py`
- `tests/report/test_doc_selection.py`
- `tests/report/test_step_summaries.py`
- `tests/report/test_doc_prefilter.py`
- `tests/report/test_chapter_sidecar.py`
- `tests/report/test_tools_in_report.py`

## 核心流程

1. Reporter 读取 outline、章节计划、采集结果和报告语言。
2. 候选资料通过 doc prefilter 去重、评分、分桶和批处理。
3. 信息维度矩阵段落选择：rationale 生成 → 段落抽取+3 维度评分（coverage 0.8 / reliability 0.1 / data_density 0.1）→ 按 rationale top-k 选择 → 覆盖校验（详见 [信息维度矩阵段落选择](./report-generation/coverage-matrix-doc-selection.md)）。
4. 子报告 Prompt 根据章节契约、选中文档和历史上下文生成 Markdown。
5. 报告工具清理标题编号、规范化表格标题，并按报告类型生成摘要、结论或建议。
6. 可视化内容如需插入，先抽取结构化数据并校验 schema，再生成 Mermaid 或交给图表模块。
7. 最终报告、classified contents、sidecar 和引用相关元数据交给后续溯源和前端展示。

## 数据契约与依赖

关键输入：

- `Outline` 和 `Section`
- 章节 `Plan`
- `doc_infos` / `classified_content`
- `ChapterSidecar`
- `ResearchIntent` 和 section-local contract

关键输出：

- `response_content`：Markdown 报告正文。
- `all_classified_contents`：供溯源、用户反馈和图表生成复用的资料集合。
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
- [全局溯源](./source-trace.md)
- [图表生成](./chart-generation.md)
- [报告模板生成](./report-template.md)
