# 局部溯源

## 维护范围

本文档覆盖 `user_feedback_processor` 中的局部溯源后处理能力，即 `local_source_trace.py` 对改写类 action 结果执行差异感知 citation 更新的流程。

局部溯源不是前端 action。它由 `synonym_rewrite`、`supplementary_search`、`new_task` 等改写类动作在 `enable_local_source_trace` 开启时复用。本文档不覆盖完整报告生成后的全局溯源流程，也不覆盖 source trace 核心算法内部实现。

## 功能目的

局部溯源用于在报告局部内容被改写后，只对变化片段重新补充来源标记，并保留未变化文本中的既有 citation。它降低二次编辑后整篇报告重新溯源的成本，同时避免重排已有参考文献编号。

## 可见行为

改写类 action 返回 `action_result` 后，如果 `enable_local_source_trace=True`，处理器会调用局部溯源：

- 对原始文本和改写文本做差异切分。
- 保留未变化片段中的既有 checked citation。
- 对变化片段使用新增资料和被移除 citation 对应的既有资料进行局部溯源。
- 将局部 citation id 和 reference index 映射回整篇报告的全局编号。
- 以 append-only 策略追加新增参考文献。

如果局部溯源无法为变化片段找到资料，结果可以携带 warning，但不应阻塞改写结果返回。

## 关键代码路径

- 接入入口：`openjiuwen_deepsearch/algorithm/user_feedback_processor/user_feedback_processor.py`
- 核心实现：`openjiuwen_deepsearch/algorithm/user_feedback_processor/local_source_trace.py`
- Markup 清理：`openjiuwen_deepsearch/algorithm/user_feedback_processor/report_edit_utils.py`
- 溯源依赖：`openjiuwen_deepsearch/algorithm/source_trace/citation_checker_research.py`
- 参考文献处理：`openjiuwen_deepsearch/algorithm/source_trace/add_source.py`

主要测试：

- `tests/user_feedback_processor/test_local_source_trace.py`
- `tests/user_feedback_processor/test_processor.py`

## 核心流程

1. 改写类 action 生成 `action_result`，包含 `new_report`、原始区间、改写文本和可选资料列表。
2. `UserFeedbackProcessor.execute` 根据 `enable_local_source_trace` 决定是否调用局部溯源。
3. `apply_local_source_trace_to_action_result` 读取当前报告正文、已有 `citation_messages` 和参考文献列表。
4. `strip_markup_in_range` 清理原始被替换范围，并记录被移除的 citation。
5. `build_diff_segments` 比较原始文本和改写文本，区分未变化片段和变化片段。
6. 每个变化片段合并新增资料、被移除 citation 对应资料和已有 citation data。
7. `_run_local_source_trace_with_semaphore` 以并发上限调用 `run_local_source_trace`。
8. `apply_global_citation_numbering` 把局部 citation 编号映射为整篇报告的全局编号。
9. `append_reference_entries` 只追加新增 URL 的参考文献，不重排既有编号。
10. 返回增强后的 `action_result`，可能包含更新后的 `citation_messages` 和 `warning_info`。

## 数据契约与依赖

`action_result` 依赖字段：

- `new_report`：改写后的完整报告。
- `original_text` / `original_text_clean`：原始选区或章节文本。
- `original_start_offset` / `original_end_offset`：原始替换范围。
- `rewritten_text`：改写后文本。
- `rewritten_start_offset` / `rewritten_end_offset`：改写文本在新报告中的范围。
- `source_trace_doc_infos`：可选，改写或采集流程提供的新资料。

`final_result` 依赖字段：

- `response_content`：改写前的完整报告。
- `citation_messages`：已有 checked citation 数据。

局部溯源依赖：

- `CitationCheckerResearch`。
- `add_source_references`。
- 当前 LLM 模型名称和报告语言。
- `LOCAL_SOURCE_TRACE_MAX_CONCURRENCY` 控制变化片段并发溯源数量。

输出契约：

- 未变化片段应尽量保留原有 markup。
- 新 citation data 追加到已有 `citation_messages["data"]` 后。
- 新参考文献追加到报告末尾或参考文献区后，不覆盖已有编号。
- 发生可降级问题时通过 `warning_info` 暴露，而不是丢弃改写正文。

## 边界与错误处理

- 没有 source records 时，单段局部溯源返回 warning 和原文本。
- URL 已存在于参考文献映射中时，复用既有 reference index。
- URL 中包含括号或转义括号时，checked citation 解析和全局编号映射必须保持稳定。
- 未闭合或无法解析的 checked citation 标记不应被错误重写。
- 插入型改写没有原始文本时，应仍能对新增片段执行局部溯源。
- 改写片段靠近下一个标题时，应保留章节边界空白，避免标题和正文粘连。
- 并发执行的多个变化片段最终编号应按文档顺序稳定合并。

## 测试与验证

修改局部溯源时，优先运行：

```bash
uv run pytest tests/user_feedback_processor/test_local_source_trace.py
```

如果改动影响改写类 action 的接入，还应运行对应测试：

```bash
uv run pytest tests/user_feedback_processor/test_processor.py
uv run pytest tests/user_feedback_processor/test_rewrite.py
uv run pytest tests/user_feedback_processor/test_supplementary_search.py
uv run pytest tests/user_feedback_processor/test_new_task_processor.py
```

## 相关文档

- [用户反馈处理总览](../user-feedback-processor.md)
- [同义改写](./synonym-rewrite.md)
- [补充检索与改写](./supplementary-search.md)
- [新增任务处理](./new-task-processing.md)
