# 补充检索与改写

## 维护范围

本文档覆盖 `user_feedback_processor` 中的 `supplementary_search` action，包括补充检索任务生成、资料采集、选区改写和相关章节改写。

本文档不覆盖通用 collector 图的内部实现，也不覆盖同义改写、新增任务和真实性核验。

## 功能目的

补充检索与改写用于在用户认为报告某段内容需要更多资料支撑时，围绕选区生成检索任务，采集新的资料，再用补充资料改写选区或相关章节。

## 可见行为

前端传入 `supplementary_search` 后，后端根据 `rewrite_scope` 选择处理方式：

- `selected_only`：只替换用户选中的文本。
- `selected_and_related`：定位包含选区的最小章节块，并改写该章节内容。

返回结果会包含更新后的完整报告、原始替换区间、改写文本、改写 offset、章节区间和采集摘要。`selected_only` 的替换区间等于用户选区；`selected_and_related` 的替换区间是包含选区的最小章节块。开启局部溯源时，改写结果继续更新 citation。

## 关键代码路径

- Action 映射：`openjiuwen_deepsearch/algorithm/user_feedback_processor/action_definitions.py`
- 主入口：`openjiuwen_deepsearch/algorithm/user_feedback_processor/user_feedback_processor.py`
- 核心实现：`openjiuwen_deepsearch/algorithm/user_feedback_processor/supplementary_search.py`
- 章节定位：`openjiuwen_deepsearch/algorithm/user_feedback_processor/section_locator.py`
- 采集执行：`openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/collector_execution_service.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/supplementary_search_task.md`
- `openjiuwen_deepsearch/algorithm/prompts/supplementary_search_rewrite_selected_only.md`
- `openjiuwen_deepsearch/algorithm/prompts/supplementary_search_rewrite_selected_and_related.md`

主要测试：

- `tests/user_feedback_processor/test_supplementary_search.py`
- `tests/user_feedback_processor/test_processor.py`
- `tests/user_feedback_processor/test_section_locator.py`

## 核心流程

1. `UserFeedbackProcessor.execute` 将 `supplementary_search` 分发给 `SupplementarySearcher`。
2. `SupplementarySearcher.supplementary_search` 读取 `rewrite_scope`，默认使用 `selected_only`。
3. 处理器定位选区或 enclosing section，并剥离需要改写范围内的 markup。
4. `_build_research_task` 根据选区、章节上下文和用户要求生成补充检索任务。
5. `_run_collection` 调用采集执行服务获取补充资料。
6. 根据 scope 调用 selected-only 或 selected-and-related Prompt 生成改写内容。
7. 处理器拼接 `new_report`，返回改写范围、采集摘要和补充资料信息。
8. 如果启用局部溯源，结果继续进入局部溯源阶段。

## 数据契约与依赖

`feedback` 依赖字段：

- `action`：固定为 `supplementary_search`。
- `selected_text`：用户选中文本。
- `start_offset` / `end_offset`：选区位置。
- `rewrite_scope`：可选，支持 `selected_only` 和 `selected_and_related`。
- `user_instruction`：用户补充要求，可为空。

`final_result` 依赖：

- `response_content`：当前报告正文。
- 报告 metadata、子报告资料或分类资料：用于提供章节上下文和可复用证据。

Prompt 契约：

- 检索任务 Prompt 输出一段可交给 collector 的 research task。
- selected-only 改写 Prompt 输出只替换选区的正文片段。
- selected-and-related 改写 Prompt 输出整个目标章节的新正文。
- 不复制 Prompt 全文，变更 Prompt 变量或输出格式时需同步更新本文档和测试。

外部依赖：

- 工作流 session 中的采集配置。
- 当前 collector model context。
- `CollectorExecutionService`。
- 局部溯源链路。

## 边界与错误处理

- `rewrite_scope` 未提供时默认走 `selected_only`。
- selected-and-related 需要稳定定位最小 enclosing 章节，章节边界变化应覆盖测试。
- 采集服务需要 session 和模型上下文；缺失时应转为用户反馈处理异常。
- 改写结果应保持报告结构，不应吞掉下一章节标题或章节间隔。
- 敏感日志模式下，不应记录用户选区、检索任务或改写文本。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/user_feedback_processor/test_supplementary_search.py
uv run pytest tests/user_feedback_processor/test_processor.py
uv run pytest tests/user_feedback_processor/test_section_locator.py
```

如果改动影响 citation 更新，还应运行：

```bash
uv run pytest tests/user_feedback_processor/test_local_source_trace.py
```

## 相关文档

- [用户反馈处理总览](../user-feedback-processor.md)
- [同义改写](./synonym-rewrite.md)
- [真实性核验](./truth-verification.md)
- [新增任务处理](./new-task-processing.md)
- [局部溯源](./local-source-trace.md)
