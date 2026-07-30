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
- `focus_dimensions` 只定义当前章节的研究范围，子大纲生成器不会机械地为每个维度创建一个 H2；多个维度可以在一个连贯的扁平章节中呈现。
- 聚焦、简洁且不需要独立比较轴、类别、阶段、机制、对象、问题或步骤的章节，可以生成仅含一级标题的扁平子大纲；需要显式内部结构时仍生成连续编号的二级标题。
- 子大纲的每个非空行必须是第一行一级标题或后续二级标题。说明文字、代码围栏、正文、空标题、错序标题和其他章节标题均视为非法输出，并触发子大纲重试。
- 所有传入 LLM prompt 的 outline 均经 `export_outline_without_plans` 处理：剥离 `plans`（含 `step_result`/`evaluation` 等收集结果全文），仅保留章节标题、描述、依赖关系等结构骨架，避免超长输入导致模型 token 超限。
- key passages 只约束模型新增的具体事实、指标、案例、公司名和命名示例，不用于重命名或泛化用户指定的 subsection titles。
- 子报告写作严格复用已批准的子大纲标题。单行扁平大纲只允许一个 H1，不得增加子大纲之外的 Markdown 标题；章节要求的结论、建议、启示等内容仍须保留，并使用段落、编号句、列表、表格或加粗引导语表达。
- professional 写作 Prompt 对标题施加与下游校验一致的硬约束：禁止 H3 及更深标题（深层结构用加粗无序列表表达）、子大纲每一行必须恰好输出为一个 Markdown 标题、禁止子大纲之外的任何 `#`/`##` 标题、标题文字必须逐字复制，并明示"标题不匹配将导致整章校验失败作废"。
- professional 和 brief 写作 Prompt 都遵循相同的扁平标题契约，并保留 `format_requirements` 中的表格、列名、逐项枚举、来源限制和覆盖要求。
- 子报告写作只输出当前顶层章节及其二级标题，并保留 `format_requirements` 中的表格、列名、逐项枚举、来源限制和覆盖要求。
- 子报告失败重试只向下一轮 Prompt 传递受控错误码、位置和计数字段；不会回放模型生成标题、provider 异常或本地校验原始文本。
- 普通与依赖驱动写作路径都会把 `section_format_requirements` 和 `section_local_contract` 写入
  `SectionContext`。依赖写作工作流不得在开始节点边界将这两个字段退化为 `[]` 或 `{}`，下游
  SubReporter 使用它们约束章节格式、允许展开的分析维度和最终判断权限。
- 章节 sidecar 保存摘要、资料映射和局部契约，供后续用户反馈和报告流程复用。
- 子大纲与子报告正文生成失败后会按 `max_generate_retry_num` 重试；重试时上一轮校验失败原因会以带数据边界的 `<retry_feedback>` user 消息追加到下一次调用的消息列表末尾（system prompt 不变，首次调用不追加）。
- rationale（信息维度）生成失败后同样按 `max_generate_retry_num` 重试；重试循环在 `_generate_section_rationales` 内部，上一轮失败原因（LLM 异常 / 空输出 / JSON 解析失败）由内部循环变量维护，同样以 `<retry_feedback>` user 消息注入下一次 LLM 调用。
- rationale 生成重试耗尽后，最后一次失败原因（`last_error`）随返回值传播到上游 `generate_sub_report` 的错误消息（截断 500 字符，敏感模式下省略明细），不再是泛化的 "rationale generation fail"。
- 子报告正文重试的 warning 日志在非敏感模式下包含校验失败的具体原因，敏感模式下为泛化文案。

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
3. LLM 生成当前章节的子大纲；`Reporter.check_chapter_format()` 逐行验证标题格式和顺序，非法输出进入重试。
4. 信息维度矩阵文档选择：rationale 生成 → n-gram 粗筛 → 覆盖矩阵评估 → 贪心子模选择 → elbow 截断 → 覆盖校验（详见 [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)）。
5. 根据报告类型选择 professional 或 brief 子报告 Prompt，两者共享扁平/层级标题契约。
6. LLM 按已批准的子大纲生成章节 Markdown。
7. 标题编号和过深标题被清理，并校验 Markdown 标题与子大纲逐项一致；如果失败，生成受控重试反馈并重新生成章节。
8. 生成或更新 chapter sidecar。
9. 子报告交给最终报告拼接。

提纲、正文、rationale 生成和覆盖矩阵评估失败重试时，上一轮失败原因会以带数据边界的 `<retry_feedback>` user 消息追加到下一次调用的消息列表末尾（system prompt 不变，首次调用不追加）；rationale/覆盖矩阵重试耗尽后，真实失败原因（截断 500 字符）会传播到上游错误消息；覆盖矩阵全部批次失败时降级为跳过打分选文、直接用候选文档继续写作（不丢章），部分批次失败继续合并并记录 warning 日志；敏感模式下重试日志使用泛化文案，且异常类文本（provider 异常详情）在上游错误消息、返回值与 LLM 反馈中均泛化（校验类结构化原因不受影响）。

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
- 扁平子大纲必须恰好包含一个合法一级标题；层级化子大纲必须先出现一级标题，再出现带非空标题文本的 `n.x` 二级标题。
- 三级编号检查按行首匹配（如 `2.1.1 标题`）；标题行内出现的点分数字（日期区间 `2010.3.12–2021.2.26`、版本号等）不视为三级编号。
- 子大纲中的额外说明、Markdown 代码围栏、正文或错序标题不能被静默接受。
- 正文标题必须与已批准子大纲的数量、层级、顺序和文本一致，不能破坏整体报告层级。
- 重试反馈只允许包含白名单化错误码，例如 `HEADING_COUNT_MISMATCH`、`HEADING_LEVEL_MISMATCH`、`HEADING_TITLE_MISMATCH`、`SUB_REPORT_GENERATION_EXCEPTION`，以及安全的数字位置/计数字段；不把原始失败文本作为下一轮 LLM 指令。
- 敏感日志模式下不输出完整资料和子报告正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_sub_report.py
uv run pytest tests/report/test_chapter_sidecar.py
uv run pytest tests/report/test_tools_in_report.py
uv run pytest tests/algorithm/query_understanding/test_research_intent_contract.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [候选文档预筛](./doc-prefilter.md)
- [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)
- [Prompt 模板系统](../prompt-template-system.md)
