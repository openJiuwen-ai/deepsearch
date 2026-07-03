# WorkflowAgent 封装

## 维护范围

本文档覆盖 DeepSearch 自定义 `WorkflowAgent`、`WorkflowControllerAdapter` 和 `WorkflowController` 对 openJiuwen ControllerAgent
接口的适配。

不覆盖业务 workflow 节点内容；业务流程见 [报告研究主工作流](./research-workflow.md)。

## 功能目的

WorkflowAgent 封装允许当前仓库用 ControllerAgent 形式暴露由 openJiuwen `Workflow` 组成的 Agent，并把 workflow 注册到
`Runner.resource_mgr`，供 `Runner.run_agent_streaming` 和交互恢复链路调用。

## 可见行为

- `WorkflowAgent(card, config)` 接受 `WorkflowControllerConfig` 或 dict。
- `add_workflows` 支持 workflow 实例、带 `id/version` 的 provider 或带 `card()` 的 provider。
- workflow 以 `workflow_id_version` 形式注册到 `Runner.resource_mgr`，并使用 Agent config id 作为 tag。
- Controller 输入可以来自文本帧或 JSON 帧，最终转换成 workflow 需要的 dict。
- 交互恢复输入会包装为 `InteractiveInput`。
- streaming 默认转发 `CUSTOM` 和 `OUTPUT` 两类 workflow chunk。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/workflow_agent.py`
- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/workflow_controller.py`
- `openjiuwen_deepsearch/framework/openjiuwen/core/workflow_agent/config.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`
- `tests/workflow/test_workflow_agent.py`
- `tests/workflow/test_workflow_run.py`

## 核心流程

1. `WorkflowAgent.__init__` 创建 `WorkflowControllerAdapter` 并初始化 ControllerAgent。
2. `add_workflows` 解析 workflow card、构造 provider，并把 workflow card 追加到 config。
3. workflow 注册到 `Runner.resource_mgr`，重复注册时记录已有 tag 和拓扑签名差异。
4. `WorkflowControllerAdapter.stream` 把 openJiuwen `InputEvent` 转成 dict。
5. `WorkflowController` 选择第一个 workflow card，过滤输入字段，并从 resource manager 获取 workflow。
6. 调用 `Runner.run_workflow_streaming` 转发子 workflow chunk。

## 数据契约与依赖

- `WorkflowControllerConfig.workflows` 是可运行 workflow 列表。
- `WorkflowCard.input_params` 决定输入过滤行为；缺少标准 JSON schema 时兼容旧式 `{key: {"type": ...}}`。
- `conversation_id` 不直接作为 workflow 输入必填字段，但会用于 session 选择和恢复。
- JSON 输入帧中的 `query`、`user_input`、`user_id` 和 metadata 会合并成 controller 输入 dict。

## 边界与错误处理

- 未初始化 adapter 时抛出 `WORKFLOW_CONTROLLER_ADAPTER_NOT_INIT`。
- config 中没有 workflow 时抛出 `WORKFLOW_CONTROLLER_NO_WORKFLOWS`。
- resource manager 找不到 workflow 时抛出 `WORKFLOW_NOT_FOUND_IN_RESOURCE`。
- workflow 参数类型非法时抛出 `WORKFLOW_PARAM_INVALID`。
- 旧版 `Runner.run_workflow_streaming` 不接受 `session` 参数时，会 fallback 到无 session 调用。

## 测试与验证

- `uv run pytest tests/workflow/test_workflow_agent.py`
- `uv run pytest tests/workflow/test_workflow_run.py`
- 修改交互恢复输入构造时，补充运行用户反馈和大纲交互相关测试。

## 相关文档

- [节点基类与会话上下文](./base-node-and-session-context.md)
- [报告研究主工作流](./research-workflow.md)
