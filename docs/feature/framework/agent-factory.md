# Agent 工厂与运行模式

## 维护范围

本文档覆盖 SDK 侧根据 `AgentConfig` 创建具体 Agent 实例的选择逻辑，以及 `search_mode`、`execution_method` 对 framework
工作流入口的影响。

不覆盖各 Agent 内部节点流程；研究报告主流程见 [报告研究主工作流](./research-workflow.md)，DeepSearch 搜索流程见
[DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)。

## 功能目的

Agent 工厂给 SDK 和服务端提供统一构造入口，使调用方只需要传入配置，就能得到研究报告、依赖驱动研究、DeepSearch 搜索或
ReAct 搜索对应的运行对象。

## 可见行为

- `search_mode=research` 时，根据 `execution_method` 创建报告研究 Agent。
- `execution_method=parallel` 创建并行章节研究流程。
- `execution_method=dependency_driving` 创建依赖驱动章节研究流程。
- `search_mode=search` 创建 DeepSearch 搜索 Agent。
- `search_mode=react` 创建简单 ReAct 搜索 Agent。
- 缺失必填配置、Pydantic 校验失败或未知模式会抛出 `CustomValueException`。
- 敏感日志模式下，参数校验错误不会把原始校验详情写入异常消息。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/agent_factory.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `openjiuwen_deepsearch/config/config.py`
- `openjiuwen_deepsearch/config/method.py`
- `openjiuwen_deepsearch/config/search_mode.py`
- `tests/workflow/test_create_agent.py`
- `tests/server/test_agent_manager.py`

## 核心流程

1. `AgentFactory.create_agent` 先调用 `validate_agent_required_field` 校验必填配置。
2. 使用 `AgentConfig.model_validate` 归一化输入，并在校验失败时按敏感日志开关决定异常详情。
3. 校验 `search_mode` 是否属于 `SearchMode`。
4. `research` 模式继续校验 `execution_method`，并从 `agent_map` 选择 `DeepresearchAgent` 或
   `DeepresearchDependencyAgent`。
5. `search` 和 `react` 模式直接由 `search_mode` 映射到对应 Agent。
6. 实例化 Agent 并记录创建日志。

## 数据契约与依赖

- 输入是可被 `AgentConfig` 校验的 dict。
- `AgentConfig.llm_config.general` 是实际运行时的必需 LLM 槽位，工厂阶段只负责结构校验。
- `search_mode` 的有效值来自 `SearchMode`。
- `execution_method` 的有效值来自 `ExecutionMethod`，只在 `research` 模式下生效。
- `WORKFLOW_EXECUTE_TIMEOUT` 在模块加载时同步为 `Config().service_config.workflow_execution_timeout`。

## 边界与错误处理

- 未知 `search_mode` 或 `execution_method` 使用 `WORKFLOW_TYPE_NOT_EXIST_ERROR`。
- 参数校验失败使用 `PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR`；敏感日志模式下使用不打印详情的错误码。
- 工厂只负责选择和构造，不初始化 LLM、搜索工具或 workflow session。

## 测试与验证

- `uv run pytest tests/workflow/test_create_agent.py`
- `uv run pytest tests/server/test_agent_manager.py`
- 涉及服务端调用链时，补充运行 `uv run pytest tests/server/test_deepsearch_run.py`。

## 相关文档

- [报告研究主工作流](./research-workflow.md)
- [DeepSearch 搜索子工作流](./deepsearch-sub-workflows.md)
- [搜索工具注册与运行时 API 工具](./search-tool-registration.md)
