# 用户反馈处理

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/user_feedback_processor/` 下的报告生成后用户反馈处理能力，包括：

- 前端 action 到后端处理器的统一映射与分发。
- 同义改写、补充检索、新增任务、真实性核验、整篇同步和完成动作的总览。
- 选区 offset、报告 markup、局部溯源、结果下发和历史更新相关的共享契约。

本文档不覆盖前端 UI 交互实现、完整报告生成流程、通用信息采集算法内部实现、全局溯源算法内部实现。子能力细节见：

- [同义改写](./user-feedback-processor/synonym-rewrite.md)
- [补充检索与改写](./user-feedback-processor/supplementary-search.md)
- [真实性核验](./user-feedback-processor/truth-verification.md)
- [新增任务处理](./user-feedback-processor/new-task-processing.md)
- [局部溯源](./user-feedback-processor/local-source-trace.md)

`sync` 和 `finish` 是轻量会话控制动作，目前只在本文档总览中说明，不单独拆分子文档。

## 功能目的

用户反馈处理用于在报告生成完成后，接收前端针对报告内容的二次操作请求，并将这些请求转换为后端可执行的动作。它让用户可以对已生成报告进行局部改写、补充检索后改写、新增任务扩展、真实性核验、整篇同步和完成交互。

该能力的主要调用方是工作流节点和前端报告编辑交互。算法层负责解析 action、校验选区、调用对应子处理器、生成新的报告快照，并在需要时对改写结果执行局部溯源。

## 可见行为

前端通过 JSON payload 传入 `action` 和选区信息后，后端先将 action 归类，
再分发到对应能力：

- 同义改写：包含 `expand`、`shorten`、`polish` 三个 action，对选中文本进行扩写、缩写或润色，并返回可替换选区的文本。
- 补充检索与改写：由 `supplementary_search` 触发，围绕选区或相关章节补充检索资料，并用新资料改写报告内容。
- 新增任务处理：由 `new_task` 触发，根据用户新增任务补充报告内容。
- 真实性核验：由 `truth_verification` 触发，对选中段落做真实性核验，返回核验结论和证据摘要。
- 会话控制：包含 `sync` 和 `finish`，分别用于同步前端整篇报告内容和结束反馈交互流程。

改写类动作会返回更新后的完整报告、原始替换区间、清理 markup 后的原始文本、改写文本和改写后 offset。开启局部溯源时，改写结果还会经过差异感知的 citation 更新。

## 关键代码路径

核心入口：

- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/user_feedback_processor.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/action_definitions.py`

子能力实现：

- `openjiuwen_deepsearch/algorithm/user_feedback_processor/synonym_rewrite.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/supplementary_search.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/new_task_processor.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/truth_verification.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/local_source_trace.py`

共享工具：

- `openjiuwen_deepsearch/algorithm/user_feedback_processor/common.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/history.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/report_edit_utils.py`
- `openjiuwen_deepsearch/algorithm/user_feedback_processor/section_locator.py`

相关 Prompt 由各子能力文档分别列出。总览层主要测试：

- `tests/user_feedback_processor/test_processor.py`
- `tests/user_feedback_processor/test_workflow_integration.py`

## 核心流程

1. 前端或工作流节点传入原始用户反馈 JSON。
2. `parse_feedback` 将原始字符串解析为 `feedback` 字典。
3. `UserFeedbackProcessorNode` 会在进入 `UserFeedbackProcessor.execute` 前截获 `finish`，通知前端结束反馈并路由到 `EndNode`。
4. 其他 action 先由 `validate` 检查基础字段、offset、选区文本和 action 级参数。
5. `execute` 读取 `feedback["action"]`，通过 `USER_INPUT_ACTION_MAP` 识别动作大类。
6. `execute` 分发到同义改写、补充检索、新增任务、真实性核验或同步分支。
7. 改写类动作返回 `new_report`、替换区间、改写文本和 offset。
8. 开启 `enable_local_source_trace` 时，改写结果进入 `apply_local_source_trace_to_action_result` 更新 citation。
9. `build_stream_result` 将 action 结果转换为前端流式输出需要的结构。
10. `send_result` 按动作大类调用对应发送函数，将结果写回会话或下发给前端。

## 数据契约与依赖

