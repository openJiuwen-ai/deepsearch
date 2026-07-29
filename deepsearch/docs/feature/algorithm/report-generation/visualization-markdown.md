# Markdown 可视化

## 维护范围

本文档覆盖报告生成中的 Markdown 可视化能力，包括从子章节内容抽取可视化数据、校验抽取 schema、单位归一化和生成 Mermaid 图表片段。

本文档不覆盖 VLM 图表图片生成和 `#insertChart` 占位符插入。

## 功能目的

Markdown 可视化用于在报告正文中以 Mermaid 等文本图表形式表达结构化数据。它比 VLM 图表模块更轻量，直接在 Markdown 报告生成阶段工作。

## 可见行为

- 可视化抽取输出必须包含标题、图表类型、records 和单位信息。
- 支持 bar、line、pie、timeline 等类型。
- 非 timeline 图表必须有明确且不混合的单位。
- 单位归一化输出必须通过 schema 校验后才能生成图表。
- 当主流程已生成的可视化不足以覆盖数据密集内容时，报告生成会从已生成的子报告正文中选择适合可视化的候选块；真正用于 LLM 抽取和溯源校验的 `origin_content` 来自候选块 citation 对应的 `classified_content` 原始资料。
- 同一章节可以插入多张 Mermaid 图表，但每张图表必须表达一个独立且可追溯的数据集，避免把同一组 records 换一种图型重复表达。
- 插入到报告正文的 Mermaid 图表会带有系统管理的居中图题；图题引用优先使用图表候选文本中出现的完整 citation 集合，避免跨来源图表只归因到首个来源。

## 性能边界

Markdown 可视化会触发多轮 LLM 调用，因此需要明确控制报告生成尾延迟。主流程仍优先从检索后的 `classified_content` 中抽取图表；只有当主流程图表不足、而已生成的子报告正文暴露出新的数据密集候选时，才启动正文补图流程。

正文补图在单个章节内串行执行，不会按候选块并发扇出。当前边界由 `report.py` 中的常量控制：

- 每个章节最多选择 6 个正文候选块。
- 每个候选块最多生成 3 张互不重复的图表。
- 每个章节最终最多保留 8 张 Mermaid 图表。
- 每个章节正文补图最多触发 6 次完整 `_process_visualization_task`。一次完整任务内部可能包含数据抽取、可追溯性校验、合规校验、单位归一化和 Mermaid 生成等多轮 LLM 重试；这个任务预算用于避免数值密集章节在报告正文生成后继续拉长尾延迟。

如果上游报告生成同时处理多个章节，实际同时运行的补图任务数量受上游章节并发策略约束；正文补图自身不会再引入新的候选级并发。

## 关键代码路径

- 报告工具：`openjiuwen_deepsearch/algorithm/report/report_utils.py`
- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_content.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_normalize_units.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_mermaid.md`
- `openjiuwen_deepsearch/algorithm/prompts/insert_visualization.md`
- `openjiuwen_deepsearch/algorithm/prompts/chart_compliance_validate.md`

主要测试：

- `tests/report/test_general_report.py`
- `tests/report/test_tools_in_report.py`
- `tests/report/test_sub_report.py`
- `tests/algorithm/report_export/test_mermaid_renderer.py`

## 核心流程

1. 报告生成阶段识别适合可视化的章节内容。
2. LLM 抽取图表标题、类型、records 和单位。
3. 抽取结果通过 schema 校验。
4. 对需要数值单位的图表执行单位归一化。
5. 根据图表类型生成 Mermaid 片段。
6. 合规校验确认 Mermaid 语法、图表类型、数据一致性、可读性和引用上下文满足要求。
7. Mermaid 片段插入报告正文，并在图题中保留对应 citation。

## 数据契约与依赖

抽取输出：

- `image_title`
- `image_type`
- `records`

生成后的可视化项：

- `sub_section_visualization_content`：抽取后的图表 JSON 字符串。
- `mermaid_content`：可插入 Markdown 的 Mermaid 片段。
- `index`：兼容单一引用场景的首个 citation 编号。
- `citation_indices`：图表候选正文中出现的去重 citation 编号列表；渲染图题时优先使用该字段。

归一化输出：

- `unit`
- `records`

## 边界与错误处理

- 混合单位、空 records、非有限数值都应被拒绝。
- pie 图不允许负数。
- timeline 不要求数值单位。
- schema 不通过时应跳过可视化，不应生成错误图表。
- 报告正文补图流程只使用 LLM 从原始资料抽取且校验通过的数据，不通过本地正则从正文硬抽图表数据，也不把模型生成的草稿正文作为图表数据的真实性来源。
- 如果章节文本缺少足够可视化数据或 LLM 抽取、单位归一化、Mermaid 生成、合规校验失败，应跳过该候选图表；已有有效可视化结果不应被清空。
- 非 timeline 图表的 `unit_string` 不应包含混合单位分隔符，例如 `或`、`/`、`|`、`,`、`;` 或 ` and `，以保持抽取 prompt 与 schema 校验规则一致。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_general_report.py
uv run pytest tests/report/test_tools_in_report.py
uv run pytest tests/report/test_sub_report.py
uv run pytest tests/algorithm/report_export/test_mermaid_renderer.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [图表生成](../chart-generation.md)
