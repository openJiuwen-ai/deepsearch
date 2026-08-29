# 子报告生成

## 维护范围

本文档覆盖专业版报告生成中的子报告能力，包括章节契约、资料分类输入、子报告 Markdown 生成和 sidecar 生成。Brief 使用独立的报告级证据与写作流程，见 [Brief 精简版报告工作流](../brief-report.md)。

本文档不覆盖候选文档预筛、信息维度矩阵文档选择、表格 caption 和最终服务端格式转换。

## 功能目的

子报告生成用于按大纲章节和研究计划生成报告主体内容。它把章节目标、采集资料、历史上下文和研究意图组合为 Prompt 输入，输出可拼接到最终报告的 Markdown 章节。

## 可见行为

- 每个章节会生成独立子报告。
- 子报告标题会被清理编号，过深标题会被降级为列表项。
- 子大纲生成只面向当前顶层章节；用户在当前 outline、章节标题或章节描述中指定的 subsection titles 会被精确保留。
- `focus_dimensions` 只定义当前章节的研究范围，子大纲生成器不会机械地为每个维度创建一个 H2；多个维度可以在一个连贯的扁平章节中呈现。
- 聚焦、简洁且不需要独立比较轴、类别、阶段、机制、对象、问题或步骤的章节，可以生成仅含一级标题的扁平子大纲；需要显式内部结构时仍生成连续编号的二级标题。
- 子大纲的每个非空行必须是第一行一级标题或后续二级标题。说明文字、代码围栏、正文、空标题、错序标题和其他章节标题均视为非法输出，并触发子大纲重试。
- 所有传入 LLM prompt 的 outline 均经 `export_outline_without_plans` 处理：剥离 `plans`（含 `step_result`/`evaluation` 等收集结果全文），仅保留章节标题、描述、依赖关系等结构骨架，避免超长输入导致模型 token 超限。
- 子大纲生成 Prompt 中使用的 fulltext 内容会被截断到 500 字符以控制输入长度；完整 fulltext 内容仍保留给后续子报告写作 Prompt 使用。
- key passages 只约束模型新增的具体事实、指标、案例、公司名和命名示例，不用于重命名或泛化用户指定的 subsection titles。
- 全文抽取阶段（`enrich_fulltext_for_section` 为同步调用，非 async），`FullTextEvidence` 会聚合同一 URL 下所有段落的 reliability（取最大值）和 data_density，使全文证据继承段落级的质量评估。
- 子报告写作严格复用已批准的子大纲标题。单行扁平大纲只允许一个 H1，不得增加子大纲之外的 Markdown 标题；章节要求的结论、建议、启示等内容仍须保留，并使用段落、编号句、列表、表格或加粗引导语表达。
- professional 写作 Prompt 对标题施加与下游校验一致的硬约束：禁止 H3 及更深标题（深层结构用加粗无序列表表达）、子大纲每一行必须恰好输出为一个 Markdown 标题、禁止子大纲之外的任何 `#`/`##` 标题、标题文字必须逐字复制，并明示"标题不匹配将导致整章校验失败作废"。
- professional 写作 Prompt 无条件禁止正文模型直接输出 Mermaid 代码围栏、图表代码或手写图块；章节正文只能输出可溯源的文字和表格，受控图表插入由后续图表管线处理。若模型仍返回 Mermaid/图表源码，章节输出契约会拒绝该草稿并通过有界重试要求模型改写，不对正文做事后删除。
- 子报告写作只输出当前顶层章节及其二级标题，并保留 `format_requirements` 中的表格、列名、逐项枚举、来源限制和覆盖要求。
- 子报告 Prompt 定义冲突解决优先级：fulltext > 高分段落 > 低分段落。fulltext 没有覆盖分但优先级最高，当多来源对同一事实出现冲突时按此优先级采纳。
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
  > `report.py` 已按职责拆分为 11 个模块。子报告生成逻辑分布在：`report.py`（核心编排：`generate_sub_report`、`_write_subsection_reports`、`_write_with_retry` 等）、`evidence.py`（证据生成/抽取/评分/`PassageSelectionContext`，含证据管线编排 `_prepare_evidence`）、`sub_section_outline.py`（子大纲生成/重试：`_generate_sub_section_outline` / `_generate_outline_with_retry`）、`report_parts.py`（子报告 Prompt 构建 `_build_subsection_prompt`、后处理 `_post_process_subsection`、摘要/结论/sidecar/过渡）、`visualization.py`（可视化数据提取/Mermaid 代码生成：`_generate_content_for_visualization`）、`visualization_insertion.py`（图表插入：`_insert_visualization`）。
