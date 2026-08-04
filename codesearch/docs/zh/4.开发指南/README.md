# 开发指南

## 分层架构

依赖方向单向，只允许由右向左依赖：

```
[base] ← domain ← config ← algorithm ← framework/openjiuwen ← api
 公共能力  纯模型   配置模型   纯算法+工具    编排/图/运行隔离    门面
                                ↑ indexing / retrieval（索引与检索基建）
```

| 层 | 职责 | 约束 |
|---|---|---|
| `openjiuwen-search-base` | search 场景公共能力：LLM 适配、embedding 客户端、Milvus 存取基建与表达式安全构造、工作流节点模板、日志、运行注册表 | 不依赖任何产品包；重依赖为可选分组 |
| `domain/` | 纯数据模型：代码片段、片段记忆、终止原因、结果 | 不 import 包内其他模块 |
| `config/` | pydantic 配置模型 | 一处解析、运行期只读，无全局可变状态 |
| `algorithm/` | 检索算法：决策组装、逐行筛选、结果构造、智能体工具 | **禁止 import `framework/`** |
| `indexing/` `retrieval/` | 切块、嵌入、入库；检索协议与 Milvus 实现 | 重依赖使用受保护导入 |
| `framework/openjiuwen/` | 循环阶段函数、工作流图、运行上下文与隔离 | 活对象经运行注册表注入，不进入工作流状态 |
| `api/` | 对外门面 | 唯一公开接口面 |
| `benchmarks/` | 评测适配 | 只依赖公开 API，核心包不得反向依赖 |
| `openjiuwen_codesearch/server/` | HTTP 服务层（FastAPI）：健康检查、检索接口、索引作业 | 只依赖公开 API；随包分发，故 whl 安装后亦可起服务 |

## 双引擎

`SearchAgentConfig.engine` 取值：

| 取值 | 含义 |
|---|---|
| `graph` | openJiuwen 工作流图形态，节点级可观测 |
| `react` | 纯代码循环，无框架依赖的兜底形态 |
| `auto`（默认） | 框架可用则用 `graph`，否则回退 `react` |

两个引擎共享 `framework/openjiuwen/steps.py` 中的同一份阶段逻辑，集成测试
锁定二者输出逐字节一致。图结构与运行隔离设计见
[特性文档](../../feature/framework/codesearch-workflow.md)。

## 扩展点

| 需求 | 实现方式 |
|---|---|
| 支持新的编程语言 | 实现 `indexing/chunkers/base.py` 的 `Chunker` 协议，新增一个实现文件 |
| 接入新的检索后端 | 实现 `retrieval/base.py` 的 `CodeRetriever` 协议 |
| 接入新的模型服务 | 实现 base 包的 `LLMClient` 协议（框架适配集中于单个文件） |
| 新增智能体工具 | 在 `algorithm/search_tools/` 增加一个 `ToolSpec`（schema + 执行函数）并注册 |

## 关键配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `agent.max_turns` | 20 | 检索循环轮次上限 |
| `agent.stagnation_rounds` | 3 | 连续无新增发现的检索轮数达到该值时提前终止 |
| `agent.search_topk` | 10 | 单次检索返回的片段数 |
| `agent.retrieve_topk` | 20 | 最终返回的片段数上限 |
| `agent.filter_concurrency` | 8 | 逐行筛选的并发上限 |
| `agent.time_limit_seconds` | 900 | 单次运行的工作流超时 |
| `index.enable_trigram` | True | 三元组字段开关（存储主要来源） |
| `index.max_num_files_per_repo` | None | 单仓索引文件数上限 |
| `index.max_file_size_bytes` | 5MB | 超过则跳过该文件 |
| `milvus.collection_prefix` | `cs_` | 集合命名空间前缀 |
| `milvus.schema_version` | `v1` | 索引模式版本，模式变更须递增 |

密钥类配置（`llm.*.api_key`、`embed.api_key`、`milvus.token`）以 `bytearray`
存储，构造时可直接传字符串；仅在发起外部调用时解码，必要时可用
`openjiuwen_search_base.security.zero_secret` 就地清零。

## 测试

```sh
pytest tests/unit -W ignore          # 零外部依赖，含完整检索轨迹回放
pytest tests/integration -W ignore   # 需要 openjiuwen（工作流图）与 server 分组
pytest -m e2e -W ignore              # 需要可访问的 Milvus 实例
```

服务层的接口契约见[安装指导](../2.安装指导/README.md)的 HTTP 服务一节；
索引为长任务，服务端以后台作业方式执行并通过作业接口查询状态。

约定：单元测试不得导入 openjiuwen 或 pymilvus；行为契约（记忆渲染格式、
结果排序、工具消息文本、行号映射）均有对应用例锁定，修改这些行为需同步
修改测试并在特性文档中说明。

## 工程约定

完整约定见仓库根的 [AGENTS.md](../../../AGENTS.md)，要点：

- 所有 Milvus 查询表达式经 base 包的安全构造函数生成，禁止字符串拼接；
- 配置经模型构造注入，运行态挂在运行上下文而非实例属性；
- 行为可见的变更需同步更新 `docs/`，特性级设计按
  [模板](../../feature/_template.md)写入 `docs/feature/`。

## 目录结构

见 [directory_structure.md](directory_structure.md)。
