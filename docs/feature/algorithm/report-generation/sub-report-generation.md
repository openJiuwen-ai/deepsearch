# 子报告生成

## 维护范围

本文档覆盖报告生成中的子报告生成能力，包括章节契约、资料分类输入、子报告 Markdown 生成、brief/professional 差异和 sidecar 生成。

本文档不覆盖候选文档预筛、信息维度矩阵文档选择、表格 caption 和最终服务端格式转换。

## 功能目的

子报告生成用于按大纲章节和研究计划生成报告主体内容。它把章节目标、采集资料、历史上下文和研究意图组合为 Prompt 输入，输出可拼接到最终报告的 Markdown 章节。

## 可见行为

- 每个章节会生成独立子报告。
- brief 报告和 professional 报告使用不同 Prompt 或不同段落策略。
- 子报告标题会被清理编号，过深标题会被降级为列表项。
- 子大纲生成只面向当前顶层章节；用户在当前 outline、章节标题或章节描述中指定的 subsection titles 会被精确保留。
- key passages 只约束模型新增的具体事实、指标、案例、公司名和命名示例，不用于重命名或泛化用户指定的 subsection titles。
- 子报告写作只输出当前顶层章节及其二级标题，并保留 `format_requirements` 中的表格、列名、逐项枚举、来源限制和覆盖要求。
- 普通与依赖驱动写作路径都会把 `section_format_requirements` 和 `section_local_contract` 写入
  `SectionContext`。依赖写作工作流不得在开始节点边界将这两个字段退化为 `[]` 或 `{}`，下游
  SubReporter 使用它们约束章节格式、允许展开的分析维度和最终判断权限。
- 章节 sidecar 保存摘要、资料映射和局部契约，供后续用户反馈和报告流程复用。

## 关键代码路径

- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`
- 报告配置：`openjiuwen_deepsearch/algorithm/report/config.py`
- compact doc info：`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/sub_report_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_brief_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_sidecar.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_summary.md`

主要测试：

- `tests/report/test_sub_report.py`
- `tests/report/test_general_report.py`
- `tests/report/test_chapter_sidecar.py`

## 核心流程

1. Reporter 读取 outline section、章节计划和 classified contents。
2. 构建章节局部契约和资料摘要。
3. 信息维度矩阵文档选择：rationale 生成 → n-gram 粗筛 → 覆盖矩阵评估 → 贪心子模选择 → elbow 截断 → 覆盖校验（详见 [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)）。
4. 根据报告类型选择子报告 Prompt。
5. LLM 生成章节 Markdown。
6. 标题编号和过深标题被清理。
7. 生成或更新 chapter sidecar。
8. 子报告交给最终报告拼接。

## 数据契约与依赖

输入：

- `Outline` / `Section`。
- 章节 `Plan`。
- `classified_content`。
- `ResearchIntent`。
- section local contract。

输出：

- 子报告 Markdown。
- `ChapterSidecar`。

## 边界与错误处理

- 子报告生成异常使用 `SUB_REPORT_GENERATE_ERROR` 格式化。
- Prompt 输出空内容时应走错误或 fallback 路径。
- 标题不能破坏整体报告层级。
- 敏感日志模式下不输出完整资料和子报告正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_sub_report.py
uv run pytest tests/report/test_chapter_sidecar.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [候选文档预筛](./doc-prefilter.md)
- [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)
- [Prompt 模板系统](../prompt-template-system.md)
