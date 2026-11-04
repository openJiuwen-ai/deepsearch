# Citation 校验

## 维护范围

本文档覆盖全局溯源中的 citation 校验能力，包括解析 `[source_tracer_result]` 引用、验证引用有效性、重建段落和组织前端 citation data。

本文档不覆盖来源匹配和参考文献追加。

## 功能目的

Citation 校验用于保证报告中插入的引用真实有效、URL 安全、展示数据可被前端消费。它负责移除无效引用，并把有效引用整理为 `citation_messages`。

## 可见行为

- 只保留校验通过的文本引用。
- 图片引用不进入前端浮窗数据。
- 无效或无法解析的 citation 会从段落中移除。
- LLM 返回仅有 JSON 语法问题时会先进行本地格式恢复，恢复结果仍需满足引用数量、字段和原文片段校验。
- LLM 批次失败会在每条引用最多三次调用的限制内二分降批；最终失败的普通引用会移除，图表引用保持现有兜底展示。
- 输出给前端的数据包含 URL、标题、正文片段、来源、发布时间、分数和 reference index 等字段。

## 关键代码路径

- Citation 校验：`openjiuwen_deepsearch/algorithm/source_trace/citation_checker_research.py`
- 引用验证：`openjiuwen_deepsearch/algorithm/source_trace/citation_verify_research.py`
- Markdown URL 解析依赖：`openjiuwen_deepsearch/utils/common_utils/markdown_url_utils.py`

主要测试：

- `tests/source_tracer/test_citation_checker_research.py`
- `tests/source_tracer/test_citation_verify_research.py`
- `tests/source_tracer/test_citation_verify_routing.py`

## 核心流程

1. 校验器扫描报告中的 `[source_tracer_result]` Markdown 引用。
2. URL 使用栈式解析，避免括号嵌套导致截断。
3. 每条引用被发送到验证流程判断是否支持当前位置文本；LLM 结果先经 JSON 格式恢复，再进行严格校验。
4. 失败批次会按中点拆分重试；成功子批次立即保留，普通引用最终失败时标记为无效。
5. 段落按校验结果重建，只保留有效引用；图表引用保留既有来源与分数兜底。
6. 有效引用被转换为前端 citation data。

## 数据契约与依赖

输入：

- 含 `[source_tracer_result]` 的报告段落。
- 来源 data items。
- LLM verifier。
- `json-repair`：用于恢复常见 JSON 语法问题，不用于改写引用语义。

输出：

- 重建后的段落文本。
- `citation_messages` 前端数据。

## 边界与错误处理

- URL 未闭合时该引用保持或跳过，不应误删周边正文。
- Markdown 链接标题需要转义，避免破坏链接结构。
- 图片引用和 invalid data 不进入前端 citation 列表。
- JSON 恢复后只要不是对象数组、数量不符、缺少必填字段或标记片段不能回查原文，就视为本次 LLM 校验失败。
- 重试日志记录失败类别、层级、拆分路径与批次大小；敏感日志模式下不输出原始 LLM 响应或引用正文。
- 图表引用在 LLM 最终失败后仍使用既有的域名来源和最低 0.85 分数兜底。
- 敏感日志模式下不输出完整段落和引用正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer/test_citation_checker_research.py
uv run pytest tests/source_tracer/test_citation_verify_research.py
uv run pytest tests/source_tracer/test_chart_citation.py
```

## 相关文档

- [全局溯源总览](../source-trace.md)
- [来源匹配](./source-matching.md)
- [参考文献生成](./reference-generation.md)
