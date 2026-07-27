# 信息维度矩阵文档选择

## 维护范围

本文档覆盖报告生成中的信息维度（rationale）驱动的文档选择能力，包括 rationale 生成、n-gram 粗筛、覆盖矩阵评估、贪心子模选择、elbow 自适应截断和覆盖校验。

本文档不覆盖候选文档预筛（URL 去重和评分排序）和子报告 Markdown 生成。

## 功能目的

用信息维度矩阵替代旧的 LLM 多轮分类收敛方案，在减少 LLM 调用次数和 token 消耗的同时，提升文档选择的覆盖度和多样性。旧方案每轮 ~58K tokens、2-3 轮收敛；新方案 rationale 生成 ~3K tokens + 覆盖矩阵分批并行评估每批 ~15K tokens，总体成本显著降低。

## 可见行为

- 每个章节生成 3-8 个 rationale（信息维度），描述该章节需要覆盖的关键信息。
- rationale 生成优先对齐用户原始 query 意图，不曲解或过度解读。
- 候选文档先经 n-gram Pool-IDF 粗筛（0 LLM 调用），删除与所有 rationale 零重叠的文档。
- n-gram 分词对中文按单字拆分（CJK 无词边界），英文按整词保留，确保不同措辞的中文文档也能产生字符 bigram 重叠。
- 覆盖矩阵评估使用分批并行（BATCH_SIZE=15），并发上限 5 批，每批独立 LLM 调用。
- rationale 生成和覆盖矩阵评估的 LLM 调用均按 `max_generate_retry_num`（默认 3）重试，覆盖 LLM 异常、空内容、JSON 解析失败三类瞬时失败，全部失败才降级。
- Prompt 安全：rationale 生成和覆盖矩阵评估的 system prompt 只含指令和抗注入约束，不可信数据（文档内容、step summaries）通过 user message 传入，防止恶意网页注入指令操纵评分。
- 贪心子模选择按边际价值排序，含冗余惩罚和噪声惩罚。
- elbow 截断后做覆盖感知扩展：跳变后只要某文档在某个 rationale 维度是最高覆盖分就保留。
- 覆盖校验只检查实际选入报告的文档，未覆盖维度写入局限性说明。

## 关键代码路径

- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`
- n-gram 工具：`openjiuwen_deepsearch/algorithm/report/ngram_utils.py`
- compact doc info：`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/rationale_generator.md`
- `openjiuwen_deepsearch/algorithm/prompts/coverage_matrix_evaluator.md`

常量定义：

- `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`（`SUB_REPORTER_RATIONALE_GENERATOR`、`SUB_REPORTER_COVERAGE_MATRIX_EVALUATOR`）

主要测试：

- `tests/report/test_doc_selection.py`
- `tests/report/test_ngram_utils.py`
- `tests/report/test_step_summaries.py`

## 核心流程

1. **rationale 生成**：LLM 根据用户 query、章节任务、大纲和 step summaries 生成 3-8 个信息维度。LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败。
2. **n-gram 粗筛**：用 unigram+bigram+trigram 的 Pool-IDF 加权交集，删除与所有 rationale 零重叠的文档（0 LLM 调用）。中文按单字拆分，英文按整词保留。
3. **覆盖矩阵评估**：将候选文档分批（BATCH_SIZE=15），并发上限 5 批，每批并行送 LLM 评估对每个 rationale 的覆盖分、可信度和噪声分。每批 LLM 调用按 `max_generate_retry_num` 重试，覆盖异常/空内容/JSON 解析失败，全部失败才降级为空响应，不影响其他批次。
4. **贪心子模选择**：每轮选边际价值最大的文档，边际价值 = 覆盖增益 - β×冗余惩罚 - γ×噪声惩罚 - δ×不可信惩罚。
5. **elbow 截断**：检测边际值跳变点，跳变前全部保留；跳变后遍历所有文档，只要某文档在某个 rationale 维度是最高覆盖分就保留，最终截断到 top_k。
6. **覆盖校验**：只检查选入报告的文档，按 0.6/0.3 阈值分类 covered/weak/uncovered，未覆盖维度写入局限性说明。

## 数据契约与依赖

输入：

- `doc_infos`：候选文档列表。
- `Outline` / `Section`：章节信息。
- `step_summaries`：研究步骤摘要。
- `user_query`：用户原始查询。

输出：

- `selected_docs`：选中的文档列表（可能非连续）。
- `coverage_result`：覆盖矩阵、可信度、噪声分。
- `verify_result`：覆盖状态、局限性说明。

Prompt 输入变量：

- rationale_generator：system prompt 只含指令和抗注入约束；user message 含 `user_query`、`section_task`、`section_description`、`overall_outline`（经 `export_outline_without_plans` 剥离 plans）、`step_summaries`（不可信数据在 user prompt 中）
- coverage_matrix_evaluator：system prompt 只含指令和抗注入约束；user message 含 `section_task`、`section_description`、`rationales`、`doc_infos`（不可信数据在 user prompt 中）

关键配置：

- `classify_doc_infos_res_top_k_num`：最大选择文档数（默认 20）。
- `max_generate_retry_num`：rationale 生成和覆盖矩阵评估的 LLM 调用重试次数（默认 3）。
- `BATCH_SIZE`：覆盖矩阵分批大小（默认 15）。
- `MAX_CONCURRENT_BATCHES`：覆盖矩阵 LLM 并发上限（默认 5）。
- `β=0.3`（冗余）、`γ=0.3`（噪声）、`δ=0.2`（不可信）。

## 边界与错误处理

- rationale 生成重试 `max_generate_retry_num` 次后仍失败才返回空列表，章节走错误路径。
- 覆盖矩阵评估 LLM 返回空内容、JSON 解析失败或调用异常（限流、超时等）时，每批重试 `max_generate_retry_num` 次后仍失败才返回空 dict，不影响其他批次。
- n-gram 粗筛删除全部文档时回退到原始 doc_infos。
- elbow 截断最终受 top_k 上限约束。
- 覆盖校验和 elbow 截断使用 `id(doc)` 对象身份做 doc→index 映射，正确处理同 URL 不同内容变体。
- 覆盖校验的 `filtered_docs` fallback 使用原始 `doc_infos`，确保映射正确。
- 最终回查 `_get_classified_infos` 直接接受矩阵选中的 `selected_docs`（位置参数，对象身份精确反查）和对应的 `marginal_values`（贪心选择的边际价值，index-aligned），不再传入 `doc_infos` 全集或 URL 列表，从签名上保证矩阵淘汰的低覆盖/高噪声变体不会重新进入写作和引用。同 source_key 组内挑代表时用 `marginal_values` 替代原始文档综合评分，更贴合矩阵覆盖语义。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_doc_selection.py
uv run pytest tests/report/test_ngram_utils.py
uv run pytest tests/report/test_step_summaries.py
```

必须覆盖的场景：

- rationale 生成优先对齐用户 query。
- n-gram 粗筛删除零重叠文档。
- n-gram 中文按单字拆分，不同措辞的中文文档产生字符 bigram 重叠。
- 覆盖矩阵分批并行评估和 offset 映射。
- LLM 调用异常降级为空响应，不影响其他批次。
- 贪心选择的边际递减和冗余惩罚。
- elbow 截断的覆盖感知非连续保留。
- 覆盖校验只检查选中文档。

## 相关文档

- [报告生成总览](../report-generation.md)
- [候选文档预筛](./doc-prefilter.md)
- [子报告生成](./sub-report-generation.md)
- [Prompt 模板系统](../prompt-template-system.md)
