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

- 本文描述的需求清单映射与按复杂度自适应 rationale 数量由 `ServiceConfig.professional_report_adaptive_rationales` 控制，默认关闭；开启后才启用本节新增行为，关闭时沿用原有 5-10 条提示与 rationale 生成流程。
- 按复杂度自适应生成 rationale：简单任务 5-10 条，中等任务 10-15 条，复杂任务 15-20 条。分级依据是原始 query 的要求密度、独立问题数、枚举项、实体/地区/时期、比较维度和表格字段。
- rationale 生成优先对齐用户原始 query 意图，不曲解或过度解读。
- 候选文档按 EXTRACT_BATCH_SIZE=5 分批，每批 LLM 一次调用完成：抽取与 rationale 相关的原文段落 + 评分（coverage 按 rationale；reliability/data_density 按 passage 整体）。
- 抽取规则：只选取原文句子（verbatim），不改写、不总结；保留精确数字、表格、作者+年份结论三元组。
- 评分规则：每个 passage×rationale 对只评估 `coverage`；`reliability` 与 `data_density` 为该 passage 的整体评估值（LLM 在 passage 顶层输出，避免按 rationale 重复评估）。`coverage_matrix` 直接存 coverage 供 top-k 排序使用。
- 并发上限 5 批（MAX_CONCURRENT_BATCHES），每批独立 LLM 调用。
- rationale 生成和抽取+打分的 LLM 调用均按 `max_generate_retry_num`（默认 3）重试，覆盖 LLM 异常、空输出、JSON 解析失败三类瞬时失败；重试时上一轮失败原因以带数据边界的 `<retry_feedback>` user 消息追加到下一次调用的消息列表末尾。
- Prompt 安全：rationale 生成和抽取+打分的 system prompt 只含指令和抗注入约束，不可信数据（文档内容、step summaries）通过 user message 传入，防止恶意网页注入指令操纵评分。
- 按维度 top-k 选择：每个 rationale 独立选 top_k 个段落，排序键为覆盖分加 content_date 约束下的时效分（source_date 或无约束时退化为纯按覆盖分降序），覆盖分为 0 的段落不参与选择，跨 rationale 按对象身份去重（同一段落被多个维度选中时只保留首次出现）。
- 共享证据契约：基于已有数据构造 `rationale_contract`（单一 `coverage_status`：covered/weak/uncovered，阈值为 0.6/0.3，另含原始覆盖分、优先级和 citation 导航）、`time_contract` 与 `outline_evidence_cards`；不增加 LLM 调用。

## 关键代码路径

- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`
- 证据生成/抽取/评分/top-k 选择：`openjiuwen_deepsearch/algorithm/report/evidence.py`（rationale 生成、段落抽取打分、按维度 top-k 选择）
- 常量与正则：`openjiuwen_deepsearch/algorithm/report/report_common.py`（`EXTRACT_BATCH_SIZE`、`MAX_CONCURRENT_BATCHES` 等）
- 全文抽取管线：`openjiuwen_deepsearch/algorithm/report/report_rationale_fulltext.py`（L1/L2 过滤、URL 频次选择、分类内容构建）
- 子大纲 sidecar：`openjiuwen_deepsearch/algorithm/report/evidence.py`（`_build_rationale_contract`、`_build_outline_evidence_cards`）及 `framework/openjiuwen/agent/search_context.py`（`build_time_contract`）

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/rationale_generator/`
- `openjiuwen_deepsearch/algorithm/prompts/passages_extractor/`

常量定义：

- `openjiuwen_deepsearch/algorithm/report/report_common.py`（`EXTRACT_BATCH_SIZE`、`MAX_CONCURRENT_BATCHES`）
- `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`（`SUB_REPORTER_RATIONALE_GENERATOR`、`SUB_REPORTER_PASSAGES_EXTRACTOR`）

主要测试：

- `tests/report/test_doc_selection.py`
- `tests/report/test_report_rationale_fulltext.py`
- `tests/report/test_evidence.py`
- `tests/report/test_type_safety.py`

