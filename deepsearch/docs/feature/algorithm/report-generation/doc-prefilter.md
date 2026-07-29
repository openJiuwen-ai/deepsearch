# 候选文档预筛

## 维护范围

本文档覆盖报告生成中的候选文档预筛能力，包括 URL 规范化、正文变体去重、评分提取、step 分桶、候选数量限制和均衡批处理。

本文档不覆盖信息维度矩阵文档选择（rationale 生成、覆盖矩阵评估、贪心子模选择）和 collector 侧证据生成。

详见 [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)。

## 功能目的

候选文档预筛用于在资料进入 LLM 分类前减少重复和低价值输入，控制上下文成本，并尽量保证不同研究步骤都有代表性资料进入分类阶段。

## 可见行为

- 同一 URL 和相同正文变体会被归并。
- 分数会归一化到 0 到 1，并计算综合分。
- 文档按 step bucket 分配候选名额。
- 输出包含最终候选、去重全集和调试统计。

## 关键代码路径

- 文档预筛：`openjiuwen_deepsearch/algorithm/report/doc_prefilter.py`
- compact doc info：`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py`
- collector 证据 hash：`openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`

主要测试：

- `tests/report/test_doc_prefilter.py`

## 核心流程

1. 读取候选 `doc_infos`。
2. 规范化 URL 和正文 hash。
3. 构建 URL 去重 key 和正文变体 key。
4. 提取四维评分并计算综合分。
5. 按 step bucket 计算配额。
6. 每个 bucket 内按分数和输入顺序选择代表文档。
7. 输出 `PrefilterResult`。

## 数据契约与依赖

输入：

- `doc_infos`。
- `candidate_limit`。
- score weights。

输出：

- `doc_infos`：最终进入分类的候选。
- `deduped_doc_infos`：去重全集。
- `score_stats` 和 bucket stats。

## 边界与错误处理

- 缺失或无法解析的分数按 0 处理。
- URL 无法解析时使用公共规范化结果。
- 正文为空仍会产生稳定 hash。
- 预筛不能替换下游展示和引用使用的原始 URL。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_doc_prefilter.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)
- [子报告生成](./sub-report-generation.md)
- [资料采集](../research-collector.md)
