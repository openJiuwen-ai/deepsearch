# Brief 大纲升级专业版报告

## 维护范围

覆盖 brief 报告结果回传升级为专业版报告的完整链路：metadata 输出、`/run` 接口
metadata 入参与校验、工作流注入路由、大纲 LLM 扩写转换及标题一致性约束。

不覆盖 brief 工作流自身的生成逻辑（见
[brief-report.md](../algorithm/brief-report.md)），也不覆盖大纲交互、编辑团队等
专业版既有节点行为（见 [research-workflow.md](research-workflow.md)）。

## 功能目的

用户跑完 brief（精简版）报告后，希望基于该版报告的大纲生成专业版报告，且要求
大纲的标题、数量、顺序与 brief 版本保持一致。brief 大纲仅有 `goal/research_steps`，
专业版 Section 需要 `plans/steps` 等研究计划字段驱动后续编辑团队，因此由 LLM 在
保持结构一致的前提下扩写补齐。

## 可见行为

- brief 运行的 `final_result.metadata` 输出三键结构：
  `brief_outline`（BriefOutline 序列化）、`research_intent`（意图识别产出，
  含 audience_role/tone 等）、`language`。
- `/run` 接口新增可选入参 `metadata`，客户端回传上一次 brief 运行的
  `final_result.metadata` 即可发起升级运行。
- 升级运行跳过意图识别与澄清，直接进入大纲节点；大纲章节标题、数量、顺序与
  brief 版一致，缺失的研究计划字段由 LLM 扩写补齐。
- 大纲交互（revise_outline/revise_comment）在升级运行中正常进行，交互轮
  与普通专业版运行完全一致（prompt 不携带 brief 大纲，用户可任意修订
  结构，服务端不做拦截）；brief 结构约束仅作用于首版大纲生成。
- 生成流程从大纲往后（编辑团队、写作、溯源等）与普通专业版一致。

## 关键代码路径

- `openjiuwen_deepsearch/framework/openjiuwen/agent/brief_nodes.py`：
  `BriefOutlineNode._post_handle` 写入 `final_result.metadata`。
