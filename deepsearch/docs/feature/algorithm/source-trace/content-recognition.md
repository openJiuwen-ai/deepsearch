# 溯源内容识别

## 维护范围

本文档覆盖全局溯源中的待引用内容识别能力，即从报告正文中识别需要添加来源支持的句子或片段。

本文档不覆盖来源匹配、citation 校验、参考文献追加和用户反馈局部溯源。

## 功能目的

内容识别用于把完整报告收敛为需要溯源的候选句子，避免后续来源匹配对整篇报告做无差别处理。它是全局溯源链路的第一步，决定后续 citation 覆盖范围。

## 可见行为

- 报告会先去掉参考文献区并保留可溯源正文。
- 系统过滤标题、表格分隔符、代码块标记等非正文内容。
- 识别结果应对应报告中真实存在或可相似匹配的句子。
- 无有效正文时后续溯源可直接降级跳过。

## 关键代码路径

- 内容识别：`openjiuwen_deepsearch/algorithm/source_trace/content_analyzer.py`
- 报告预处理：`openjiuwen_deepsearch/algorithm/source_trace/source_tracer_preprocessors.py`
- 溯源入口：`openjiuwen_deepsearch/algorithm/source_trace/source_tracer.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/content_recognition.md`

主要测试：

- `tests/source_tracer/test_content_analyzer.py`
- `tests/source_tracer/test_source_tracer_preprocessors.py`
- `tests/source_tracer/test_source_tracer.py`

## 核心流程

1. 全局溯源入口接收报告正文。
2. 预处理移除参考文献区，并把已有引用转换为内部标记。
3. 内容识别 Prompt 或相似度逻辑产出候选句子。
4. 代码侧校验候选句子是否能在报告中定位或相似匹配。
5. 有效候选句子交给来源匹配阶段。

## 数据契约与依赖

输入：

- `modified_report` 或预处理后的报告正文。
- `similarity_threshold`。
- 当前 LLM 模型。

输出：

- 需要溯源的句子列表。

## 边界与错误处理

- 空报告、只有标题或只有表格分隔符时不应产生候选句子。
- LLM 输出中不存在于报告的句子需要通过相似度纠正，无法匹配时丢弃。
- 敏感日志模式下不输出完整报告正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer/test_content_analyzer.py
uv run pytest tests/source_tracer/test_source_tracer_preprocessors.py
```

## 相关文档

- [全局溯源总览](../source-trace.md)
- [来源匹配](./source-matching.md)
- [Citation 校验](./citation-checking.md)
