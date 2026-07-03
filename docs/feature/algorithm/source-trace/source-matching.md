# 来源匹配

## 维护范围

本文档覆盖全局溯源中的来源匹配能力，包括按来源类型处理搜索记录、调用 LLM 做句子到来源的匹配、解析匹配结果，以及精确/模糊匹配辅助算法。

本文档不覆盖待引用内容识别、citation 有效性校验和参考文献追加。

## 功能目的

来源匹配用于为待引用句子找到支持它的资料片段。它把内容识别输出的句子和预处理后的搜索记录连接起来，生成后续引用插入所需的匹配结果。

## 可见行为

- 不同来源类型会分组处理。
- 过长资料会在预处理阶段拆分或裁剪。
- LLM 匹配结果会被解析、合并和校验。
- 精确匹配、模糊匹配和分类匹配辅助判断引用片段质量。

## 关键代码路径

- 来源匹配：`openjiuwen_deepsearch/algorithm/source_trace/source_matcher.py`
- 匹配算法：`openjiuwen_deepsearch/algorithm/source_trace/source_match_algo.py`
- 预处理：`openjiuwen_deepsearch/algorithm/source_trace/source_tracer_preprocessors.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/source_matching.md`

主要测试：

- `tests/source_tracer/test_source_matcher.py`
- `tests/source_tracer/test_source_match_algo.py`
- `tests/source_tracer/test_score_quality.py`
- `tests/source_tracer/test_batch_alignment.py`

## 核心流程

1. 搜索记录被预处理为可匹配的 source list。
2. 待引用句子按来源类型进入匹配流程。
3. 每个 source type 调用 LLM 或匹配算法生成候选结果。
4. 匹配结果被解析为统一结构。
5. 多批匹配结果合并去重，并过滤无效或低质量结果。
6. 有效结果交给参考文献生成和 citation 校验阶段。

## 数据契约与依赖

输入：

- 待引用句子列表。
- `search_record`。
- 来源类型和 chunk 信息。

输出：

- 匹配结果列表，包含句子、匹配来源索引和来源类型。

## 边界与错误处理

- 空 source list 时返回空匹配，不应伪造引用。
- LLM 输出无法解析时应返回空结果或降级，不应中断整篇报告。
- 匹配索引必须落在对应来源列表范围内。
- 敏感日志模式下不输出来源正文和待引用句子。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer/test_source_matcher.py
uv run pytest tests/source_tracer/test_source_match_algo.py
```

## 相关文档

- [全局溯源总览](../source-trace.md)
- [溯源内容识别](./content-recognition.md)
- [参考文献生成](./reference-generation.md)