- 报告配置：`openjiuwen_deepsearch/algorithm/report/config.py`
- compact doc info：`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py`
- 全文抽取管线：`openjiuwen_deepsearch/algorithm/report/report_rationale_fulltext.py`（URL 频次选择、分类内容构建）

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/sub_report_markdown.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_sidecar.md`
- `openjiuwen_deepsearch/algorithm/prompts/sub_report_summary.md`

主要测试：

- `tests/report/test_sub_report.py`
- `tests/report/test_general_report.py`
- `tests/report/test_chapter_sidecar.py`
- `tests/report/test_evidence.py`
- `tests/report/test_sub_section_outline.py`

## 核心流程

1. Reporter 读取 outline section、章节计划和 classified contents。
2. 构建章节局部契约和资料摘要。
3. LLM 生成当前章节的子大纲；`Reporter.check_chapter_format()`（定义于 `markdown_utils.py`）逐行验证标题格式和顺序，非法输出进入重试。
4. 信息维度矩阵文档选择：rationale 生成 → 抽取式总结+打分 → 按维度 top-k 段落选择（0 分段落不参与选择） → L1/L2 过滤 → 全文抽取（同步调用） → 覆盖校验（详见 [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)）。
5. 使用专业版子报告 Prompt，并遵循已批准子大纲的扁平/层级标题契约；Brief 由独立 Brief 工作流处理，不经过本 Reporter 子报告流程。
6. LLM 按已批准的子大纲生成章节 Markdown。
7. 标题编号和过深标题被清理，并校验 Markdown 标题与子大纲逐项一致；章节草稿同时校验不含 Mermaid/图表源码，且 Mermaid 语法判断仅针对代码围栏内容，不会因普通正文关键词误判；任一章节校验失败，生成受控重试反馈并重新生成章节。
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
- 重试反馈只允许包含白名单化错误码，例如 `HEADING_COUNT_MISMATCH`、`HEADING_LEVEL_MISMATCH`、`HEADING_TITLE_MISMATCH`、`MERMAID_OUTPUT_FORBIDDEN`、`SUB_REPORT_GENERATION_EXCEPTION`，以及安全的数字位置/计数字段；不把原始失败文本作为下一轮 LLM 指令。
- 敏感日志模式下不输出完整资料和子报告正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_sub_report.py
uv run pytest tests/report/test_chapter_sidecar.py
uv run pytest tests/report/test_tools_in_report.py
uv run pytest tests/report/test_evidence.py
uv run pytest tests/report/test_sub_section_outline.py
uv run pytest tests/algorithm/query_understanding/test_research_intent_contract.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [候选文档预筛](./doc-prefilter.md)
- [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)
- [Prompt 模板系统](../prompt-template-system.md)
- [Brief 精简版报告工作流](../brief-report.md)

## 结构化证据辅助

文档选择完成后，Reporter 使用已有的 rationale 和覆盖矩阵构建精简的结构化证据说明，不增加 LLM 调用。guide 提供维度、优先级、覆盖状态到 citation 的导航，每个维度最多保留三篇覆盖分最高的入选文档；key passages 和完整证据继续由原有的子大纲上下文与 `Collected Evidence` 提供，避免重复内容和额外的模型输出协议。guide builder 接收与最终文档顺序对齐的 `doc_N` 键，用于读取对应的覆盖矩阵行。

被提升为 fulltext 的段落会同时保留在 evidence_guide 中，确保其所属 rationale 的覆盖状态仍被计入，避免这些维度在 guide 中被误标为"未覆盖"。

该说明提供给专业版模板化/非模板化子大纲及子报告：证据充分的主要信息维度用于辅助组织内容，弱证据要求谨慎表述，未覆盖维度不得作为新增事实的依据。模型仍可基于其他 covered citations 进行明确标识的综合分析，但必须说明剩余证据限制，不得把综合判断表述为来源直接报告的事实。Brief 由独立工作流使用其专有的证据收集、评审和写作契约。

结构化证据说明只辅助组织已有材料，不替代 `classified_content`、原文和引用编号。覆盖矩阵缺失、稳定文档键不一致或 background-knowledge-only 路径下，系统使用空 guide 并保持原有生成流程。

端到端排查时可在 INFO 日志中搜索 `[structured_evidence]`。`[build]` 只记录状态计数、文档数、字符数和 guide hash；`[sub_outline]` 与 `[sub_report]` 记录完整 guide 是否实际出现在对应 LLM 输入中，子报告还记录 `Collected Evidence` citation block 的数量和边界是否配对。INFO 不输出 rationale、URL 或完整 guide；`is_sensitive=False` 时只在 build 阶段通过 DEBUG 提供详细诊断。