## 核心流程

1. **rationale 生成**：仅当 `professional_report_adaptive_rationales` 开启时，才根据原始 query 和章节交付要求判断复杂度，并以简单 5-10、中等 10-15、复杂 15-20 作为目标区间；关闭时沿用旧的 5-10 条提示与生成流程。LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败。数量低于目标下限只记录诊断，不单独触发重试；超过上限、重复描述或需求映射校验失败仍会重试。其他校验错误重试耗尽后，若已有可用 rationale 集合则保留最后一组并继续章节流程；只有没有可用集合时才进入章节错误路径。
2. **段落抽取+打分**：`_extract_and_score_documents` 将原始文档分批（EXTRACT_BATCH_SIZE=5），并发上限 5 批，每批并行送 LLM 抽取相关段落并评分。`coverage` 按 passage×rationale 评估；`reliability`、`data_density` 按 passage 整体评估一次（LLM 在 passage 顶层输出）。`coverage_matrix` 直接存 coverage 供 top-k 排序使用。维度分存储在 `dimension_scores`（结构为 `{passage_key: {rationale_id: {coverage, reliability, data_density}}}`，其中 reliability/data_density 为 passage 级整体值镜像到每个 rationale 条目），passage dict 顶层通过 `scores` 键携带每个 rationale 的 coverage/reliability/data_density，同时携带 passage 级 `reliability`/`data_density` 供图表可视化选择直接读取。每批 LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败；部分批次失败时跳过失败批次继续合并，失败批次及原因记入 warning 日志，不影响其他批次。
3. **按维度 top-k 选择**：`_select_by_rationale_coverage` 对每个 rationale 独立选 top_k 个段落，排序键 = 覆盖分 + `effective_weight` × 时效分，其中 `effective_weight = CONTENT_DATE_TIMELINESS_WEIGHT × known_ratio`（`known_ratio` = 候选池中四档非 unknown 的段落占比，有日期信号越多权重越接近上限）。content_date 约束下，按段落的 `content_time` 与约束区间判四档 compliant/partial/violation/unknown，对应 +1.0/-0.3/-1.0/0.0（部分重叠少扣、完全不符多扣、信息不足不奖不罚）；`known_ratio` 低（候选池普遍无日期）时 `effective_weight` 自动趋零、退化为纯覆盖分排序，避免在噪声上排序并挤压覆盖度；source_date 或无约束时 `timeliness_weight` 为 0，退化为纯覆盖分降序。保留门要求段落最大覆盖度 ≥ 0.15（`SELECTION_COVERAGE_FLOOR`，与下游 L1 的 `filter_passages_by_coverage` 默认阈值对齐：选出的段落必然活过 L1，时间加权的提升不会被 L1 撤销，低于门槛的段落也不再占 top-k 座位——混合池中对非时间路径同样严格不差；全池低于门槛时退回 `score>0` 老门兜底，对应 L1 的 top-5 兜底语义），门槛只看原始覆盖分（时效惩罚不会变相硬删合规段落），跨 rationale 按对象身份去重。**并集补回（union-restore）**：content_date 加权生效时，每个 rationale 另回放一份纯覆盖分 top-k 基线（独立去重轨迹、含同一地板门），被时间加权挤掉的成员中**仅 unknown 档**（判不出日期、时效分 0）补回池子——护住"没日期不罚"的无辜高覆盖段落，避免挤占导致普通维度失分；violation/partial 档不补回，扣分保留（观测日志 `restored=` 记录补回数）；补回按"每 rationale 交付 ≤ `FULLTEXT_TOP_K_PER_RATIONALE`（15）"封顶，使下游 L2 的 top-15 纯覆盖度截断对单 rationale 新增交付不触发（以 top_k ≤ 15 为前提；默认 top_k=15 时饱和 rationale 不触发补回）。
4. **L1/L2 过滤**：`filter_passages_by_coverage` 移除最大覆盖度低于阈值（默认 0.15）的段落，当全部段落低于阈值时降级保留按最大覆盖度排序的 top-5 作为兜底；`dedup_passages_by_rationale` 对高度相似的段落去重（n-gram Jaccard > 0.70，中文按字符 unigram、拉丁文按词级 bigram+trigram 计算集合），每个 rationale 保留 top-15；per-rationale 去重后再执行一次全局近重复扫描，跨 rationale 移除重复段落，保留总覆盖度更高者。
5. **全文抽取与证据卡构造**：`enrich_fulltext_for_section`（同步调用，非 async）按 URL 在 rationale 中被引用的频次选取 top-N URL，直接使用 info_collector 阶段已有的 `original_content` 作为全文证据（不做 Tavily/Jina 抓取、不做 LLM 压缩、不做覆盖度评估，全文条目无 coverage 评分，但聚合 reliability（取 max）和 data_density）。它构建统一的 `classified_content`（写作 LLM 用 passage 的 `passage_text` 或全文的 `original_content`）和 `references`（与 classified_content 逐项对应，不做 URL 去重）。随后复用已有数据构造 `outline_evidence_cards`：全文卡提供确定性 `outline_excerpt`、该 URL 下按 rationale 选中的原文 `selected_passages` 和规则抽取的 `coverage_passages`，段落卡提供入选 `passage_text`。旧 `sub_section_core_content` 可作为内部兼容数据保留，但不进入新子大纲 Prompt。
6. **共享语义**：`rationale_contract` 为每个 rationale 以单一 `coverage_status` 标注覆盖状态（covered: max_coverage >= 0.6 / weak: >= 0.3 / uncovered: < 0.3），并提供覆盖分、优先级及 citation 导航；大纲和写作 Prompt 分别解释状态在各阶段的用法，不再重复编码 `heading_policy` / `writing_policy`。`time_contract` 仅提供 source/content 时间范围，子大纲 system Prompt 负责固定的标题时间规则。二者与 `outline_evidence_cards` 一起供子大纲使用；全文卡除摘要和规则 coverage 外，还包含该来源被 rationale 选中的原文段落，便于标题依据回溯；子报告写作复用 `rationale_contract`，并从同一 `research_intent` 单独构造时间 Prompt 上下文；不额外调用 LLM。

