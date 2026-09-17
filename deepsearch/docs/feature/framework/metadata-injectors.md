# 运行时 Metadata 注入器机制

## 维护范围

覆盖 metadata 通用运行时元数据通道的机制实现：注入器接口契约、注册表
编排（结构校验 / 状态注入 / 路由接管 / 强制执行模式）、first-wins 合并
策略，以及 server 与 StartNode 两个消费层的分层职责。

不覆盖具体用途的行为契约（如 brief 大纲升级，见
[brief-outline-upgrade.md](brief-outline-upgrade.md)）。

## 功能目的

`/run` 接口与 SDK 的 `DeepresearchAgent.run` 需要一个通用的运行期元数据
通道：客户端持有、请求时回传、服务端不持久化。为避免在 server 与
StartNode 中硬编码各用途的分支逻辑，机制采用注册表模式——每个用途实现
一个 `MetadataInjector` 实例并注册，两层自动复用编排逻辑。新增 metadata
用途只需在 `metadata_injectors.py` 追加注入器实例，不修改 server、
workflow 或节点代码。

## 可见行为

- server 入口对携带 metadata 的请求做纯结构校验（`validate_metadata`）：
  所有匹配的注入器都必须通过，任一失败返回 HTTP 400，与 report_type 等
  请求上下文无关。
- server 在 Agent 构建前按注入器声明的 `force_execution_method` 覆盖请求
  的 `execution_method`（`resolve_forced_execution_method`）：执行模式决定
  Agent 类（即 workflow 图），必须在构建前纠正。
- StartNode 通过 `apply_injectors` 编排注入：合并全部激活注入器的状态更新
  并按 `next_node` 接管路由；无激活注入器时走默认流程。
- SDK 直连（绕过 server）时注入器在 inject 阶段自行兜底（如执行模式冲突
  显式抛错），不静默降级。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/metadata_injectors.py`：
  机制与用途实现的单文件承载（接口 + 编排 + 注册表）。
- `server/routers/deepsearch_run.py`：`_validate_upgrade_metadata` 入口校验、
  `_force_execution_method_for_metadata` 构建前强制覆盖。
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`：
  StartNode 调用 `apply_injectors` 完成注入与路由接管。
- 主要测试：`tests/node/test_agent_node.py`（注入编排与冲突异常）、
  `tests/server/test_deepsearch_run.py`（server 校验与强制覆盖）。

## 核心流程

```text
/run 请求携带 metadata
  → server: validate_metadata（纯结构校验，非法 400）
  → server: resolve_forced_execution_method → 构建前覆盖 execution_method
  → 透传 metadata 到 DeepresearchAgent.run（仅 research 模式）
  → StartNode: apply_injectors
      ├─ matches: 指纹检测，决定注入器是否消费
      ├─ inject: 返回 None（匹配但不激活）或 MetadataInjection
      └─ 合并 state_updates（first-wins）+ 取首个 next_node
  → 按 next_node 路由（None 时走默认流程）
```

## 数据契约与依赖

`MetadataInjector`（frozen dataclass）四个回调字段 + 一个声明字段：

| 字段 | 契约 |
| --- | --- |
| `name` | 注入器名称，用于日志 |
| `matches` | 轻量指纹检测（如关键键存在）；不抛异常 |
| `validate` | 严格结构校验，非法抛 `CustomValueException`（server 入口用） |
| `inject` | 解析并构造 `MetadataInjection`；返回 None 表示匹配但不激活 |
| `force_execution_method` | 该用途要求的执行模式；None 表示不约束 |

`MetadataInjection`（frozen dataclass）：`state_updates`（写入 session 的
全局状态键值对）+ `next_node`（可选路由接管）。

合并策略：**first-wins** —— 先注册的注入器对同名状态键保持权威，
`next_node` 取首个非空声明，与 `resolve_forced_execution_method` 的
取值策略一致。

依赖：`CustomValueException`/`StatusCode`
（`openjiuwen_deepsearch/common/`）、`ExecutionMethod`
（`openjiuwen_deepsearch/config/method.py`）。

## 边界与错误处理

- 校验分层：结构校验在 server 入口（所有匹配注入器都必须通过，快速
  失败）；执行模式约束在 Agent 构建前；SDK 直连冲突兜底在 inject 阶段。
- `matches` 必须无副作用：server 校验、构建前覆盖、StartNode 注入三个
  阶段都会重复调用。
- 注入仅发生在升级运行首轮（StartNode）；后续轮次由 workflow 状态机接管。
- metadata 不参与 Agent 缓存键（见
[deepsearch-agent-config.md](../server/deepsearch-agent-config.md)）。

## 测试与验证

```bash
uv run pytest tests/node/test_agent_node.py -v -k injectors
uv run pytest tests/server/test_deepsearch_run.py -v
```

覆盖场景：注入编排语义（激活/忽略/first-wins）、`resolve_forced_execution_method`
取值、SDK 直连执行模式冲突异常、server 400 校验与强制覆盖。

## 相关文档

- [brief-outline-upgrade.md](brief-outline-upgrade.md)（首个用途：brief 大纲升级）
- [research-workflow.md](research-workflow.md)
- [deepsearch-run-streaming.md](../server/deepsearch-run-streaming.md)
- [deepsearch-agent-config.md](../server/deepsearch-agent-config.md)
