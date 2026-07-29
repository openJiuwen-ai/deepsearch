# Action Pool

## 维护范围

本文档覆盖 DeepSearch 搜索智能体中的 action pool 能力，即候选 action 的入池、评分、采样、运行中/完成状态记录和快照输出。

本文档不覆盖 action 生成 Prompt、工具执行和搜索索引构建。

## 功能目的

Action pool 用于在搜索循环中管理多个候选探索方向。它让系统可以按 proposal score、候选变量强度、状态深度和配置权重选择下一步执行的 action，并记录搜索过程状态。

## 可见行为

- 新 action 入池时会预热评分缓存。
- 采样时优先处理 immediate queue。
- depth 过深的状态会被降权，浅层状态可获得加成。
- 运行中、已完成、成功完成 action 会分别记录。
- 配置了日志目录时，会异步写出 `action_pool.json` 快照。

## 关键代码路径

- Action pool：`openjiuwen_deepsearch/algorithm/search_agent/action_pool.py`
- 搜索上下文模型：`openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`

主要测试：

- `tests/search_agent/test_action_pool.py`
- `tests/search_agent/test_termination.py`

## 核心流程

1. find action 阶段生成候选 `Action` 列表。
2. `ActionPool.add` 计算并缓存 base score。
3. 搜索循环从 pool 中采样下一批 action。
4. action 执行时进入 running 列表。
5. 执行结束后记录 completed 和 successfully completed。
6. 如启用快照，pool 状态异步写入日志目录。

## 数据契约与依赖

核心对象：

- `Action`
- `ActionProposal`
- `State`
- `Result`
- `ActionSamplingConfig`

## 边界与错误处理

- action pool 只在单个 asyncio 协程上下文中使用，不提供线程安全保证。
- 快照写入失败只记录日志，不应影响搜索主流程。
- 运行中 action 未找到时仍会记录完成状态，并输出 warning。
- score cache 以 action id 为 key，action id 需要稳定唯一。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/search_agent/test_action_pool.py
```

## 相关文档

- [DeepSearch 搜索智能体总览](../search-agent.md)
- [Search Nodes](./search-nodes.md)
- [Search Tools](./search-tools.md)
