# 信息维度矩阵段落选择

## 维护范围

本文档覆盖报告生成中的信息维度（rationale）驱动的段落选择能力，包括 rationale 生成、段落抽取+打分、按维度 top-k 段落选择。

本文档不覆盖候选文档预筛（仅 URL/正文去重）和子报告 Markdown 生成。

## 功能目的

用信息维度（rationale）驱动的段落选择，在减少 LLM 调用次数和 token 消耗的同时，提升段落选择的覆盖度和多样性。

核心方案：LLM 一次调用同时完成文档精华段落抽取和 rationale 覆盖打分（3 维度评分，排序仅按 coverage）。
- **跨语言有效**：LLM 直接语义理解，中文 rationale 可匹配英文文献
- **上下文完整**：LLM 看到完整文档，避免论点被切断
- **数据保真**：抽取式总结只选取原文句子，不改写、不丢失精确数字/表格/作者+年份
- **步骤精简**：抽取+打分一步完成
- **3 维度评分**：`coverage` 按 rationale 评估；`reliability` 与 `data_density` 按 passage/文档整体评估一次（来源可靠性与数据密度是整段/整篇的属性，不随 rationale 变化）。排序仅按 coverage

## 可见行为

- 每个章节生成 3-8 个 rationale（信息维度），描述该章节需要覆盖的关键信息。
- rationale 生成优先对齐用户原始 query 意图，不曲解或过度解读。
- 候选文档按 EXTRACT_BATCH_SIZE=5 分批，每批 LLM 一次调用完成：抽取与 rationale 相关的原文段落 + 评分（coverage 按 rationale；reliability/data_density 按 passage 整体）。
- 抽取规则：只选取原文句子（verbatim），不改写、不总结；保留精确数字、表格、作者+年份结论三元组。
- 评分规则：每个 passage×rationale 对只评估 `coverage`；`reliability` 与 `data_density` 为该 passage 的整体评估值（LLM 在 passage 顶层输出，避免按 rationale 重复评估）。`coverage_matrix` 直接存 coverage 供 top-k 排序使用。
- 并发上限 5 批（MAX_CONCURRENT_BATCHES），每批独立 LLM 调用。
- rationale 生成和抽取+打分的 LLM 调用均按 `max_generate_retry_num`（默认 3）重试，覆盖 LLM 异常、空输出、JSON 解析失败三类瞬时失败；重试时上一轮失败原因以带数据边界的 `<retry_feedback>` user 消息追加到下一次调用的消息列表末尾。
- Prompt 安全：rationale 生成和抽取+打分的 system prompt 只含指令和抗注入约束，不可信数据（文档内容、step summaries）通过 user message 传入，防止恶意网页注入指令操纵评分。
- 按维度 top-k 选择：每个 rationale 独立选 top_k 个段落，排序键为覆盖分加 content_date 约束下的时效分（source_date 或无约束时退化为纯按覆盖分降序），覆盖分为 0 的段落不参与选择，跨 rationale 按对象身份去重（同一段落被多个维度选中时只保留首次出现）。
- 结构化证据指南：`build_structured_evidence_guide` 为每个 rationale 标注覆盖状态（covered/weak/uncovered），按 0.6/0.3 阈值分类，并列出 top-3 段落供写作参考。

## 关键代码路径

- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`
- 全文抽取管线：`openjiuwen_deepsearch/algorithm/report/report_rationale_fulltext.py`（L1/L2 过滤、URL 频次选择、分类内容构建）
- 结构化证据指南：`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py`（`build_structured_evidence_guide`）

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/rationale_generator.md`
- `openjiuwen_deepsearch/algorithm/prompts/passages_extractor.md`

常量定义：

- `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`（`SUB_REPORTER_RATIONALE_GENERATOR`、`SUB_REPORTER_PASSAGES_EXTRACTOR`）

主要测试：

- `tests/report/test_doc_selection.py`
- `tests/report/test_report_rationale_fulltext.py`

## 核心流程

