# 开发指南

## 分层架构（依赖方向只允许向左）

```
domain ← config ← algorithm ← framework/openjiuwen ← api
   纯模型    pydantic    纯算法+工具     图/编排/隔离      门面
                           ↑ indexing / retrieval（索引与检索基建）
```

| 层 | 职责 | 纪律 |
|---|---|---|
| `domain/` | 纯数据模型（Snippet/SnippetMemory/Termination/结果） | 不 import 包内任何模块 |
| `config/` | 全 pydantic 配置 | 一处解析、全程只读，禁止全局可变状态 |
| `algorithm/` | 阶段算法（reasoning/filtering/memory_ops）+ 工具 registry | **禁止 import framework** |
| `indexing/` `retrieval/` | 切块/嵌入/入库；检索协议 + Milvus 实现 | 重依赖 guarded import |
| `framework/openjiuwen/` | steps 阶段函数、BaseNode、workflow 图、RunContext | 活对象经运行注册表注入，不进 workflow state |
| `api/` | `CodeSearchRetriever` 门面 | 唯一公开面 |
| `benchmarks/` | ContextBench 适配 | 只依赖公共 API，核心包不得反向 import |

## 引擎

`SearchAgentConfig.engine`：`graph`（openJiuwen workflow 图形态，默认优先）/
`react`（纯代码循环兜底）/ `auto` / `retropus`（KG+BM25，独立工具注册表与
`CodeSearchConfig.retropus` 配置）。`graph`/`react` 共享
`framework/openjiuwen/steps.py` 的同一份阶段逻辑，集成测试锁定输出逐字节一致。
图结构见 [codesearch-workflow.md](../../feature/framework/codesearch-workflow.md)；
Retropus 配置与行为见
[retropus-agent.md](../../feature/framework/retropus-agent.md)。

## 扩展点

| 要扩展什么 | 实现哪个协议 / 改哪里 |
|---|---|
| 新语言切块 | `indexing/chunkers/base.py` 的 `Chunker` 协议，新增一个实现文件 |
| 新检索后端 | `retrieval/base.py` 的 `CodeRetriever` 协议 |
| 新 LLM 接入 | `llm/factory.py` 的 `LLMClient` 协议（openjiuwen API 面隔离在此单文件） |
| 新智能体工具 | `algorithm/search_tools/`：一个 SPEC（schema+executor）+ registry 注册 |

## 测试

```sh
pytest tests/unit -W ignore         # 零外部依赖（fixture 回放，含完整 agent 轨迹）
pytest tests/integration -W ignore  # 需 openjiuwen（graph 引擎真实 Runner 驱动）
pytest -m e2e -W ignore             # 需运行中的 Milvus（MILVUS_PORT 可覆盖）
```

测试纪律：单测不得 import openjiuwen/pymilvus；行为契约（记忆渲染格式、
最终排序、工具消息文本）均有测试锁定，改动即显式行为变更。

## API Reference

TBD —— 按 deepsearch 惯例逐模块补充（agent / workflow / nodes / runtime_context /
search_tools / config）。

## 目录结构

见 [directory_structure.md](directory_structure.md)。
