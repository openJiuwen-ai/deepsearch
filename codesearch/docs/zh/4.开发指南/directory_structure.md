# 目录结构

```
codesearch/
├── main.py                          # CLI 入口（index / search）
├── pyproject.toml                   # 包定义与可选依赖分组
├── openjiuwen_codesearch/
│   ├── api/                         # 公开门面：CodeSearchRetriever（+兼容别名 JiuwenRetriever）
│   ├── domain/                      # 纯数据模型：Snippet / SnippetMemory / Termination / 结果
│   ├── common/                      # 异常 + 错误码表
│   ├── config/                      # pydantic 配置：llm / index+milvus / agent / 总装
│   ├── llm/                         # LLMClient 协议 + openjiuwen 适配（guarded）
│   ├── utils/log_utils/             # LogManager（含敏感脱敏）
│   ├── algorithm/                   # 纯算法层（禁止 import framework）
│   │   ├── prompts/                 # .md 提示词（主检索 / 过滤 / retropus_*）
│   │   ├── reasoning.py             # 一轮 LLM 决策
│   │   ├── filtering.py             # 过滤智能体（有界并发逐行提取）
│   │   ├── memory_ops.py            # 最终结果构造
│   │   └── search_tools/            # CodeSearch 5 工具 + retropus_registry / graph_tools
│   ├── retropus/                    # 厂商化 KG / BM25（engine=retropus；需 [retropus] extra）
│   ├── indexing/                    # 切块器（ast）/ 嵌入（SQLite 缓存）/ 增量索引编排
│   ├── retrieval/                   # CodeRetriever 协议 + InMemory fake + milvus/ 实现
│   └── framework/openjiuwen/        # steps / BaseNode / nodes / workflow /
│                                    #   CodeSearchRunContext / RetropusRunContext /
│                                    #   CodeSearchAgent + RetropusCodeSearchAgent
├── benchmarks/contextbench/         # 数据集加载 / runner / 预测导出与官方评分
├── tests/
│   ├── unit/                        # 零外部依赖（fixture 回放）
│   ├── integration/                 # graph 引擎（需 openjiuwen）
│   └── e2e/                         # 真实 Milvus
├── third_party/contextbench/        # ContextBench（git submodule，不随仓库携带）
└── docs/                            # 本文档树（zh / en / feature）
```