1. **rationale 生成**：LLM 根据章节任务、章节描述、章节焦点和 step summaries 生成 3-8 个信息维度。LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败；重试时上一轮失败原因以 `<retry_feedback>` user 消息注入下一次调用，重试耗尽后 `last_error` 随返回值传播到上游错误消息。
2. **段落抽取+打分**：`_extract_and_score_documents` 将原始文档分批（EXTRACT_BATCH_SIZE=5），并发上限 5 批，每批并行送 LLM 抽取相关段落并评分。`coverage` 按 passage×rationale 评估；`reliability`、`data_density` 按 passage 整体评估一次（LLM 在 passage 顶层输出）。`coverage_matrix` 直接存 coverage 供 top-k 排序使用。维度分存储在 `dimension_scores`（结构为 `{passage_key: {rationale_id: {coverage, reliability, data_density}}}`，其中 reliability/data_density 为 passage 级整体值镜像到每个 rationale 条目），passage dict 顶层通过 `scores` 键携带每个 rationale 的 coverage/reliability/data_density，同时携带 passage 级 `reliability`/`data_density` 供图表可视化选择直接读取。每批 LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败；部分批次失败时跳过失败批次继续合并，失败批次及原因记入 warning 日志，不影响其他批次。
3. **按维度 top-k 选择**：`_select_by_rationale_coverage` 对每个 rationale 独立选 top_k 个段落，排序键 = 覆盖分 + `effective_weight` × 时效分，其中 `effective_weight = CONTENT_DATE_TIMELINESS_WEIGHT × known_ratio`（`known_ratio` = 候选池中四档非 unknown 的段落占比，有日期信号越多权重越接近上限）。content_date 约束下，按段落的 `content_time` 与约束区间判四档 compliant/partial/violation/unknown，对应 +1.0/-0.3/-1.0/0.0（部分重叠少扣、完全不符多扣、信息不足不奖不罚）；`known_ratio` 低（候选池普遍无日期）时 `effective_weight` 自动趋零、退化为纯覆盖分排序，避免在噪声上排序并挤压覆盖度；source_date 或无约束时 `timeliness_weight` 为 0，退化为纯覆盖分降序。保留门要求段落最大覆盖度 ≥ 0.15（`SELECTION_COVERAGE_FLOOR`，与下游 L1 的 `filter_passages_by_coverage` 默认阈值对齐：选出的段落必然活过 L1，时间加权的提升不会被 L1 撤销，低于门槛的段落也不再占 top-k 座位——混合池中对非时间路径同样严格不差；全池低于门槛时退回 `score>0` 老门兜底，对应 L1 的 top-5 兜底语义），门槛只看原始覆盖分（时效惩罚不会变相硬删合规段落），跨 rationale 按对象身份去重。**并集补回（union-restore）**：content_date 加权生效时，每个 rationale 另回放一份纯覆盖分 top-k 基线（独立去重轨迹、含同一地板门），被时间加权挤掉的成员中**仅 unknown 档**（判不出日期、时效分 0）补回池子——护住"没日期不罚"的无辜高覆盖段落，避免挤占导致普通维度失分；violation/partial 档不补回，扣分保留（观测日志 `restored=` 记录补回数）；补回按"每 rationale 交付 ≤ `FULLTEXT_TOP_K_PER_RATIONALE`（15）"封顶，使下游 L2 的 top-15 纯覆盖度截断对单 rationale 新增交付不触发（以 top_k ≤ 15 为前提；默认 top_k=15 时饱和 rationale 不触发补回）。
4. **L1/L2 过滤**：`filter_passages_by_coverage` 移除最大覆盖度低于阈值（默认 0.15）的段落，当全部段落低于阈值时降级保留按最大覆盖度排序的 top-5 作为兜底；`dedup_passages_by_rationale` 对高度相似的段落去重（n-gram Jaccard > 0.70，中文按字符 unigram、拉丁文按词级 bigram+trigram 计算集合），每个 rationale 保留 top-15；per-rationale 去重后再执行一次全局近重复扫描，跨 rationale 移除重复段落，保留总覆盖度更高者。
5. **全文抽取**：`enrich_fulltext_for_section`（同步调用，非 async）按 URL 在 rationale 中被引用的频次选取 top-N URL，直接使用 info_collector 阶段已有的 `original_content` 作为全文证据（不做 Tavily/Jina 抓取、不做 LLM 压缩、不做覆盖度评估，全文条目无 coverage 评分，但聚合了 reliability（取 max）和 data_density），从段落集合中移除已被全文替代的段落，剩余段落保留 passage 级 `reliability`/`data_density`，构建统一的 `classified_content`（段落项含 `passage_text` 切片和 `original_content` 全文；写作 LLM 用 `passage_text`（段落）或 `original_content`（全文），溯源统一用 `original_content`，图表选择读顶层字段）、`sub_section_core_content`（全文用 `original_content`，段落用 `passage_text`）、`references`（与 classified_content 逐项对应，不做 URL 去重）和段落版 `structured_evidence_guide`（每个 rationale 取 top-3 覆盖度段落；被提升为全文的段落也纳入 evidence guide，避免 rationale 显示为 uncovered）；`build_core_content_list` 将全文截断为 500 字供 outline prompt 使用。
6. **结构化证据指南**：`build_structured_evidence_guide` 为每个 rationale 标注覆盖状态（covered: max_coverage >= 0.6 / weak: >= 0.3 / uncovered: < 0.3），并列出该 rationale 下 top-3 段落（coverage >= 0.3）供写作参考。覆盖状态仅写入 evidence guide 字符串，不单独输出局限性说明。

## 数据契约与依赖

输入：

- `raw_passages`：原始文档列表（doc-level，含 `original_content` 全文）。
- `Outline` / `Section`：章节信息。
- `step_summaries`：研究步骤摘要。

输出：

