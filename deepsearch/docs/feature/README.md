# Feature 文档

`docs/feature/` 是维护者优先的特性文档区，用于记录当前特性的行为、
设计边界、关键代码路径、数据契约和测试入口。它补充 `docs/zh/` 与
`docs/en/` 下的用户文档，但不替代用户文档。

Feature 文档只描述当前现状和当前契约，不维护变更流水。历史请查 git log、
PR 描述、release notes 或专门 changelog。

## 何时必须更新

当 PR 改变以下内容时，应在同一个 PR 中新增或更新相关 feature 文档：

- 功能行为
- 工作流编排
- 公共 SDK/API 行为
- 运行时配置
- Prompt 契约
- 数据契约
- 持久化逻辑
- 报告生成或转换
- 溯源逻辑
- 其他用户或调用方可见行为

纯测试、格式化、依赖元数据、注释、无行为/API/配置/契约变化的重构可以不更新
`docs/feature/`，但 PR 说明中应写明为什么不需要更新 feature 文档。

## 文档组织方式

Feature 文档按主要代码归属组织：

- `algorithm/`：核心研究算法、报告生成、溯源、用户反馈处理等。
- `framework/`：Agent 工作流、节点编排、搜索上下文等。
- `server/`：REST API、后端管理器、报告转换、存储与持久化等。
- `llm/`：统一 LLM 封装与厂商适配。
- `config/`：运行时配置和服务配置。
- `common/`：错误码、异常基类和跨模块公共常量。

跨子系统特性应放在主要 owner 下，并在文档中列出相关模块。不要在多个目录复制
同一份设计说明。

## 大特性拆分

较大的特性采用“总览文档 + 子能力文档”的结构。总览文档描述共享行为、公共数据
契约、公共错误边界和子文档链接；子能力文档描述独立 action、Prompt 契约、
测试入口和边界条件。

当一个 feature 下面存在多个独立 action、独立 Prompt 契约、独立测试文件，
或者常见改动通常只影响其中一个子能力时，应拆出子文档。

## 篇幅原则

Feature 文档不设置硬性字数限制，但应保持高信息密度。文档应优先说明当前行为、
关键代码路径、数据契约、边界条件和测试入口，避免复制 Prompt 全文、代码实现细节
或历史变更。

当单个文档开始覆盖多个独立 action、多个 Prompt 契约、多个测试入口，或常见改动
通常只影响其中一部分内容时，应拆分为“总览文档 + 子能力文档”，而不是继续堆叠
内容。

## 新增文档步骤

1. 从 `_template.md` 复制结构。
2. 只记录当前行为和当前契约。
3. 链接源码、Prompt、测试和相关文档，不复制 Prompt 全文或逐行解释代码。
4. `关键代码路径` 只列入口文件、核心实现文件、相关 Prompt 和主要测试文件，
   不维护完整方法调用链。
5. 在同一个 PR 中更新相关用户文档或 API 文档，如果本次改动影响外部使用方式。

## 文档入口

- [查询理解](./algorithm/query-understanding.md)
- [资料采集](./algorithm/research-collector.md)
- [DeepSearch 搜索智能体](./algorithm/search-agent.md)
- [报告生成](./algorithm/report-generation.md)
- [时间约束软过滤](./temporal-soft-filter.md)
- [Brief 精简版报告工作流](./algorithm/brief-report.md)
- [报告模板生成](./algorithm/report-template.md)
- [全局溯源](./algorithm/source-trace.md)
- [推理链溯源](./algorithm/source-tracer-infer.md)
- [图表生成](./algorithm/chart-generation.md)
- [Prompt 模板系统](./algorithm/prompt-template-system.md)
- [用户反馈处理](./algorithm/user-feedback-processor.md)
- [Agent 工厂与运行模式](./framework/agent-factory.md)
- [报告研究主工作流](./framework/research-workflow.md)
- [DeepSearch 搜索子工作流](./framework/deepsearch-sub-workflows.md)
- [章节推理与写作子工作流](./framework/section-reasoning-writing-sub-workflows.md)
- [信息采集子图](./framework/info-collector-subgraph.md)
- [节点基类与会话上下文](./framework/base-node-and-session-context.md)
- [搜索上下文与数据契约](./framework/search-context.md)
- [WorkflowAgent 封装](./framework/workflow-agent.md)
- [LLM 模型槽位适配](./framework/llm-model-adaptation.md)
- [搜索工具注册与运行时 API 工具](./framework/search-tool-registration.md)
- [DeepSearch 网页抓取 Provider 注册](./framework/web-fetch-provider-registry.md)
- [LLM 运行时封装](./llm/llm-runtime.md)
- [LLM 调用辅助](./utils/llm-invocation-utils.md)
- [日志与接口记录](./utils/logging.md)
- [参数校验、安全目录与 URL 处理](./utils/validation-security-url.md)
- [流式输出与运行遥测](./utils/streaming-and-telemetry.md)
- [调试与中间结果导出](./utils/debug-and-export.md)
- [QPS 限流](./utils/rate-limiting.md)
- [上下文变量、常量与问题路由](./utils/context-routing-constants.md)
- [文本、Markdown 与 Embedding 辅助](./utils/text-markdown-embedding-helpers.md)
- [Agent 与服务运行配置](./config/agent-and-service-config.md)
- [DeepSearch 搜索工作流配置](./config/search-workflow-config.md)
- [Runtime API 工具配置](./config/runtime-api-tool-config.md)
- [错误码、异常与公共常量](./common/error-and-common-contracts.md)
- [Server 应用运行时](./server/fastapi-app-runtime.md)
- [DeepSearch 运行与 SSE 流](./server/deepsearch-run-streaming.md)
- [DeepSearch Agent 配置组装](./server/deepsearch-agent-config.md)
- [Server 报告转换](./server/report-conversion.md)
- [知识库管理](./server/knowledge-base.md)
- [模板与联网搜索引擎管理](./server/template-and-web-search-engine-management.md)
- [Server 持久化与存储](./server/persistence-and-storage.md)
- [遥测事件服务](./server/telemetry-event-server.md)
