# CodeSearch 检索工作流

## 维护范围

本文档覆盖 `framework/openjiuwen/` 的编排行为：`code_search` workflow 的图结构、
双引擎（graph/react）等价性、per-run 运行隔离、超时注入与终止条件。
不覆盖检索算法与提示词细节（见 [search-agent.md](../algorithm/search-agent.md)）。

## 功能目的

把多轮检索循环以 openJiuwen workflow 图形态承载（Studio/Ops 可观测），同时保留
纯代码循环兜底；两引擎共享同一份阶段逻辑，行为逐字节一致。

## 图结构

```
START → REASONING ⇄ TOOL（自环），两者均可路由 END
```

| 节点 | 调用 | 路由输出（next_node） |
|---|---|---|
| `CSStartNode` (Start) | 校验 run_id 注册表命中 | → reasoning（固定边） |
| `ReasoningNode` | `steps.reasoning_step`：fail-fast / 轮次上限 / 一轮 LLM 决策 | tool / end |
| `ToolNode` | `steps.tool_step`：批量执行 pending 工具调用、过滤入记忆、停滞计数、临界警告 | reasoning / end |
| `CSEndNode` (End) | `steps.finalize`：按 Termination 构造最终结果 | — |

路由基于 `Termination` 枚举写入 `next_node`（BranchRouter 条件
`${节点.next_node} == '目标'`），禁止业务字符串比较。

## 可见行为

- workflow 对象进程内类级共享（双检锁单例注册，`Runner.run_workflow`
  以 `code_search_1`（id_version）调用）；
- **会话纪律**：workflow session 只携带可序列化的 `run_id`；记忆/检索器/LLM
  等活对象经 `runtime_context` 运行注册表注入，try/finally 注销——大负载与
  含锁对象不进会被复制的 workflow state；
- **超时**：openJiuwen workflow 默认执行超时 60s；每次运行经
  `workflow_session_vars` 注入 `SearchAgentConfig.time_limit_seconds`
  （默认 900s），finally reset，重叠运行互不影响；
- **终止条件**（`domain.result.Termination`）：`submitted` 主动提交 /
  `stagnated` 连续 N 个检索轮零新增 / `max_turns` 轮次耗尽降级 /
  `no_tool_call`、`llm_error` 降级 / `index_not_ready` fail-fast；
- 双引擎等价性由集成测试
  `test_graph_and_react_produce_identical_results` 锁定。

## 关键代码路径

| 文件 | 内容 |
|---|---|
| `framework/openjiuwen/steps.py` | 阶段函数（两引擎共享的全部循环逻辑） |
| `framework/openjiuwen/workflow.py` | 图组装、注册、GraphCodeSearchAgent、超时注入 |
| `framework/openjiuwen/nodes.py` | 四节点薄包装 |
| `framework/openjiuwen/base_node.py` | 薄壳 → **base 包** `openjiuwen_search_base.workflow`（三段式 BaseNode + init_router） |
| `framework/openjiuwen/runtime_context.py` | CodeSearchRunContext + 运行注册表（注册表实现在 **base 包** `openjiuwen_search_base.runtime`） |
| `framework/openjiuwen/agent.py` | react 引擎（同一 steps 的 while 循环驱动） |