- `selected_passages`：选中的段落列表（passage-level，含 `doc_url`/`doc_title`/`passage_text`）。
- `coverage_result`：覆盖矩阵、dimension_scores、抽取段落列表（`filtered_passages`）。
- `structured_evidence_guide`：段落版写作指南，含每个 rationale 的覆盖状态和 top-3 段落。

Prompt 输入变量：

- rationale_generator：system prompt 只含指令和抗注入约束；user message 含 `section_task`、`section_description`、`section_focus`、`focus_dimensions`、`step_summaries`（不可信数据在 user prompt 中）
- passages_extractor：system prompt 只含指令和抽取规则+抗注入约束；user message 含 `section_task`、`section_description`、`rationales`、文档全文（不可信数据在 user prompt 中）

关键配置：

- `classify_doc_infos_res_top_k_num`：每个 rationale 最大选择段落数（默认 15）。L2 去重后每个 rationale 保留 `top_k_per_rationale=15`。
- `CONTENT_DATE_TIMELINESS_WEIGHT`（`report.py` 模块常量，非配置项）：content_date 约束下时效分在排序键中的权重上限（0.2）。实际排序权重 `effective_weight = CONTENT_DATE_TIMELINESS_WEIGHT × known_ratio`，`known_ratio` 为候选池中有日期段落的占比（unknown 越多权重越自动趋零，低信号场景自动退出，不再挤压覆盖度）；source_date 或无约束时不生效。
- `max_generate_retry_num`：rationale 生成和抽取+打分的 LLM 调用重试次数（默认 3）。
- `EXTRACT_BATCH_SIZE`：每批文档数量（默认 5）。
- `MAX_CONCURRENT_BATCHES`：LLM 并发上限（默认 5）。

## 边界与错误处理

- rationale 生成重试 `max_generate_retry_num` 次后仍失败才返回空列表，并携带最后一次失败原因（`last_error`）传播到上游错误消息（截断 500 字符，敏感模式下省略明细），章节走错误路径。
- 抽取+打分 LLM 返回空内容、JSON 解析失败或调用异常时，每批重试 `max_generate_retry_num` 次后仍失败才返回空 dict，并携带该批次失败原因；部分批次失败时跳过失败批次继续合并，失败批次及原因记入 warning 日志，不影响其他批次。
- **filtered_passages 为空时降级（不丢章）**：当 `_extract_and_score_documents` 合并后 `filtered_passages` 为空时（不仅限于全部批次失败，也包括所有批次返回空段落的情况），将原始文档截断为 500 字作为 passage，包含 `"data_density": 0.0` 字段，空覆盖矩阵，合并后的批次错误原因记录 warning；`generate_sub_report` 检测到该降级结果后跳过打分选文，直接用截断后的候选文档继续写作。
- LLM 返回的 `rationale_scores` 中非数值类型被降级为 0.0，避免 float 崩溃。
- 覆盖校验使用 passage dict 上的显式 `_passage_key` 字段做 doc→index 映射（替代 `id()` 对象身份比较），正确处理同 URL 不同内容变体；URL 匹配增加 `passage_text` 消歧。
- 最终回查 `enrich_fulltext_for_section` 直接接受矩阵选中的 `selected_passages`（段落级，使用 `doc_url`/`doc_title`/`passage_text` 字段）。引用与 `classified_content` 逐项对应，不做 URL 去重；`passage_text` 直接作为 key passage 输出。
- `dimension_scores` 随 `coverage_result` 传递，Excel 导出时在"信息维度 Top 段落"sheet 中输出每个 passage×rationale 对的 coverage/reliability/data_density 维度分。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_doc_selection.py
```

必须覆盖的场景：

- rationale 生成优先对齐用户 query。
- 抽取式总结产出 passage-level 字段（doc_url/doc_title/passage_text）。
- 3 维度评分（coverage/reliability/data_density）存储于 dimension_scores，排序仅按 coverage，dimension_scores 结构正确。
- filtered_passages 为空时（含全部批次失败）降级为原始文档截断。
- LLM 返回非数值 rationale_scores 被降级为 0.0。
- 按维度 top-k 选择：每个 rationale 独立选 top_k，跨 rationale 去重。
- passage_text 保留原文精确数字，不被改写。
- 引用与 classified_content 逐项对应，不做 URL 去重。
- 结构化证据指南为每个 rationale 标注 covered/weak/uncovered 状态。
- content_date 约束下排序键含时效分：compliant 段落优先、violation 段落被降权但不硬删（覆盖分 > 0 仍可选）；倒置的 `content_time` 区间视为无效、判 unknown 不奖不罚；`known_ratio=0`（全部 unknown）时 `effective_weight=0`、排序退化为纯覆盖度，`known_ratio=1` 时等价于固定权重（回归）。

## 相关文档

- [报告生成总览](../report-generation.md)
- [候选文档预筛](./doc-prefilter.md)
- [子报告生成](./sub-report-generation.md)
- [Prompt 模板系统](../prompt-template-system.md)
