# 同义改写

## 维护范围

本文档覆盖 `user_feedback_processor` 中的同义改写能力，即前端 `expand`、`shorten`、`polish` 三类 action 到 `SynonymRewriter` 的处理链路。

本文档不覆盖补充检索、新增任务、真实性核验和局部溯源内部算法；这些能力分别由其他 feature 文档或总览文档描述。

## 功能目的

同义改写用于在报告生成完成后，对用户选中的已有报告文本执行扩写、缩写或润色。它只生成可替换选区的正文片段，不负责重新规划报告结构，也不主动补充外部资料。

## 可见行为

前端传入 `expand`、`shorten` 或 `polish` 后，后端会剥离选区内的 citation / inference 等 markup，再调用对应 Prompt 生成新文本。返回结果包含更新后的完整报告、原始选区、清理后的原始文本、改写文本和改写后的 offset。

开启局部溯源时，同义改写结果会继续进入局部溯源阶段更新 citation；关闭时直接返回改写结果。

## 关键代码路径

- Action 映射：`openjiuwen_deepsearch/algorithm/user_feedback_processor/action_definitions.py`
- 主入口：`openjiuwen_deepsearch/algorithm/user_feedback_processor/user_feedback_processor.py`
- 核心实现：`openjiuwen_deepsearch/algorithm/user_feedback_processor/synonym_rewrite.py`
- Markup 清理：`openjiuwen_deepsearch/algorithm/user_feedback_processor/report_edit_utils.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/synonym_rewrite_expand.md`
- `openjiuwen_deepsearch/algorithm/prompts/synonym_rewrite_polish.md`
- `openjiuwen_deepsearch/algorithm/prompts/synonym_rewrite_shorten.md`

主要测试：

- `tests/user_feedback_processor/test_rewrite.py`
- `tests/user_feedback_processor/test_processor.py`

## 核心流程

1. `UserFeedbackProcessor.execute` 判断 `feedback["action"]` 是否属于 `SYNONYM_REWRITE_ACTIONS`。
2. `SynonymRewriter.synonym_rewrite` 读取 `action`、`start_offset`、`end_offset`、`selected_text` 和 `user_instruction`。
3. `strip_markup_in_range` 移除选区内结构化标记，得到可交给模型改写的纯文本。
4. `get_prompt_name` 将 `expand`、`shorten`、`polish` 映射到对应 Prompt。
5. LLM 返回可替换选区的改写文本。
6. 处理器拼接出 `new_report`，计算 `rewritten_start_offset` 与 `rewritten_end_offset`。
7. 如果启用局部溯源，结果继续进入 `apply_local_source_trace_to_action_result`。

## 数据契约与依赖

`feedback` 依赖字段：

- `action`：只能是 `expand`、`shorten` 或 `polish`。
- `selected_text`：前端选中文本。
- `start_offset` / `end_offset`：选区在当前报告正文中的位置。
- `user_instruction`：用户补充要求，可为空。

Prompt 契约：

- 输入变量：`original_text`、`language`、`user_instruction`。
- 输出格式：纯文本片段，可直接替换选区。
- 不要求 JSON 输出。
- 不应生成 citation / inference 标记；选区内原有标记由调用前处理剥离。

外部依赖：

- 当前 LLM 上下文中的用户反馈处理模型。
- 日志敏感信息开关。
- 可选的局部溯源处理链路。

## 边界与错误处理

- 未知同义改写 action 会按无效 action 处理。
- offset 必须与当前报告正文匹配，否则校验阶段应拦截。
- LLM 调用异常会转换为用户反馈处理相关自定义异常。
- 敏感日志模式下，不应记录原文和改写文本。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/user_feedback_processor/test_rewrite.py
uv run pytest tests/user_feedback_processor/test_processor.py
```

如果改动影响局部溯源接入，还应运行：

```bash
uv run pytest tests/user_feedback_processor/test_local_source_trace.py
```

## 相关文档

- [用户反馈处理总览](../user-feedback-processor.md)
- [补充检索与改写](./supplementary-search.md)
- [真实性核验](./truth-verification.md)
- [新增任务处理](./new-task-processing.md)
- [局部溯源](./local-source-trace.md)
