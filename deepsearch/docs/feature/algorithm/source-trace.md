# 全局溯源

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/source_trace/` 下的全局报告溯源能力，包括待引用内容识别、搜索记录预处理、来源匹配、citation 校验、参考文献追加、来源域名映射和前端 citation 数据组织。

本文档不覆盖用户反馈后的局部溯源和推理链溯源。子能力细节见：

- [溯源内容识别](./source-trace/content-recognition.md)
- [来源匹配](./source-trace/source-matching.md)
- [Citation 校验](./source-trace/citation-checking.md)
- [参考文献生成](./source-trace/reference-generation.md)
- [域名来源映射](./source-trace/domain-source-mapping.md)

## 功能目的

全局溯源用于在报告生成后，为报告句子添加来源引用，并把引用信息整理为前端可展示的数据。它将报告文本、classified contents 和搜索记录转换为 citation 标记、reference 列表和 citation message data。

## 可见行为

- 空报告或无 classified content 时可跳过溯源。
- 报告会先移除参考文献章节，并将已有引用转换为内部 citation 标记。
- 系统识别需要引用的句子，再从搜索记录中匹配支持来源。
- 生成的 `[source_tracer_result]` 引用会经过有效性校验，无效引用会被移除。
- 最终报告追加参考文献，并输出前端 citation data。

## 关键代码路径

- 溯源入口：`openjiuwen_deepsearch/algorithm/source_trace/source_tracer.py`
- 报告和搜索记录预处理：`openjiuwen_deepsearch/algorithm/source_trace/source_tracer_preprocessors.py`
- 内容识别：`openjiuwen_deepsearch/algorithm/source_trace/content_analyzer.py`
- 来源匹配：`openjiuwen_deepsearch/algorithm/source_trace/source_matcher.py`
- 匹配算法：`openjiuwen_deepsearch/algorithm/source_trace/source_match_algo.py`
- citation 校验：`openjiuwen_deepsearch/algorithm/source_trace/citation_checker_research.py`
- 参考文献追加：`openjiuwen_deepsearch/algorithm/source_trace/add_source.py`
- 来源域名映射：`openjiuwen_deepsearch/algorithm/source_trace/domain_source_mapping.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/content_recognition.md`
- `openjiuwen_deepsearch/algorithm/prompts/source_matching.md`

主要测试：

- `tests/algorithm/test_source_tracer.py`
- `tests/source_tracer/test_source_tracer.py`
- `tests/source_tracer/test_source_tracer_node.py`
- `tests/source_tracer/test_sub_source_tracer_node.py`
- `tests/source_tracer/test_source_tracer_preprocessors.py`
- `tests/source_tracer/test_content_analyzer.py`
- `tests/source_tracer/test_source_matcher.py`
- `tests/source_tracer/test_citation_checker_research.py`
- `tests/source_tracer/test_citation_verify_research.py`
- `tests/source_tracer/test_add_source.py`
- `tests/source_tracer/test_domain_source_mapping.py`

## 核心流程

1. `SourceTracer` 接收报告、classified content 和 LLM 模型名。
2. `transform_search_record` 将 classified_content 转换为搜索记录，兼容新旧字段名（`doc_url`→`url`、`doc_title`→`title`、`passage_text`→`original_content`），按 URL 去重。
3. 前置覆盖率检查判断是否需要生成新引用。
4. 报告和搜索记录预处理，移除参考文献区并裁剪过长内容。
5. 内容识别模块找出需要引用的句子。
6. 来源匹配模块按 source type 调用 LLM 或匹配算法生成候选引用。
7. `add_source` 将引用插入报告并生成 data items。
8. citation checker 校验并重建段落，只保留有效引用。
9. 参考文献区被追加或更新，前端 citation data 被组织输出。

## 数据契约与依赖

字段映射（passage-level 新字段名 → 溯源模块统一字段名）：

source_tracer 在 3 处对新旧字段名做了兼容映射，统一输出 `url`/`title`/`content`：

1. **`transform_search_record`**（`source_tracer.py`）：`doc_url`→`url`、`doc_title`→`title`、`passage_text`→`content`（或 `url`/`title`/`original_content` 旧字段名直接透传）
2. **`_build_citation_mapping`**（`source_tracer_preprocessors.py`）：`doc_title`→`title`、`doc_url`→`url`、`passage_text`→`content`（或旧字段名直接透传）
3. **`classify_search_record`**（`source_tracer_infer/infer_extract_info.py`）：`doc_title`→`title`、`doc_url`→`url`、`passage_text`→`content`（或旧字段名直接透传）

关键输入：

- `report`
- `classified_content`
- `llm_model_name`

关键中间数据：

- `search_record`
- `source_tracer_result`
- `citation_messages["data"]`
- `reference_index`

关键输出：

- 添加 citation 的报告正文。
- 前端 citation data。
- 参考文献列表。

## 边界与错误处理

- 覆盖率足够时可跳过生成，避免重复溯源。
- 无搜索记录或预处理失败时应降级返回原报告。
- Markdown URL 解析需要处理嵌套括号和未闭合链接。
- 图片引用不进入前端浮窗展示。
- URL scheme 必须校验，避免插入不安全链接。
- 敏感日志模式下不输出报告正文、来源正文或 citation 片段。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer
```

## 相关文档

- [溯源内容识别](./source-trace/content-recognition.md)
- [来源匹配](./source-trace/source-matching.md)
- [Citation 校验](./source-trace/citation-checking.md)
- [参考文献生成](./source-trace/reference-generation.md)
- [域名来源映射](./source-trace/domain-source-mapping.md)
- [报告生成](./report-generation.md)
- [资料采集](./research-collector.md)
- [推理链溯源](./source-tracer-infer.md)
- [用户反馈处理](./user-feedback-processor.md)
- [局部溯源](./user-feedback-processor/local-source-trace.md)
