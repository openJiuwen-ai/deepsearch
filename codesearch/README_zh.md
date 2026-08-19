**阅读语言：** [English](./README.md) | 简体中文

# 🔍 什么是 openJiuwen-CodeSearch

**openJiuwen-CodeSearch** 是一款面向代码仓库的智能体检索引擎。输入一段问题描述
（如 GitHub Issue、缺陷报告、功能诉求），输出"要解决它应当查看哪些文件的哪些行"。
本系统以 openJiuwen agent-core 能力为基础，由检索智能体自主完成查看仓库结构、
多策略搜索、扩展上下文、筛选与提交等步骤，为缺陷定位、代码问答与自动修复流水线
提供精准的代码上下文。

## 应用场景

- **缺陷定位**：给定 Issue 或缺陷报告，定位到需要修改的函数与代码行，作为
  自动修复（issue-to-patch）流水线的第一环。
- **代码问答上下文供给**：为"这个功能在哪实现""这个报错从哪来"等问题提供
  精确到行的代码依据，替代人工翻仓。
- **大型仓库导航**：在数十万行规模的陌生仓库中，以自然语言描述换取相关代码
  切片，降低新成员上手成本。

## 核心特性

- **智能体式多轮检索**
    + 检索智能体自主决策：查看仓库结构、发起多策略搜索、按行展开上下文、
      维护片段记忆、自主提交结论。
    + 双模型协同：高能力模型负责搜索决策，轻量模型负责逐行筛选相关代码，
      在保证质量的同时控制成本。

- **面向代码的混合索引**
    + 语法感知切块：按函数与类边界切分，每个片段都是完整语义单元
      （**当前仅支持 Python**）。
    + 双路稀疏检索：标准词元检索用于语义关键词，字符三元组检索用于
      `data.sum()`、堆栈跟踪等含符号的精确子串；可选稠密向量混合检索。
    + 增量索引：按文件内容哈希去重，同一仓库的多个版本共享未变更文件的索引。

- **双引擎等价实现**
    + 工作流图引擎（默认）：以 openJiuwen 工作流图承载检索循环，节点级可观测。
    + 纯代码循环引擎：作为无框架依赖的兜底形态。
    + 两个引擎共享同一份阶段逻辑，测试锁定输出逐字节一致。

- **面向服务的工程能力**
    + 多形态交付：SDK、命令行、HTTP 服务与容器镜像。
    + 运行隔离：每次请求独立上下文，同进程并发互不串扰。
    + 多产品共存：向量库集合以产品前缀与模式版本命名，可与其他检索产品
      共用同一 Milvus 实例。
    + 完整的超时、有界并发、生命周期与 token 用量统计能力。

## 系统架构

```
┌──────────────── 索引（离线） ────────────────┐
│  代码仓库 → 语法感知切块 → Milvus 双路稀疏索引  │
│  增量：文件哈希去重，多版本共享未变更文件        │
└────────────────────────────────────────────┘
┌──────────────── 检索（在线） ────────────────┐
│  问题描述 → 检索智能体（决策模型 · 筛选模型 ·   │
│            片段记忆 · 五类工具）→ 文件+行区间   │
└────────────────────────────────────────────┘
```

分层设计（依赖方向单向）：`domain ← config ← algorithm ← framework ← api`，
search 场景公共能力沉淀于 `openjiuwen-search-base`。详见
[开发指南](docs/zh/4.开发指南/README.md)。

# 📦 安装指导

支持三种部署方式：**源码**、**Docker 镜像**（自行构建）、**正式 whl**
（发布地址下载 `openjiuwen-search-base` + `openjiuwen-codesearch`）。

请先阅读 [快速指引](docs/zh/2.安装指导/快速指引.md) 选择方式；公共环境变量、
Milvus、待索引仓库说明与安全边界见
[安装指导总览](docs/zh/2.安装指导/README.md)。

源码快速安装示例（须同时安装同仓 `base`）：

```sh
python3 -m venv .venv && .venv/bin/pip install -e ../base -e '.[dev,milvus,llm,server]'
```

计划使用 `engine=retropus`（进程内知识图谱 + BM25，不依赖 Milvus）时，
额外加装 `retropus` 分组：`'.[dev,llm,retropus]'`。

索引需要**本地仓库目录**（远程仓请先 clone）；检索使用索引时起的集合名：

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
codesearch index --repo /data/repos/agent-core --collection agent_core
codesearch search --collection agent_core --query "..."
```

计划使用 `engine=retropus`（进程内知识图谱 + BM25，不依赖 Milvus）时，
额外加装 `retropus` 分组：`'.[dev,llm,retropus]'`。

以 HTTP 服务方式运行：

```sh
codesearch-server
```

默认监听 `0.0.0.0:8100`。服务**不含鉴权**，索引接口只接受
`CODESEARCH_INDEX_ROOTS` 白名单内的路径（未配置时返回 403，属预期行为）。
当前索引**仅支持 Python（`.py`）**。

# 🚀 快速上手

索引一个本地仓库，然后用自然语言描述检索：

```sh
codesearch index --repo /path/to/your/repo --collection my_repo
```

```sh
# 推荐：复制模板为 .env 并填写（.env 覆盖同名 export；无 .env 时再用 export）
cp .env.example .env
# 编辑 .env：CODESEARCH_LLM_API_KEY / CODESEARCH_LLM_BASE_URL（及可选模型名）