`feedback` 至少依赖以下字段：

- `action`：前端动作字符串，必须存在于 `USER_INPUT_ACTION_MAP`。
- `selected_text`：前端选中的报告文本；`sync` 动作中表示整篇最新报告。
- `start_offset` / `end_offset`：选区在当前报告正文中的字符偏移。
- `user_instruction`：用户对本次反馈动作的补充要求，可为空字符串。

部分动作依赖额外字段：

- `supplementary_search` 可使用 `rewrite_scope` 区分只改写选区或联动相关章节。
- `truth_verification` 会把选中文本清理后取首段作为待核验段落。
- `sync` 使用 `selected_text` 作为整篇报告内容，不调用 LLM 子处理器。

`final_result` 至少需要提供 `response_content` 作为当前报告正文。部分路径还依赖报告 metadata、子报告资料、分类资料或 session 中的采集配置。

Prompt 契约只记录输入输出约定，不复制 Prompt 全文：

- 同义改写 Prompt 接收 `original_text`、`language`、`user_instruction`，输出可直接替换选区的纯文本片段。
- 补充检索 Prompt 生成检索任务，并根据补充资料输出改写后的报告片段或章节内容。
- 新增任务 Prompt 评估任务资料充分性并生成章节改写内容。
- 真实性核验 Prompt 评估待核验段落与证据的支持关系，输出核验结论、展示文本、证据和是否需要补充搜索。

外部依赖包括当前 LLM 上下文、工作流 session、采集执行服务、报告中的 citation/reference 结构和日志敏感信息开关。

## 边界与错误处理

重要边界：

- `action` 不在 `USER_INPUT_ACTION_MAP` 时，应抛出无效 action 对应的自定义异常。
- offset 必须落在当前报告正文范围内，且与 `selected_text` 对应。
- 改写前会剥离选区内 citation / inference 等 markup，避免模型改写结构化引用标记。
- `truth_verification` 的待核验段落为空时，应按无效参数处理。
- 局部溯源可通过 `enable_local_source_trace` 关闭；关闭时改写结果直接返回。
- 追加参考文献时使用 append-only 策略，避免重排既有引用编号。
- 敏感日志开关开启时，不应记录原文、改写文本或用户指令等敏感内容。

涉及异常和状态码时，优先使用 `openjiuwen_deepsearch/common/status_code.py` 中已有的 `USER_FEEDBACK_PROCESSOR_*` 状态码和 `openjiuwen_deepsearch/common/exception.py` 中的自定义异常类型。

## 测试与验证

修改用户反馈处理能力时，优先运行相关子能力测试：

```bash
uv run pytest tests/user_feedback_processor/test_processor.py
uv run pytest tests/user_feedback_processor/test_rewrite.py
uv run pytest tests/user_feedback_processor/test_supplementary_search.py
uv run pytest tests/user_feedback_processor/test_new_task_processor.py
uv run pytest tests/user_feedback_processor/test_truth_verification.py
uv run pytest tests/user_feedback_processor/test_local_source_trace.py
```

如果改动影响 offset、markup 剥离、章节定位或历史记录，还应运行：

```bash
uv run pytest tests/user_feedback_processor/test_report_edit_utils.py
uv run pytest tests/user_feedback_processor/test_section_locator.py
uv run pytest tests/user_feedback_processor/test_history.py
```

如果改动影响工作流节点集成，还应运行：

```bash
uv run pytest tests/user_feedback_processor/test_workflow_integration.py
```

纯文档更新可使用以下命令检查关键路径和链接文本是否保留：

```bash
rg -n "USER_INPUT_ACTION_MAP|UserFeedbackProcessor|enable_local_source_trace|test_processor.py" docs/feature/algorithm/user-feedback-processor.md
```

## 相关文档

- [同义改写](./user-feedback-processor/synonym-rewrite.md)
- [补充检索与改写](./user-feedback-processor/supplementary-search.md)
- [真实性核验](./user-feedback-processor/truth-verification.md)
- [新增任务处理](./user-feedback-processor/new-task-processing.md)
- [局部溯源](./user-feedback-processor/local-source-trace.md)
- [开发指南](../../zh/4.开发指南/README.md)
- [目录结构](../../zh/4.开发指南/directory_structure.md)
