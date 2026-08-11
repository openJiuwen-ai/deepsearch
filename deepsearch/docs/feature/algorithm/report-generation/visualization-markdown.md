# Markdown 可视化

## 维护范围

本文档覆盖报告生成中的 Markdown Mermaid 可视化能力，包括从检索后的章节资料中选择可视化候选、抽取图表数据、校验抽取 schema、单位归一化、生成 Mermaid 片段，以及把 Mermaid 图表插入到子报告正文中。

本文档不覆盖 VLM 图表图片生成，也不覆盖 `#insertChart` 占位符插入。

## 功能目的

Markdown 可视化用于在报告正文中以 Mermaid 文本图表表达结构化数据。它比 VLM 图表模块更轻量，直接工作在 Markdown 报告生成链路中。

该能力的边界是：图表数据必须来自检索、筛选和分配到当前章节的 `classified_content` 原始资料，而不是来自模型已经写出的草稿正文。正文生成完成后，系统只负责把已经生成并校验通过的 Mermaid 图表插入到合适位置，不再启动额外的正文后补图抽取流程。

## 可见行为

- 可视化抽取输出必须包含标题、图表类型、records 和单位信息。
- 支持 `bar`、`line`、`pie`、`timeline` 等类型。
- 非 timeline 图表必须有明确且不混合的单位。
- 单位归一化输出必须通过 schema 校验后才会继续生成图表。
- 同一章节可以插入多张 Mermaid 图表；多图来源于章节内多个高数据密度候选资料，而不是正文生成后的二次补图。
- 插入到报告正文的 Mermaid 图表会带有系统管理的居中图题，并在图题中保留对应 citation。
- 章节正文写作 Prompt 不允许模型直接输出 Mermaid 代码围栏、图表代码或手写图块；该约束不依赖 `visualization_enable`，正文草稿不会混入未受控 Mermaid。
- 若某个候选资料抽取、归一化、合规校验或 Mermaid 生成失败，该候选会被跳过；系统不会使用本地正则从正文中硬抽图表数据。

## 性能边界

Markdown 可视化会触发多轮 LLM 调用，因此当前实现只保留正文生成前的主链路：

1. 从章节的 `classified_content` 中选择数据密度较高的资料。
2. 对每个候选资料执行图表数据抽取、校验、单位归一化和 Mermaid 生成。
3. 子报告正文生成完成后，只执行插入位置规划和 Mermaid 片段渲染。

当前实现不在正文写完后再次扫描草稿正文、生成候选、重跑图表抽取或执行重复数据去重预算控制。

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

1. 报告生成阶段根据 `classified_content` 的数据密度选择适合可视化的章节资料。
2. 根据章节标题和章节大纲推断期望图型；该结果只作为软约束，不能覆盖真实数据形态。
3. LLM 从候选原始资料中抽取图表标题、类型、records 和单位。
4. 抽取结果通过 schema 校验；混合单位、空 records、字段缺失等结果会被拒绝。
5. 对需要数值单位的图表执行单位归一化。
6. 根据图表类型生成 Mermaid 片段。
7. 合规校验确认 Mermaid 语法、图表类型、数据一致性、可读性和引用上下文满足要求。
8. 子报告正文生成完成后，系统请求插入位置规划，将已生成的 Mermaid 片段插入正文，并在图题中保留 citation。

## 数据契约与依赖

抽取输出：

- `image_title`
- `image_type`
- `records`

生成后的可视化项：

- `sub_section_visualization_content`：抽取并归一化后的图表 JSON 字符串。
- `mermaid_content`：可插入 Markdown 的 Mermaid 片段。
- `index`：图表对应的 citation 编号。
- `citation_indices`：可选字段；如果上游提供多个 citation，插入图题时会优先渲染该列表。

归一化输出：

- `unit`
- `records`

## 边界与错误处理

- 混合单位、空 records、非有限数值都应被拒绝。
- pie 图不允许负数。
- timeline 不要求数值单位。
- schema 不通过时应跳过可视化，不应生成错误图表。
- Mermaid 生成失败或合规校验失败时，只跳过当前候选，不影响子报告正文生成。
- 已有有效可视化结果不应被插入阶段清空。
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
