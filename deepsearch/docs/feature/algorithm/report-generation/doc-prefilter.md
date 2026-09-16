# 候选文档预筛

## 维护范围

本文档覆盖报告生成中的候选文档预筛能力，仅包括 URL 规范化和正文变体去重。

本文档不覆盖信息维度矩阵文档选择（rationale 生成、覆盖矩阵评估、top-k 选择）和 collector 侧证据生成。

详见 [信息维度矩阵文档选择](./coverage-matrix-doc-selection.md)。

## 功能目的

候选文档预筛用于在资料进入 LLM 分类前去除重复文档，控制上下文成本。

## 可见行为

- 同一 URL 和相同正文变体只保留内容最长的代表。
- URL 规范化用于去重 key 生成，不替换下游展示或引用使用的原始 URL。
- 正文变体通过 content hash 或 source_id 识别。
- 输出去重后的文档列表，顺序按首次出现的去重 key 保持稳定。

## 关键代码路径

- 文档预筛：`openjiuwen_deepsearch/algorithm/report/doc_prefilter.py`
- collector 证据 hash：`openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`

主要测试：

- `tests/report/test_doc_prefilter.py`

## 核心流程

1. 读取候选 `doc_infos`。
2. 规范化 URL（小写 scheme/netloc、去除 www./m. 前缀、合并连续斜杠、去除尾部斜杠和 index 后缀、短路径保留 query）。
3. 构建正文变体 key（优先使用 source_id，否则使用正文 content hash）。
4. 同 URL 且同正文变体只保留内容最长的代表；长度相同时保留首次出现的条目。
5. 输出去重后的文档列表。

## 数据契约与依赖

输入：

- `doc_infos`：待去重的候选文档列表。

输出：

- 去重后的文档列表（浅拷贝，顺序稳定）。

## 边界与错误处理

- 非 dict 条目会被跳过。
- URL 无法解析时使用公共规范化结果。
- 正文为空仍会产生稳定 hash。
- URL 规范化只用于去重，不替换下游展示和引用使用的原始 URL。

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
