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

## 关键代码路径

- 报告工具：`openjiuwen_deepsearch/algorithm/report/report_utils.py`
- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_content.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_visualization_normalize_units.md`

主要测试：

- `tests/report/test_general_report.py`
- `tests/report/test_tools_in_report.py`

## 核心流程

1. 报告生成阶段识别适合可视化的章节内容。
2. LLM 抽取图表标题、类型、records 和单位。
3. 抽取结果通过 schema 校验。
4. 对需要数值单位的图表执行单位归一化。
5. 根据图表类型生成 Mermaid 片段。
6. Mermaid 片段插入报告正文。

## 数据契约与依赖

抽取输出：

- `image_title`
- `image_type`
- `records`

归一化输出：

- `unit`
- `records`

## 边界与错误处理

- 混合单位、空 records、非有限数值都应被拒绝。
- pie 图不允许负数。
- timeline 不要求数值单位。
- schema 不通过时应跳过可视化，不应生成错误图表。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_general_report.py
uv run pytest tests/report/test_tools_in_report.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [图表生成](../chart-generation.md)