## 数据契约与依赖

输入：

- `raw_passages`：原始文档列表（doc-level，含 `original_content` 全文）。
- `Outline` / `Section`：章节信息。
- `step_summaries`：研究步骤摘要。

输出：

- `selected_passages`：选中的段落列表（passage-level，含 `doc_url`/`doc_title`/`passage_text`）。
- `coverage_result`：覆盖矩阵、dimension_scores、抽取段落列表（`filtered_passages`）。
- `classified_content`：写作主证据；全文保留 `original_content`，段落保留 `passage_text`。
- `outline_evidence_cards`：子大纲证据视图；全文卡含 `outline_excerpt`、来源对应的 `selected_passages`/`coverage_passages`，段落卡含 `passage_text`。
- `rationale_contract`：rationale 的优先级、覆盖状态和 citation 导航。
- `time_contract`：仅含 source/content 时间范围；标题时间策略由子大纲 system Prompt 固定声明。

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

- 仅当没有任何可用 rationale 集合时，rationale 生成才返回空列表并让章节走错误路径；若数量不足但其他校验通过，保留该集合继续流程，并在 `rationale_coverage_diagnostics.below_minimum` 标记未达到目标下限。其他校验错误重试耗尽后，如果已有可用集合则保留最后一组继续章节流程；没有可用集合时才返回空列表和最后错误原因（截断 500 字符，敏感模式下省略明细）。
- 需求证据诊断遇到非法 passage key、coverage matrix 中缺失的 key 或非字典 coverage 行时，将该需求标记为 `undetermined`（无法判定），不把未知覆盖度当作 0.0 和 `evidence_gap`；没有选中段落且无 key 错误时仍报告 `evidence_gap`。
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
