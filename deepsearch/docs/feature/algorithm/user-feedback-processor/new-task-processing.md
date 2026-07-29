# 新增任务处理

## 维护范围

本文档覆盖 `user_feedback_processor` 中的 `new_task` action，包括目标章节解析、历史资料复用、增量采集、编辑策略选择、章节改写和新增小节。

本文档不覆盖完整报告初始生成流程，也不覆盖通用 collector 内部实现、同义改写、补充检索和真实性核验。

## 功能目的

新增任务处理用于在报告生成完成后，根据用户的新要求对已有报告进行章节级扩展。它会尽量复用历史资料；资料不足时执行增量研究，再根据评估结果修改已有小节或追加新小节。

## 可见行为

前端传入 `new_task` 后，后端定位用户选区所在的目标章节，并根据用户指令判断如何编辑报告：

- 修改已有小节。
- 在目标大章节下追加新小节。
- 在资料不足时先执行增量采集再改写。

返回结果包含更新后的完整报告、原始区间、改写区间、证据资料和增量研究信息。开启局部溯源时，改写结果继续更新 citation。

## 关键代码路径

- Action 映射：`openjiuwen_deepsearch/algorithm/user_feedback_processor/action_definitions.py`
- 主入口：`openjiuwen_deepsearch/algorithm/user_feedback_processor/user_feedback_processor.py`
- 核心实现：`openjiuwen_deepsearch/algorithm/user_feedback_processor/new_task_processor.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/new_task_assessment.md`
- `openjiuwen_deepsearch/algorithm/prompts/new_task_rewrite_section.md`

主要测试：

- `tests/user_feedback_processor/test_new_task_processor.py`
- `tests/user_feedback_processor/test_processor.py`
- `tests/user_feedback_processor/test_history.py`

## 核心流程

1. `UserFeedbackProcessor.execute` 将 `new_task` 分发给 `NewTaskProcessor`。
2. `resolve_target_section` 根据选区 offset 和报告标题结构定位目标章节。
3. `collect_section_assets` 从当前大纲、历史计划和已有资料中收集可复用证据。
4. `assess_section_assets` 判断资料是否足够，并给出编辑策略。
5. 资料不足时，`build_incremental_plan` 和 `run_incremental_collection` 执行增量研究。
6. `_apply_new_task_edit_strategy` 选择修改已有小节或追加新小节。
7. 处理器校验改写结果的标题层级、章节标题和边界。
8. `_build_new_task_result` 返回更新后的报告、区间、证据和增量研究信息。
9. 如果启用局部溯源，结果继续进入局部溯源阶段。

## 数据契约与依赖

`feedback` 依赖字段：

- `action`：固定为 `new_task`。
- `selected_text`：选区文本。
- `start_offset` / `end_offset`：选区位置。
- `user_instruction`：新增任务要求。

`final_result` 依赖：

- `response_content`：当前报告正文。
- 可能包含历史研究资料、子报告和 metadata。

运行时依赖：

- 当前大纲和历史计划。
- 工作流 session 中的采集配置。
- 当前 collector model context。
- `CollectorExecutionService`。

Prompt 契约：

- `new_task_assessment` 判断历史资料是否足够，并输出编辑策略。
- `new_task_rewrite_section` 根据目标章节、用户任务和证据资料输出章节或小节正文。
- 改写结果必须保持目标标题层级和章节结构约束。

## 边界与错误处理

- 无法定位目标章节时，应抛出用户反馈处理异常。
- 资料不足且增量采集不可用时，应返回明确错误，不应生成无证据改写。
- 改写结果为空、改变目标标题层级、缺失原章节标题或包含意外更高层级标题时，应拒绝结果。
- 追加新小节时需要生成稳定的小节标题，并避免破坏后续章节。
- 敏感日志模式下，不应记录用户任务、证据全文或改写正文。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/user_feedback_processor/test_new_task_processor.py
uv run pytest tests/user_feedback_processor/test_processor.py
uv run pytest tests/user_feedback_processor/test_history.py
```

如果改动影响 citation 更新，还应运行：

```bash
uv run pytest tests/user_feedback_processor/test_local_source_trace.py
```

## 相关文档

- [用户反馈处理总览](../user-feedback-processor.md)
- [同义改写](./synonym-rewrite.md)
- [补充检索与改写](./supplementary-search.md)
- [真实性核验](./truth-verification.md)
- [局部溯源](./local-source-trace.md)