- `openjiuwen_deepsearch/framework/openjiuwen/agent/metadata_injectors.py`：
  metadata 注入器注册表，单文件承载机制与用途实现：
  机制部分（`MetadataInjector` 接口、`validate_metadata`/`apply_injectors`
  编排、first-wins 状态合并、`resolve_forced_execution_method` 强制执行模式
  解析）+ brief 大纲升级用途（`parse_upgrade_metadata` 解析/校验，非法抛
  `CustomValueException`（`PARAM_CHECK_ERROR_UPGRADE_METADATA_INVALID`，
  错误码 200030）与 `brief_outline_injector` 注册，声明
  `force_execution_method=PARALLEL`）。
  server 入口校验走 `validate_metadata`（纯结构校验，不感知请求上下文），
  StartNode 注入走 `apply_injectors`；brief 模式的忽略条件由注入器在 inject
  阶段判断，server 不参与任何用途特有过滤。
  新增 metadata 用途 = 在该文件实现新注入器并注册，两层自动覆盖。
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`：
  StartNode 通过 `apply_injectors` 编排注入，不感知具体用途；
  `OutlineNode._pre_handle` 注入检测与 prompt 上下文组装；
  `OutlineNode._select_prompt_and_dep_driving` 注入场景强制普通大纲工具
  与 PARALLEL：首轮走 `outliner`（忽略报告模板），交互轮回跳复用
  `_select_prompt_name` 的通用交互分流（与普通运行一致）；
  `OutlineNode._do_invoke` 生成后调用
  `BriefOutline.matches_section_titles` 做标题漂移观测（仅记 warning，
  不拦截不重试；结构一致性由 prompt 引导承担）。
- `openjiuwen_deepsearch/framework/openjiuwen/agent/workflow.py`：仅并行图
  （parallel）的 START 边含 INTENT_RECOGNITION 与 OUTLINE 两个目标，由
  StartNode 返回的 `next_node` 决定路由；hybrid 与 dependency_driving 图的
  START 边为固定边（直达 INTENT_RECOGNITION），依赖图也不注册普通编辑团队
  节点——升级运行在 server 层已强制并行图，这两个图不做任何注入适配。
- `openjiuwen_deepsearch/algorithm/prompts/outliner.md`：注入场景
  brief_outline 权威结构条件块（首版大纲强约束，标题必须一致）；
  `outliner_interaction.md`、`outliner_user_revised.md` 不携带 brief 大纲，
  交互轮与普通专业版运行语义完全一致。
- `server/schemas/deepsearch_run.py`：`DeepSearchRequest.metadata` 字段。
- `server/routers/deepsearch_run.py`：`_validate_upgrade_metadata` 入口校验、
  `_force_execution_method_for_metadata` 按注入器声明覆盖执行模式
  （Agent 构建前）、`run_kwargs` 透传。
- `server/deepsearch/core/manager/agent.py`：Agent 缓存键排除 `metadata`。

## 核心流程

1. brief 运行中 `BriefOutlineNode` 将大纲、research_intent、language 写入
   `final_result.metadata`，随最终结果对外输出。
2. 客户端将 `final_result.metadata` 原样作为 `/run` 的 `metadata` 入参回传
   （可选携带 `report_type`）。
3. server 入口校验 `brief_outline/research_intent` 结构（纯结构校验，与
   report_type 无关），非法返回 400；随后按注入器声明的
   `force_execution_method` 覆盖请求的 execution_method（升级运行强制
   parallel，brief 大纲无依赖结构），再构建/复用 Agent 并透传 metadata 到
   `DeepresearchAgent.run`，metadata 不参与 Agent 缓存键。
4. `StartNode` 解析 metadata：可解析出 BriefOutline + ResearchIntent 且
   report_type 非 brief 时激活注入——写入 research_intent（覆盖 report_type 为
   本次请求值）、report_type_policy、language、brief_state，路由直达 OUTLINE；
   结构非法时抛 CustomValueException（200030）中断，不静默降级。
5. `OutlineNode` 检测 `brief_state` 携带 outline：章节数以 brief 大纲为准（不做
   max 截断），将 brief 大纲注入 prompt 上下文由 LLM 扩写为专业版结构。
6. 生成结果做标题漂移观测（归一化编号噪声后比较），漂移仅记 warning 供
   度量，不重试不拦截——同输入重试无信息增量，且会误伤交互轮用户的显式
   标题修订。
7. 大纲生成成功后进入大纲交互（如启用），后续流程与普通专业版一致。

## 数据契约与依赖

metadata 三键结构：

```python
{
    "brief_outline": BriefOutline.model_dump(),   # title + sections（goal/research_steps）
    "research_intent": ResearchIntent.model_dump(),
    "language": "zh-CN",                          # 缺省回退 zh-CN
}
```

`report_type` 组合行为：

| 传入组合 | 行为 |
| --- | --- |
| metadata + report_type="professional" | 强制 professional，注入生效 |
| metadata + report_type=None | 默认按 professional，注入生效 |
| metadata + report_type="brief" | 合法则透传，注入阶段忽略，走 brief 正常流程 |
| metadata 结构非法（server 入口） | HTTP 400（与 report_type 无关），不进入流式启动 |
| metadata 结构非法（SDK 绕过 server） | StartNode 抛 CustomValueException，workflow 输出结构化 exception_info |
| 无 metadata | 完全走既有流程 |

`execution_method` 组合行为：

| 传入组合 | 行为 |
| --- | --- |
| metadata + execution_method=dependency_driving（server 路径） | Agent 构建前强制覆盖为 parallel，走并行图 |
| metadata + execution_method=hybrid（server 路径） | 同上强制 parallel（hybrid/dependency 图 START 为固定边，无注入路由） |
| SDK 直连依赖或 hybrid Agent + metadata | inject 阶段抛 CustomValueException（`PARAM_CHECK_ERROR_UPGRADE_EXECUTION_METHOD_CONFLICT`，错误码 200031），显式失败不静默降级 |

依赖：`BriefOutline`/`BriefSection`/`BriefResearchStep`
（`openjiuwen_deepsearch/algorithm/brief_report/models.py`，含
`normalize_outline_title` 归一化与 `BriefOutline.matches_section_titles`
标题一致性契约）、`ResearchIntent`（`search_context.py`）、
`BriefWorkflowState` 作为 `search_context.brief_state` 注入载体。

## 边界与错误处理

- 不做服务端持久化：metadata 由客户端持有并回传，服务端只在运行期消费。
- 仅在升级运行首轮生效：注入发生在 StartNode，后续轮次（大纲交互反馈等）由
  workflow 状态机自然接管；`brief_state` 在运行内保留，使交互轮 prompt 的
  brief 结构块持续可用。
- `metadata` 仅 research 模式的 run 签名接受；server 层仅在 research 模式时
  透传到 run_kwargs，search/react 模式不受影响；brief 模式透传后由 SDK 注入器
  在 inject 阶段忽略（server 校验只看结构，不区分 report_type）。
- 执行模式在 Agent 构建前强制为 parallel（依据注入器
  `force_execution_method` 声明）；workflow 图在 Agent 构建时固定，依赖图
  不做运行期并行适配。Agent 缓存键含 execution_method，同会话混跑依赖模式
  普通运行与升级运行会各自缓存实例，按会话清理机制释放。
- Agent 缓存键排除 metadata，同一会话升级运行复用既有 Agent 实例。
- 标题漂移不做服务端硬校验：en-US 编号等归一化盲区或交互轮用户显式修订
  均不再触发重试/中止；`OUTLINER_GENERATE_ERROR` 仅由大纲解析失败等既有
  失败路径触发。

## 测试与验证

```bash
uv run pytest tests/server/ -v
uv run pytest tests/node/test_agent_node.py -v
uv run pytest tests/brief_report/test_nodes.py -v
uv run pytest tests/workflow/test_workflow_run.py -v
```

覆盖场景：metadata 写入（brief_nodes）、注入激活/降级与 report_type 组合
（StartNode）、注入场景章节数与标题漂移观测不拦截（OutlineNode）、/run 校验 400 与
run_kwargs 透传、execution_method 强制覆盖与 SDK 直连冲突失败
（`tests/workflow/test_dependency_workflow.py` 验证依赖图不含普通编辑团队）、
缓存键排除、Agent 透传 workflow inputs。

## 相关文档

- [metadata-injectors.md](metadata-injectors.md)（注入器机制）
- [brief-report.md](../algorithm/brief-report.md)
- [research-workflow.md](research-workflow.md)
- [search-context.md](search-context.md)
- [deepsearch-run-streaming.md](../server/deepsearch-run-streaming.md)
- [prompt-template-system.md](../algorithm/prompt-template-system.md)