# 备选（无 .env、Docker -e 等场景）：
# export CODESEARCH_LLM_API_KEY="your-key"
# export CODESEARCH_LLM_BASE_URL="https://api.openai.com/v1"
# export CODESEARCH_LLM_MODEL="openai/gpt-5"            # 可选，默认见上
# export CODESEARCH_FILTER_LLM_MODEL="openai/gpt-5-mini"

codesearch search --collection my_repo --query "TypeError when calling foo() with empty list"
```

配置优先级与变量表见 [安装指导 · 环境变量](docs/zh/2.安装指导/README.md#环境变量)。检索使用两个模型：**main**（多轮决策）默认 `openai/gpt-5`，**filter**（逐行提取）默认 `openai/gpt-5-mini`。换端点时请把模型名改成该服务实际支持的名字。Python API 与完整说明见 [快速开始](docs/zh/3.快速上手/3.快速上手.md)。

# ⚙️ 引擎

`CodeSearchConfig.agent.engine` 决定索引与检索走哪条路径
（`graph` | `react` | `retropus`），默认 `graph`。

| 引擎 | 作用 |
|---|---|
| `graph` | 默认产品路径：检索循环以 openJiuwen 工作流图运行（节点级可观测）。索引写入 **Milvus**（双路稀疏 BM25）。需 `[llm]`。 |
| `react` | 与 `graph` 共用 Milvus 索引与循环阶段，但是纯 Python agent 循环（无工作流图）。测试锁定二者输出逐字节一致。 |
| `retropus` | 独立 agent 与工具集，知识图谱 + BM25（无 Milvus）。索引驻留内存，并**落盘**到 `RETROPUS_INDEX_DIR`（默认 `./output/retropus/<collection>/`）以便复用。须显式指定。需 `[retropus]`。 |

### 如何选择引擎

**没有** `ENGINE=` 环境变量。在代码、HTTP 或 CLI 中设置：

| 入口 | 方式 |
|---|---|
| **Python SDK** | 构造 `CodeSearchRetriever` 前设置 `config.agent.engine = "retropus"`（或 `"graph"` / `"react"`）。字段：`openjiuwen_codesearch/config/agent.py` 中的 `SearchAgentConfig.engine`（默认 `graph`）。 |
| **HTTP API** | `POST /api/v1/index` 与 `POST /api/v1/search` 请求体可选 `"engine"`（默认 `"graph"`）。索引与检索须使用**同一**引擎；Retropus 与 Milvus 索引混用返回 **409**。 |
| **`codesearch` CLI** | `codesearch --engine retropus index --repo … --collection my_repo`，再 `codesearch --engine retropus search --collection my_repo --query "…"`。默认引擎为 `graph`；可用 `--index-dir` / `RETROPUS_INDEX_DIR`。 |
| **ContextBench CLI** | `python -m benchmarks.contextbench.runner --engine retropus`（亦 `--engine graph` / `react`；默认 `graph`）。 |
| **环境变量** | 引擎名本身不由环境变量选择。后端相关变量仍生效：`graph`/`react` 用 `MILVUS_*`；Retropus 用 `CodeSearchConfig.retropus` 下的 `MAX_*` / `FEAT_*` / `RETROPUS_INDEX_DIR` 等（见 [`.env.example`](.env.example)）。LLM 凭证各引擎共用：`CODESEARCH_LLM_API_KEY` / `CODESEARCH_LLM_BASE_URL`。 |

```python
from openjiuwen_codesearch import CodeSearchConfig, CodeSearchRetriever

config = CodeSearchConfig.from_env()
config.agent.engine = "retropus"  # 或 "graph" / "react"
retriever = CodeSearchRetriever(config=config)
```

Retropus 设计与参数：[retropus-agent.md](docs/feature/framework/retropus-agent.md)。
graph/react 工作流：[codesearch-workflow.md](docs/feature/framework/codesearch-workflow.md)。

# 📊 评测

可在 [ContextBench](docs/zh/3.快速上手/3.快速上手.md) 上评估检索质量——该数据集
由真实仓库 issue 与标注好的上下文答案组成。数据集以 git submodule 引入
（登记在仓库根 `.gitmodules`，路径 `codesearch/third_party/contextbench`）：

```sh
# 在 monorepo 根目录执行
git submodule update --init --recursive
```

```sh
# 在 codesearch/ 目录
pip install -e '.[bench,milvus,llm]'   # [bench]=读数+评分；检索另需 milvus/llm
python -m benchmarks.contextbench.runner --num-instances 32
```

预测与自动评分结果在 `./results/`。依赖以产品 `[bench]` 为准（含 pandas/
pyarrow 与 tree-sitter*），不必再装上游 `requirements.txt`。完整说明见
[快速上手](docs/zh/3.快速上手/3.快速上手.md#评测)。

# 💻 开发指南

分层架构、扩展点（新增语言、新增检索后端、新增智能体工具）、测试分层与
工程约定见 [开发指南](docs/zh/4.开发指南/README.md) 与
[AGENTS.md](AGENTS.md)；特性设计文档见 [docs/feature](docs/feature/)。

# ❓ FAQ

常见问题见 [FAQ](docs/zh/5.FAQ/README.md)。

# ⚖️ 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。

# 🤝 贡献方式

欢迎通过 Issue 与 Pull Request 参与贡献。提交代码前请阅读
[AGENTS.md](AGENTS.md) 中的分层纪律与测试约定，并确保：

```sh
pytest tests/unit -W ignore
```
