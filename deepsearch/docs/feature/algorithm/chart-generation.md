# 图表生成

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/chart_generation/` 下的图表生成能力，包括图表插入点识别、图表数据收集、LLM 代码生成、沙箱执行、VLM 迭代、图表占位符插入和图表来源数据更新。

本文档不覆盖报告生成阶段的 Mermaid 可视化片段，也不覆盖前端对 `#insertChart` 占位符的渲染。

## 功能目的

图表生成用于在报告生成和溯源后，根据报告内容与 classified contents 生成真实图片图表，并把图表占位符、说明和来源引用插入报告。它让报告可以从纯文本升级为带可渲染图表的结果。

## 可见行为

- 系统先识别适合插入图表的段落锚点。
- 再从 `all_classified_contents` 中收集图表数据。
- LLM 生成 Python 绘图代码，沙箱执行后产生图表文件。
- 如果配置了 VLM 迭代，图表会经过多模态反馈优化；没有可用 VLM 时可降级跳过迭代。
- 报告中插入 `(#insertChart:<chart_id>)` 占位符和图表说明。
- 图表相关来源会插入 source trace data，并追加到图表说明后。

## 关键代码路径

- 图表生成入口：`openjiuwen_deepsearch/algorithm/chart_generation/vlm_chart_generator.py`
- 图表插入点识别：`openjiuwen_deepsearch/algorithm/chart_generation/figure_placeholders.py`
- 图表数据收集：`openjiuwen_deepsearch/algorithm/chart_generation/data_collector.py`
- 图表代码生成与执行：`openjiuwen_deepsearch/algorithm/chart_generation/chart_generator.py`
- 图表插入：`openjiuwen_deepsearch/algorithm/chart_generation/insert_chart.py`
- 沙箱执行：`openjiuwen_deepsearch/algorithm/chart_generation/sandbox/sandbox_executor.py`
- 工具函数：`openjiuwen_deepsearch/algorithm/chart_generation/utils.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/vlm_find_insert_point_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/vlm_collect_data_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/vlm_generate_chart_code_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/vlm_iterate_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/chart_compliance_validate.md`
- `openjiuwen_deepsearch/algorithm/prompts/chart_data_traceability_check.md`

主要测试：

- `tests/algorithm/chart_generation/test_chart_generator_semaphore.py`
- `tests/source_tracer/test_chart_citation.py`

## 核心流程

1. `VLMChartGenerator.run` 校验报告正文、classified contents 和 source trace data。
2. 如开启 VLM 迭代但未配置 VLM，会测试普通 LLM 是否支持多模态；不支持则关闭迭代。
3. `FigurePlaceholderGenerator` 识别图表插入锚点和任务。
4. `ChartDataCollector` 根据任务和资料收集结构化图表数据。
5. `ChartGenerator` 生成绘图代码，并在沙箱内执行。
6. 图表生成使用全局和单章节双层并发限制，避免任务扇出耗尽资源。
7. `InsertChartNode` 把占位符、图表说明和来源引用插入报告。
8. 输出 `chart_messages`、修改后的报告和新的 source trace data。

## 数据契约与依赖

输入：

- `report_content`
- `all_classified_contents`
- `source_trace_datas`

输出：

- `chart_messages`：`chart_id`、`chart_title`、`description`、`base64`。
- `modified_report`
- `new_source_trace_datas`

运行依赖：

- LLM/VLM 模型配置。
- `output/vlm_chart_generator` 输出目录。
- 沙箱允许的 Python 绘图库和字体文件。

## 边界与错误处理

- 报告正文或资料为空时抛出图表生成异常。
- 图表任务为空或结果数量不匹配时返回错误或空结果。
- 沙箱限制内置函数、导入模块、文件访问和执行超时。
- 插入引用前会转义 HTML 文本、Markdown 链接文本，并校验 URL scheme。
- 锚点找不到时保留原报告并记录 warning。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/algorithm/chart_generation
uv run pytest tests/source_tracer/test_chart_citation.py
```

## 相关文档

- [报告生成](./report-generation.md)
- [全局溯源](./source-trace.md)
- [Prompt 模板系统](./prompt-template-system.md)
