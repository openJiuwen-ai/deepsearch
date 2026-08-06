# 信息维度矩阵段落选择

## 维护范围

本文档覆盖报告生成中的信息维度（rationale）驱动的段落选择能力，包括 rationale 生成、段落抽取+打分、按维度 top-k 段落选择和覆盖校验。

本文档不覆盖候选文档预筛（URL 去重和评分排序）和子报告 Markdown 生成。

## 功能目的

用信息维度（rationale）驱动的段落选择，在减少 LLM 调用次数和 token 消耗的同时，提升段落选择的覆盖度和多样性。

核心方案：LLM 一次调用同时完成文档精华段落抽取和 rationale 覆盖打分（3 维度加权评分）。
- **跨语言有效**：LLM 直接语义理解，中文 rationale 可匹配英文文献
- **上下文完整**：LLM 看到完整文档，避免论点被切断
- **数据保真**：抽取式总结只选取原文句子，不改写、不丢失精确数字/表格/作者+年份
- **步骤精简**：抽取+打分一步完成
- **3 维度评分**：每个 passage×rationale 对从 coverage（0.8）、reliability（0.1）、data_density（0.1）三个维度打分，加权汇总为 total_score

## 可见行为

- 每个章节生成 3-8 个 rationale（信息维度），描述该章节需要覆盖的关键信息。
- rationale 生成优先对齐用户原始 query 意图，不曲解或过度解读。
- 候选文档按 EXTRACT_BATCH_SIZE=5 分批，每批 LLM 一次调用完成：抽取与 rationale 相关的原文段落 + 3 维度评分（coverage/reliability/data_density）。
- 抽取规则：只选取原文句子（verbatim），不改写、不总结；保留精确数字、表格、作者+年份结论三元组。
- 评分规则：每个 passage×rationale 对的 total_score = 0.8×coverage + 0.1×reliability + 0.1×data_density；优先使用 LLM 返回的 total_score，若缺失则按权重公式计算。
- 并发上限 5 批（MAX_CONCURRENT_BATCHES），每批独立 LLM 调用。
- rationale 生成和抽取+打分的 LLM 调用均按 `max_generate_retry_num`（默认 3）重试，覆盖 LLM 异常、空输出、JSON 解析失败三类瞬时失败；重试时上一轮失败原因以带数据边界的 `<retry_feedback>` user 消息追加到下一次调用的消息列表末尾。
- Prompt 安全：rationale 生成和抽取+打分的 system prompt 只含指令和抗注入约束，不可信数据（文档内容、step summaries）通过 user message 传入，防止恶意网页注入指令操纵评分。
- 按维度 top-k 选择：每个 rationale 独立按覆盖分降序选 top_k 个段落，跨 rationale 按对象身份去重（同一段落被多个维度选中时只保留首次出现）。
- 覆盖校验只检查实际选入报告的段落，未覆盖维度写入局限性说明。

## 关键代码路径

- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/rationale_generator.md`
- `openjiuwen_deepsearch/algorithm/prompts/passages_extractor.md`

常量定义：

- `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`（`SUB_REPORTER_RATIONALE_GENERATOR`、`SUB_REPORTER_PASSAGES_EXTRACTOR`）

主要测试：

- `tests/report/test_doc_selection.py`

## 核心流程

1. **rationale 生成**：LLM 根据章节任务、章节描述、章节焦点和 step summaries 生成 3-8 个信息维度。LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败；重试时上一轮失败原因以 `<retry_feedback>` user 消息注入下一次调用，重试耗尽后 `last_error` 随返回值传播到上游错误消息。
2. **段落抽取+打分**：`_extract_and_score_documents` 将原始文档分批（EXTRACT_BATCH_SIZE=5），并发上限 5 批，每批并行送 LLM 抽取相关段落并评分。每个 passage×rationale 对输出 3 维度评分（coverage 0.8、reliability 0.1、data_density 0.1），加权汇总为 total_score；优先使用 LLM 返回的 total_score，若缺失则按权重公式计算。维度分存储在 `dimension_scores`（结构为 `{passage_key: {rationale_id: {coverage, reliability, data_density, total_score}}}`），total_score 同步存入 `coverage_matrix` 供下游选择使用。每批 LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败；部分批次失败时跳过失败批次继续合并，失败批次及原因记入 warning 日志，不影响其他批次。
3. **按维度 top-k 选择**：`_select_by_rationale_coverage` 对每个 rationale 独立按覆盖分降序选 top_k 个段落，跨 rationale 按对象身份去重。
4. **覆盖校验**：只检查选入报告的段落，按 0.6/0.3 阈值分类 covered/weak/uncovered，未覆盖维度写入局限性说明。

## 数据契约与依赖

输入：

- `raw_passages`：原始文档列表（doc-level，含 `original_content` 全文）。
- `Outline` / `Section`：章节信息。
- `step_summaries`：研究步骤摘要。

输出：

- `selected_passages`：选中的段落列表（passage-level，含 `doc_url`/`doc_title`/`passage_text`）。
- `coverage_result`：覆盖矩阵、dimension_scores、抽取段落列表（`filtered_passages`）。

Prompt 输入变量：

- rationale_generator：system prompt 只含指令和抗注入约束；user message 含 `section_task`、`section_description`、`section_focus`、`focus_dimensions`、`step_summaries`（不可信数据在 user prompt 中）
- passages_extractor：system prompt 只含指令和抽取规则+抗注入约束；user message 含 `section_task`、`section_description`、`rationales`、文档全文（不可信数据在 user prompt 中）

关键配置：

- `classify_doc_infos_res_top_k_num`：每个 rationale 最大选择段落数（默认 20）。
- `max_generate_retry_num`：rationale 生成和抽取+打分的 LLM 调用重试次数（默认 3）。
- `EXTRACT_BATCH_SIZE`：每批文档数量（默认 5）。
- `MAX_CONCURRENT_BATCHES`：LLM 并发上限（默认 5）。

## 边界与错误处理

- rationale 生成重试 `max_generate_retry_num` 次后仍失败才返回空列表，并携带最后一次失败原因（`last_error`）传播到上游错误消息（截断 500 字符，敏感模式下省略明细），章节走错误路径。
- 抽取+打分 LLM 返回空内容、JSON 解析失败或调用异常时，每批重试 `max_generate_retry_num` 次后仍失败才返回空 dict，并携带该批次失败原因；部分批次失败时跳过失败批次继续合并，失败批次及原因记入 warning 日志，不影响其他批次。
- **全部批次失败时降级（不丢章）**：`_extract_and_score_documents` 将原始文档截断为 500 字作为 passage，包含 `"data_density": 0.0` 字段，空覆盖矩阵，合并后的批次错误原因记录 warning；`generate_sub_report` 检测到该降级结果后跳过打分选文，直接用截断后的候选文档继续写作。
- LLM 返回的 `rationale_scores` 中非数值类型被降级为 0.0，避免 float 崩溃。
- 覆盖校验使用 `is` 对象身份比较做 doc→index 映射，正确处理同 URL 不同内容变体；URL 匹配增加 `passage_text` 消歧。
- 最终回查 `_get_classified_infos` 直接接受矩阵选中的 `selected_passages`（段落级，使用 `doc_url`/`doc_title`/`passage_text` 字段）。引用按 `doc_url` 去重，每个不同 URL 生成一条引用；`passage_text` 直接作为 key passage 输出。
- `dimension_scores` 随 `coverage_result` 传递，Excel 导出时在"信息维度 Top 段落"sheet 中输出每个 passage×rationale 对的 coverage/reliability/data_density/total_score 维度分。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_doc_selection.py
```

必须覆盖的场景：

- rationale 生成优先对齐用户 query。
- 抽取式总结产出 passage-level 字段（doc_url/doc_title/passage_text）。
- 3 维度评分（coverage/reliability/data_density）加权汇总为 total_score，dimension_scores 结构正确。
- 全部批次失败时降级为原始文档截断。
- LLM 返回非数值 rationale_scores 被降级为 0.0。
- 按维度 top-k 选择：每个 rationale 独立选 top_k，跨 rationale 去重。
- passage_text 保留原文精确数字，不被改写。
- 引用按 doc_url 去重，同一 URL 多段落只生成一条引用。
- 覆盖校验只检查选中段落。

## 相关文档

- [报告生成总览](../report-generation.md)
- [候选文档预筛](./doc-prefilter.md)
- [子报告生成](./sub-report-generation.md)
- [Prompt 模板系统](../prompt-template-system.md)
