# LLM 模型槽位适配

## 维护范围

本文档覆盖 framework 层如何根据节点名选择 `AgentConfig.llm_config` 中的模型槽位，以及 legacy openJiuwen 模型工厂的适配行为。

不覆盖统一 LLM wrapper 的厂商请求细节；该部分属于 `openjiuwen_deepsearch/llm/`。

## 功能目的

模型槽位适配允许不同节点使用不同能力等级的模型，同时在缺少专用槽位时回退到通用模型，避免每个节点硬编码模型名。

## 可见行为

- `intent_recognition`、`outline`、`plan_reasoning` 优先使用 `plan_understanding`。
- `info_collector` 优先使用 `info_collecting`。
- `sub_reporter` 优先使用 `writing_checking`。
- `vlm_chart_generator` 优先使用 `vlm_chart_generating`。
- 节点没有映射或专用槽位缺失时，回退到 `general`。
- VLM 专用适配在专用槽位缺失时返回 `NO VLM`，而不是回退到普通文本模型。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/llm/llm_adapter.py`
- `openjiuwen_deepsearch/framework/openjiuwen/llm/llm_model_factory.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/editor_team_nodes.py`
- `openjiuwen_deepsearch/llm/llm_wrapper.py`
- `tests/search_agent/test_model_switch_runtime_config.py`
- `tests/utils/test_llm_utils.py`

## 核心流程

1. Agent 入口从 `AgentConfig.llm_config` 创建 LLM 对象，并按 `model_name` 写入 `llm_context`。
2. 节点调用 `adapt_llm_model_name(session, node_id)`。
3. 适配器根据 `NODE_LLM_MAPPING` 查找节点对应槽位。
4. 如果配置中没有该槽位，使用 `general`。
5. 节点把模型名传给算法层，算法层再从 `llm_context` 取对应 LLM 对象。

## 数据契约与依赖

- `llm_config` 必须至少包含 `general`。
- 槽位名称来自 `LlmConfigCategory`：`general`、`plan_understanding`、`info_collecting`、`writing_checking`、
  `vlm_chart_generating`。
- 节点名来自 `NodeId` 常量。
- `LLMModelFactory` 兼容 openJiuwen `Model` 所需的 provider、api_key、api_base、timeout、hyper_parameters 和 extension。

## 边界与错误处理

- `DeepresearchAgent.run` 在运行前检查 `general`，缺失时抛出 `LLM_CONFIG_NONE`。
- `adapt_llm_model_name` 假设 session 中存在 `config.llm_config.general.model_name`。
- VLM 适配返回 `NO VLM` 后，上游图表节点应按图表开关和模型可用性处理。
- `LLM_SSL_VERIFY` 环境变量控制 legacy openJiuwen 模型客户端 SSL 校验。

## 测试与验证

- `uv run pytest tests/search_agent/test_model_switch_runtime_config.py`
- `uv run pytest tests/utils/test_llm_utils.py`
- 修改节点到槽位映射时，补充运行受影响节点的 workflow 测试。

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)
- [Prompt 模板系统](../algorithm/prompt-template-system.md)
