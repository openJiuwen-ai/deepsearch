# LLM 调用辅助

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/utils/common_utils/llm_utils.py` 中的 LLM 调用、流式聚合、响应归一化、JSON 修复、token 统计、
agent 级超时和 thinking fallback 激活逻辑。

不覆盖 LLM 对象创建和厂商 extension 注入；该部分见 [LLM 运行时封装](../llm/llm-runtime.md)。

## 功能目的

LLM 调用辅助为算法和 framework 节点提供统一调用入口，使调用方可以用同一套函数处理普通响应、流式响应、工具调用、token 统计、
超时控制和厂商错误格式。

## 可见行为

- `ainvoke_llm_with_stats` 是主要异步调用入口，支持位置参数和关键字参数兼容。
- `llm_astream` 会把流式 chunk 输出到 session custom stream，并聚合为最终响应。
- 开启 `stats_info_llm` 时，会记录 workflow 级 token usage。
- 单次 `[LLM CALL STATISTICS]` 日志同时输出 `cache_tokens`，表示缓存读取/命中的 token，
  不包含缓存创建或写入量，也不会额外加到 input/total token 中。
- workflow 总计和 `agent_name_token_usage` 按 agent 累计 `cache_tokens`，并随 session 快照保存、恢复。
  汇总输出中的 `cache_tokens` 紧接在 `total_tokens` 后面。
- `agent_llm_timeouts` 支持按 `agent_name` 精确匹配、节点级前缀匹配和 `default` 回退。
- 工具调用响应会被归一化，并修复可重放的 function arguments。
- LLM 返回 JSON 字符串时，提供提取、规范化和 Pydantic schema 修复能力。
- `safe_float` 对 LLM 返回的非数值分数/字符串数字做安全转换（失败时降级为 0.0），避免下游 `float()` 崩溃；report 模块的 coverage_matrix 消费和图表数值处理均依赖此守卫。
- thinking switch 被厂商拒绝时，调用层会切换到 fallback model 并在 session 中记住该状态。

## 关键代码路径

- `openjiuwen_deepsearch/utils/common_utils/llm_utils.py`
- `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`
- `openjiuwen_deepsearch/utils/constants_utils/session_contextvars.py`
- `openjiuwen_deepsearch/llm/llm_wrapper.py`
- `openjiuwen_deepsearch/llm/llm_request_adapter.py`
- `tests/utils/test_llm_utils.py`
- `tests/llm/test_llm_thinking.py`
- `tests/workflow/test_workflow_llm_usage_lifecycle.py`

## 核心流程

1. 调用方传入 LLM dict、messages、agent_name、tools、流式参数和可选超时配置。
2. 调用入口解析参数，并根据当前 session 的 `config.agent_llm_timeouts` 解析调用超时。
3. 根据 LLM dict 选择主模型或已激活的 thinking fallback 模型。
4. 流式路径通过 `llm_astream` 聚合 chunk，并按 custom stream 协议输出事件。
5. 非流式路径调用模型方法并统一响应结构。
6. 提取 usage 元数据，并在 `stats_info_llm` 开启时写入 workflow usage。
7. 如果主模型报出 thinking switch 不支持错误且 fallback 可用，激活 fallback 并重试。

## 数据契约与依赖

- LLM dict 至少需要 `model` 和 `model_name`；thinking fallback 还使用 `thinking_fallback_model`、`thinking_fallback_key`、
  `thinking_fallback_removed_fields`、`thinking_fallback_active`。
- workflow usage 使用 `llm_usage:<session_id>` 全局缓存，并可同步到 session。
- session thinking fallback registry 使用 `llm_runtime.thinking_fallback_active_keys`。
- `AgentLlmName` 是 agent_name 的集中事实源，供超时配置、统计和测试复用。
- LLM token usage 字段归一化为 input/output/total 三类非负整数。
- 缓存读取量兼容 SDK `cache_tokens`、DeepSeek `prompt_cache_hit_tokens`、
  `prompt_tokens_details.cached_tokens`、`input_tokens_details.cached_tokens`、
  `input_token_details.cache_read` 和 `cache_read_input_tokens`；也支持嵌套 `token_usage`。
- usage 中缺失或无效的缓存字段在单次日志中显示 `None`，不参与缓存累计；旧快照不强行补零。
  部分底层 SDK 会把未上报的缓存量默认设为 `0`，此时本层无法区分默认零与真实零命中。
  对缺失统计或混合供应商的汇总，不应直接将缓存总量除以所有输入量解释为完整命中率。

## 边界与错误处理

- 调用点必须提供有效 `agent_name`，否则会在校验阶段报错。
- LLM 实例为空时抛出 `LLM_INSTANCE_NONE_ERROR`。
- cancellation 会被单独识别，不应被包装成普通 LLM 错误。
- fallback 重试失败时保留异常链，方便同时定位主请求失败和 fallback 失败。
- 敏感日志模式下，错误详情和消息内容会被脱敏或截断。

## 测试与验证

- `uv run pytest tests/utils/test_llm_utils.py`
- `uv run pytest tests/utils/test_llm_cache_usage.py`
- `uv run pytest tests/llm/test_llm_thinking.py`
- 修改 workflow token 生命周期时，补充运行 `uv run pytest tests/workflow/test_workflow_llm_usage_lifecycle.py`。

## 相关文档

- [LLM 运行时封装](../llm/llm-runtime.md)
- [LLM 模型槽位适配](../framework/llm-model-adaptation.md)
- [报告研究主工作流](../framework/research-workflow.md)
