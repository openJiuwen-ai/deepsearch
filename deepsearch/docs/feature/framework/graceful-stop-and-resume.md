# DeepSearch 优雅停止与断点恢复

## 维护范围

本文档覆盖 DeepSearch SDK 侧（deepsearch 子进程内）的用户停止与停止后
断点恢复能力：停止信号在进程内的表示与传播契约、LLM 调用与节点边界的
停止检查点、并发任务的停止收口、停止后 checkpoint 的保留语义。

不覆盖：外层宿主（jiuwenswarm）如何下发停止帧与认领快照（见宿主仓库）、
runner 脚本（relay-claw）的停止帧监听与终态上报、大纲确认/问题澄清等
HITL 交互中断（既有能力，与用户停止共用恢复机制但非本文档重点）。

## 功能目的

用户在研究任务执行中途点击停止后，任务必须在秒级内停止消耗 LLM token
与网络抓取，且执行进度（已完成节点产物）保留在 checkpoint 中；用户再次
发起"继续"时从停止点续跑，不重跑已完成节点、不重新提问。

## 可见行为

- 停止指令到达后，秒级内 LLM 调用与节点执行中断，流式输出停止。
- 停止后进程正常退出（非 crash），checkpoint 保留不清理。
- 同 conversation_id 再次运行（`agent.run(interrupt_feedback="accepted")`）
  时，被停止的节点重新调度，已完成节点不重跑。
- 正常完成/普通业务失败不受影响：失败仍清理 checkpoint，行为与停止前
  完全一致。
- 配置了 `host_session_id` 的会话，其快照携带归属标识，供宿主侧跨会话
  误认领校验。

## 关键代码路径

| 角色 | 文件 |
| --- | --- |
| 停止控制原语 | `framework/openjiuwen/agent/stop_control.py` |
| LLM 调用停止检查点 | `utils/common_utils/llm_utils.py` |
| 主图入口停止分流 | `framework/openjiuwen/agent/workflow.py`（run） |
| 节点边界停止透传 | `framework/openjiuwen/agent/main_graph_nodes.py`、`brief_nodes.py`、`editor_team_manager_node.py`、`collector_graph/webpage_enrichment.py` |
| 算法层停止透传 | `algorithm/brief_report/*`、`algorithm/chart_generation/*`、`algorithm/report/visualization.py`、`algorithm/source_trace/*`、`algorithm/source_tracer_infer/*` |
| 归属字段 | `config/config.py`（host_session_id）、StartNode 白名单落盘 |
| 测试 | `tests/algorithm/test_stop_propagation_professional.py`、`tests/brief_report/test_stop_propagation.py`、`tests/utils/test_llm_utils.py`、`tests/workflow/test_workflow_run.py` |

## 核心流程

1. 父进程（宿主）经 stdin 停止帧置位进程级停止标志
   （`stop_control.request_stop()`，`threading.Event`，幂等）。
2. 停止标志在各检查点被消费为 `StopRequested`（普通 `Exception` 子类）：
   - LLM 调用：调用前、流式 chunk 到达时、chunk 间隙/TTFT 等待期轮询
     （2s 间隔）、非流式等待期轮询；
   - 节点边界：节点 `_do_invoke` 前后；
   - 并发任务：`gather_with_stop`（异常即取消兄弟）与
     `gather_results_with_stop`（保留普通失败压平语义、停止秒级取消兄弟）
     收口，无孤儿任务。
3. `StopRequested` 沿节点/算法层逐层上抛，任何 `except Exception` 分流
   必须先 `except StopRequested: raise`，禁止把停止吞成局部失败/空结果/
   假完成。
4. 图引擎走异常保存分支：checkpoint（含 pending_node）落盘、跳过清理。
5. `DeepresearchAgent.run` 识别 `StopRequested`（含包装异常，经
   `STOP_REQUESTED_MARKER` 识别），跳过 `release_session`，emit stopped
   终态后退出进程。
6. 恢复：同 conversation_id 再次 run，`pre_workflow_execute` recover 命中
   resume，被停止节点重调度，已完成节点不重跑。

## 数据契约与依赖

- `StopRequested`：普通 `Exception`；`__reduce__` 序列化为携带
  `STOP_REQUESTED_MARKER` 的 `RuntimeError`，保证宿主进程（可能未安装
  本包）反序列化快照时可识别停止标记。
- 停止标志是进程内全局单例：仅部署在 deepsearch 子进程内，一次运行一个
  子进程；`clear_stop()` 仅供测试复位，生产入口禁止调用（run 入口调用
  会擦除 boot 窗口内到达的停止帧）。
- `host_session_id`：config 可选键，由宿主注入，StartNode 白名单写入会话
  config 随快照落盘；resume 不重跑 StartNode，快照归属保持原始创建者。
- 停止分支保留 checkpoint（无 TTL/GC，清理策略属下游宿主 backlog）。

## 边界与错误处理

- 停止不使用 `asyncio.task.cancel()` 作为主机制：CancelledError 会让
  agent-core 走"取消清除 checkpoint"分支。取消注入只发生在停止收口的
  兄弟协程内部，主图快照由 `_save_state_on_error` 保存。
- 同步阻塞调用（`to_thread` 内的 sync invoke）无法强制中止：轮询等待
  期命中停止后宽限 5s 放弃等待，孤儿由进程退出兜底。
- 流式输出的 ContextVar 生命周期：整个流消费运行在单个长驻 task 内，
  生成器跨 yield 的调用追踪上下文不丢失。
- VLMChartGeneratorNode 等节点的通用 `except Exception` 分支前必须有
  `except StopRequested: raise`，否则停止被吞成 error_msg 假完成。

## 测试与验证

```bash
# 停止传播全量（专业版 + brief）
python -m pytest tests/algorithm/test_stop_propagation_professional.py \
  tests/brief_report/test_stop_propagation.py tests/utils/test_llm_utils.py \
  tests/workflow/test_workflow_run.py -q
```

必须覆盖的场景：TTFT/chunk 间隙/持续输出中停止、流尾停止、chart 公共
包装器真实穿透（非桩替换）、混合并发（停止 + 慢抓取）取消收口、节点
边界不降级、run 入口停止保留 checkpoint 而普通失败清理。

## 相关文档

- 宿主侧停止下发与快照认领：jiuwenswarm 仓库 deepresearch 工具文档。
- 停止帧监听与 stopped 终态上报：relay-claw 仓库 deepresearch skill 文档。
- 图引擎 checkpoint 语义：agent-core 仓库 workflow/graph 文档。
