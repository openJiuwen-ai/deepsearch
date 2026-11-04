# 参考文献生成

## 维护范围

本文档覆盖全局溯源中的引用插入和参考文献生成能力，包括从匹配结果提取来源信息、插入 source tracer 引用、清理 Markdown 引用、合并 source data 和追加参考文献。

本文档不覆盖 citation 有效性校验和用户反馈局部溯源中的 append-only 编号映射。

## 功能目的

参考文献生成用于把来源匹配结果写回报告正文，并生成报告末尾参考文献及前端可用的来源 data。它连接来源匹配和 citation 校验阶段。

## 可见行为

- 匹配到的来源会以 `[source_tracer_result][title](url)` 形式插入正文。
- 参考文献区会被追加或重建。
- source data 会合并去重，并保留来源标题、URL、正文片段和 reference index。
- URL 需要通过 scheme 校验后才可插入。

## 关键代码路径

- 参考文献处理：`openjiuwen_deepsearch/algorithm/source_trace/add_source.py`
- 溯源入口：`openjiuwen_deepsearch/algorithm/source_trace/source_tracer.py`

主要测试：

- `tests/source_tracer/test_add_source.py`
- `tests/source_tracer/test_check.py`
- `tests/source_tracer/test_chart_citation.py`

## 核心流程

1. `SourceReferenceProcessor` 根据匹配结果从 search record 中提取来源信息。
2. 引用信息插入到对应句子后。
3. source data item 被收集和合并。
4. 旧 Markdown 引用或 source tracer 引用在需要时被清理。
5. 参考文献区根据有效 data items 生成。
6. 结果交给 citation checker 进一步验证。

## 数据契约与依赖

输入：

- 预处理后的报告。
- `search_record`。
- 匹配结果中的句子、source type 和来源索引。

输出：

- 含 source tracer 引用的报告。
- `source_datas` / data items。
- 参考文献正文。

## 边界与错误处理

- 匹配句子无法在报告中定位时不插入引用。
- URL 必须通过安全校验。
- 未闭合 Markdown URL 不应被错误清理。
- 引用标题需要 Markdown 转义。
- 敏感日志模式下不输出来源正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer/test_add_source.py
uv run pytest tests/source_tracer/test_check.py
```

## 相关文档

- [全局溯源总览](../source-trace.md)
- [来源匹配](./source-matching.md)
- [Citation 校验](./citation-checking.md)
