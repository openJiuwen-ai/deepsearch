# LLM 运行时封装

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/llm/` 下的统一 LLM 对象创建、请求 extension 适配、思考模式开关和思考开关 fallback 契约。

不覆盖 framework 节点到模型槽位的选择逻辑；该部分见 [LLM 模型槽位适配](../framework/llm-model-adaptation.md)。不覆盖每个算法
Prompt 如何使用 LLM。

## 功能目的

LLM 运行时封装把仓库内的 `LLMConfig` 转换成 openJiuwen 可调用的模型对象，并在不同厂商对“思考模式”参数不一致时，统一注入、
清理或降级处理请求参数，避免算法和 workflow 节点直接处理厂商差异。

## 可见行为

- `create_llm_obj` 返回 dict，包含主模型、模型名、思考 fallback 模型和 fallback 状态字段。
- 默认调用 `create_llm_obj(llm_config)` 时不注入思考开关，保留用户原始 `extension`。
- 显式传入 `thinking_enabled=True/False` 时，按厂商规则合并思考开关字段。
- `llm_thinking_enabled` 的默认值来自 `Config().service_config.llm_thinking_enabled`，当前默认关闭。
- 当关闭思考模式的参数被厂商拒绝时，调用层可切换到不带思考开关的 fallback model，并在同一 session 后续调用中复用该 fallback。
- 创建模型对象后会清理 `llm_config.api_key` 中的 `bytearray` secret。

## 关键代码路径

- `openjiuwen_deepsearch/llm/llm_wrapper.py`
- `openjiuwen_deepsearch/llm/llm_request_adapter.py`
- `openjiuwen_deepsearch/framework/openjiuwen/llm/llm_model_factory.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/utils/common_utils/llm_utils.py`
- `openjiuwen_deepsearch/config/config.py`
- `tests/llm/test_llm_thinking.py`
- `tests/search_agent/test_model_switch_runtime_config.py`

## 核心流程

1. 调用方把 `LLMConfig` 传给 `create_llm_obj`。
2. `validate_llm_obj_params` 校验模型配置。
3. 如果 `thinking_enabled` 为 `None`，直接使用原始 `extension`。
4. 如果 `thinking_enabled` 为布尔值，`merge_thinking_extension` 根据厂商规则复制并改写 `extension`。
5. `LLMModelFactory.get_model` 使用 provider、api key、base URL、超时、超参和 extension 创建主模型。
6. 当 `thinking_enabled=False` 且当前厂商规则会移除或注入思考字段时，同时创建 fallback model。
7. `ainvoke_llm_with_stats` 捕获“思考开关不支持”类错误后，激活 fallback model 并重试一次。
8. 激活后的 fallback key 会写入当前 workflow session，后续同 key LLM dict 直接使用 fallback model。

## 数据契约与依赖

- `LLMConfig` 提供 `model_name`、`model_type`、`base_url`、`api_key`、`hyper_parameters` 和 `extension`。
- `ServiceConfig.llm_timeout` 作为模型客户端超时时间。
- `ServiceConfig.llm_thinking_enabled` 控制 SDK 内部是否显式注入思考开关。
- `create_llm_obj` 返回字段：
  - `model`：主模型对象。
  - `model_name`：配置中的模型名。
  - `thinking_fallback_model`：移除思考开关后的 fallback 模型，可能为空。
  - `thinking_fallback_key`：基于 model_type、base_url、model_name 归一化后的稳定 key。
  - `thinking_fallback_removed_fields`：fallback 移除或避免注入的字段路径。
  - `thinking_fallback_active`：当前 LLM dict 是否已启用 fallback。
- 当前思考模式厂商规则覆盖 SiliconFlow、DashScope、华为 MaaS、DeepSeek/智谱/Kimi/火山等 thinking-type provider；MiniMax 标记为不支持。

## 边界与错误处理

- 未匹配或不支持的厂商规则不会改写原始 `extension`，只记录 warning。
- 已存在的思考开关字段会在支持规则下被清理，并由 `llm_thinking_enabled` 重新写入，避免同一请求中出现冲突字段。
- fallback extension 只继承用户原始 extension 中与思考开关无关的字段，不继承主请求临时注入字段。
- fallback 响应失败时保留原始异常链，便于定位第一次失败和 fallback 失败。
- fallback 是否启用写入 session 状态键 `llm_runtime.thinking_fallback_active_keys`。
- `base_url` 在 fallback key 中会规范化 scheme、host 和尾部斜杠，降低同一模型重复 key 的概率。

## 测试与验证

- `uv run pytest tests/llm/test_llm_thinking.py`
- 修改模型槽位与 workflow 初始化联动时，补充运行 `uv run pytest tests/search_agent/test_model_switch_runtime_config.py`。
- 修改 workflow 入口的 LLM 初始化时，补充运行 `uv run pytest tests/workflow/test_workflow_llm_usage_lifecycle.py`。

## 相关文档

- [LLM 模型槽位适配](../framework/llm-model-adaptation.md)
- [报告研究主工作流](../framework/research-workflow.md)
- [DeepSearch 搜索子工作流](../framework/deepsearch-sub-workflows.md)
- [Prompt 模板系统](../algorithm/prompt-template-system.md)
