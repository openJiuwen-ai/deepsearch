# 真实性核验

## 维护范围

本文档覆盖 `user_feedback_processor` 中的 `truth_verification` action，包括待核验段落提取、章节资料匹配、证据评估、必要时补充检索以及前端核验结果下发。

本文档不覆盖全局报告溯源、通用 collector 内部实现，也不覆盖报告正文改写类 action。

## 功能目的

真实性核验用于在用户选中报告中的某段内容后，评估该段内容是否有当前章节资料支持，并向前端返回核验结论、展示文本和证据摘要。它不直接改写报告正文。

## 可见行为

前端传入 `truth_verification` 后，后端会清理选区 markup，并取选中文本首个非空段落作为待核验段落。系统会优先使用当前章节已有资料进行评估；证据不足时可生成补充检索任务并采集更多资料，再产出核验结论。

a返回给前端的结果是核验展示文本，不是替换报告正文的改写片段。流式 `SUMMARY_RESPONSE` 的 `content` 为 JSON，包含 `display_text` 与 `feedback_interaction_count`。

## 关键代码路径

- Action 映射：`openjiuwen_deepsearch/algorithm/user_feedback_processor/action_definitions.py`
- 主入口：`openjiuwen_deepsearch/algorithm/user_feedback_processor/user_feedback_processor.py`
- 核心实现：`openjiuwen_deepsearch/algorithm/user_feedback_processor/truth_verification.py`
- 章节定位：`openjiuwen_deepsearch/algorithm/user_feedback_processor/section_locator.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/truth_verification_assessment.md`
- `openjiuwen_deepsearch/algorithm/prompts/truth_verification_search_task.md`

主要测试：

- `tests/user_feedback_processor/test_truth_verification.py`
- `tests/user_feedback_processor/test_processor.py`
- `tests/user_feedback_processor/test_section_locator.py`

## 核心流程

1. `UserFeedbackProcessor.execute` 将 `truth_verification` 分发给 `TruthVerificationProcessor`。
2. 处理器从 `final_result["response_content"]` 中剥离选区内 markup。
3. `extract_verified_paragraph` 取清理后选中文本的首段作为待核验段落。
4. `_collect_section_doc_infos` 定位选区所属章节，并匹配当前报告中的章节资料。
5. `_assess_paragraph_with_docs` 调用评估 Prompt，输出结论、展示文本、证据和是否需要补充检索。
6. 如果证据不足且需要更多资料，处理器生成补充检索任务并调用采集服务。
7. 处理器返回用于前端展示的核验结果。

## 数据契约与依赖

`feedback` 依赖字段：

- `action`：固定为 `truth_verification`。
- `selected_text`：待核验选区。
- `start_offset` / `end_offset`：选区位置。
- `user_instruction`：用户补充核验要求，可为空。

`final_result` 依赖：

- `response_content`：当前报告正文。

`current_report` 依赖：

- `sub_reports`：用于根据章节标题匹配已有章节资料。
- `all_classified_contents`：在子报告缺少 classified content 时作为回退资料来源。

Prompt 契约：

- `truth_verification_assessment` 输出 JSON 语义的数据，包含结论、展示文本、证据和是否需要补充检索。
- 允许结论集合由代码侧白名单约束；未知结论应回退为证据不足。
- `truth_verification_search_task` 输出可交给 collector 的检索任务文本。

流式 `SUMMARY_RESPONSE` 的 `content` JSON 字段：

- `display_text`：Markdown 核验展示文本。
- `feedback_interaction_count`：本次核验完成后的反馈交互计数，与改写类动作语义一致。

外部依赖：

- 当前 LLM 上下文。
- 工作流 session 和 collector model context。
- `CollectorExecutionService`。

## 边界与错误处理

- 清理后的待核验段落为空时，应抛出无效参数异常。
- 无章节资料时，应返回保守的证据不足结论，而不是伪造证据。
- LLM 输出无法解析或缺失展示文本时，应回退到按结论生成的展示文本。
- 证据列表需要归一化，并限制前端展示数量。
- 敏感日志模式下，不应记录待核验原文或用户指令。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/user_feedback_processor/test_truth_verification.py
uv run pytest tests/user_feedback_processor/test_processor.py
```

如果改动影响章节定位或资料匹配，还应运行：

```bash
uv run pytest tests/user_feedback_processor/test_section_locator.py
```

## 相关文档

- [用户反馈处理总览](../user-feedback-processor.md)
- [同义改写](./synonym-rewrite.md)
- [补充检索与改写](./supplementary-search.md)
- [新增任务处理](./new-task-processing.md)
