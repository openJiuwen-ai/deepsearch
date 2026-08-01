# openJiuwen-CodeSearch

**Agentic 代码检索**：输入一段 GitHub issue / 问题描述，输出"解决它应查看的代码文件与行号区间"。

基于 **openJiuwen agent-core** 构建：一个多轮检索 Agent（主模型决策 + 轻量过滤模型逐行提取）
在 Milvus 双 BM25 索引（词元 + 字符三元组）上自主搜索、扩展上下文、维护片段记忆，最终提交
最相关的代码片段。自 [jiuwenCoder](https://github.com/pavlosvougiouklis/jiuwenCoder) 重构而来，
架构对齐同仓 [deepsearch](../deepsearch) 的分层模式。

```
┌─────────────────────── 索引（离线，无需 LLM key） ───────────────────────┐
│  本地仓库 → AST 切块(函数/类) → Milvus (Token BM25 + Trigram BM25)      │
│  增量：文件哈希去重，同仓多 revision 共享未变更文件的索引                  │
└──────────────────────────────────────────────────────────────────────┘
┌─────────────────────── 检索（在线，需 LLM key） ─────────────────────────┐
│  issue → Agent 多轮循环: 看仓库地图 / 双模式搜索 / 展开上下文 / 维护记忆   │
│        → 过滤模型逐行提取相关行 → 提交 → [文件+行号区间] 列表             │
└──────────────────────────────────────────────────────────────────────┘
```

## 特性

- **双引擎等价实现**：`graph`（openjiuwen workflow 图形态，默认，Studio/Ops 可观测）与
  `react`（纯代码循环兜底）共享同一份阶段逻辑，集成测试锁定两者输出逐字节一致；
- **双 BM25 检索**：标准词元检索 + 字符三元组检索（可精确匹配 `data.sum()`、堆栈跟踪等含符号子串）；
- **增量索引**：按 `sha256(路径+内容)` 去重，同一仓库的多个 commit 共享未变更文件；
- **双模型架构**：贵模型（默认 GPT-5）只做搜索决策，便宜模型（默认 GPT-5-mini）逐行过滤，控制成本；
- **运行隔离**：per-run context，同一进程内多请求并发互不串扰；
- **提前终止**：索引未就绪 fail-fast；连续多轮零收获自动停（不空烧 LLM 调用）。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | >= 3.11 | |
| Milvus | >= 2.5，默认 `localhost:19530` | 索引与检索都需要；BM25 Function 依赖 2.5+ |
| LLM | `OPENAI_API_KEY` + `OPENAI_BASE_URL`（默认 `https://openrouter.ai/api/v1`） | 仅检索需要；索引默认（纯稀疏）**不需要** |

## 安装

```sh
git clone <本仓库> && cd codesearch/codesearch
```

依赖同仓 `base/`（openjiuwen-search-base，search 公共能力过渡包）。

方式一：uv（openjiuwen 依赖含预发布版，需 `--prerelease=allow`）

```sh
uv venv .venv && uv pip install --prerelease=allow -e ../base -e '.[dev,milvus,llm]'
```

方式二：pip（先装 base）

```sh
python3 -m venv .venv && .venv/bin/pip install -e ../base -e '.[dev,milvus,llm]'
```

可选依赖分组：`milvus`（pymilvus）/ `llm`（openjiuwen，graph 引擎与真实 LLM 调用）/
`embed`（aiohttp，仅稠密向量模式）/ `retropus`（tree-sitter + bm25s，`engine=retropus` 的 KG 索引）/
`bench`（pandas+pyarrow，跑 ContextBench）/ `dev`（pytest）。
核心包只依赖 pydantic，不装任何 extra 也可运行单元测试与 fake 检索器。
详见 [docs/feature/framework/retropus-agent.md](docs/feature/framework/retropus-agent.md)。

## 启动 Milvus（本机）

单容器 embedded 版（最省内存）：

```sh
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh && bash standalone_embed.sh start
```

健康检查：

```sh
curl -sf http://localhost:9091/healthz && echo " milvus healthy"
```

在共享服务器上部署（含资源上限、空间控制、清理与同事通知模板）：
见 [docs/feature/runbook-server-indexing.md](docs/feature/runbook-server-indexing.md)。

## 快速开始（CLI）

### 1. 索引一个本地仓库

```sh
.venv/bin/python main.py index --repo /path/to/your/repo --collection my_repo
```

常用参数：

```sh
# 小样试点：只索引前 200 个文件（共享服务器上建议先这样测量空间占用）
.venv/bin/python main.py index --repo /path/to/repo --collection pilot --max-files 200

# 空间敏感场景：跳过 trigram 字段（省 ~85% 存储；代价：精确子串检索失效）
.venv/bin/python main.py index --repo /path/to/repo --collection my_repo --no-trigram

# 指定版本标签（任意字符串，同一 collection 可容纳多个 revision）
.venv/bin/python main.py index --repo /path/to/repo --collection my_repo --revision abc123

# 重建 collection（显式 drop 旧数据，慎用）
.venv/bin/python main.py index --repo /path/to/repo --collection my_repo --reset
```

预期输出形如：`Indexed 132 files (132 new, 0 reused), 890 chunks inserted.`
重复索引未变更的仓库会显示 `(0 new, 132 reused)` —— 增量去重生效。

### 2. 检索

```sh
export OPENAI_API_KEY="sk-or-v1-..."
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"   # 默认即可，可省略
```

```sh
.venv/bin/python main.py search --collection my_repo --query "TypeError when calling foo() with empty list" --top-k 10
```

issue 较长时用文件传入：

```sh
.venv/bin/python main.py search --collection my_repo --query-file issue.txt --top-k 20
```

预期输出形如：

```
Termination: submitted | turns=6 | cost=$0.1234
 1. src/utils/foo.py (L42-L57)
 2. src/core/handler.py (L103-L120)
 ...
```

`Termination` 含义：`submitted`（agent 主动提交）/ `stagnated`（连续多轮零收获提前停）/
`max_turns`（轮次耗尽，降级返回记忆内容）/ `index_not_ready`（该 collection/revision 无索引数据）。

## Python API

```python
import asyncio
from openjiuwen_codesearch import CodeSearchConfig, CodeSearchRetriever

async def main():
    config = CodeSearchConfig.from_env()      # 读取 OPENAI_API_KEY / OPENAI_BASE_URL / MODEL
    retriever = CodeSearchRetriever(config, collection_name="my_repo")

    report = await retriever.index_repository("/path/to/repo", revision="abc123")
    print(report)                              # files_total/files_new/chunks_inserted

    result = await retriever.search("issue 描述...", revision="abc123", top_k=20)
    for hit in result.hits:
        print(hit.file_path, hit.start_line, hit.end_line)

asyncio.run(main())
```

自定义模型 / 本地端点（任意 OpenAI 兼容 API）：

```python
from openjiuwen_codesearch.config.llm import LLMConfig, LLMSuite

config = CodeSearchConfig(
    llm=LLMSuite(
        main=LLMConfig(model_name="qwen3:32b", api_base="http://localhost:11434/v1"),
        filter=LLMConfig(model_name="qwen3:8b", api_base="http://localhost:11434/v1", max_tokens=2048),
    )
)
```

兼容旧接口：`from openjiuwen_codesearch import JiuwenRetriever`（`CodeSearchRetriever` 的别名，保留一个版本）。

## 关键配置

全部配置见 `openjiuwen_codesearch/config/`（pydantic，构造传参，无全局可变状态）。
常用项：

| 配置 | 默认 | 说明 |
|---|---|---|
| `agent.engine` | `auto` | `graph`（workflow 图形态）/ `react`（代码循环）/ `auto`（graph 可用则 graph）/ `retropus`（KG+BM25 Retropus 检索，需 `[retropus]` extra；**无** `ENGINE=` 环境变量，需程序赋值或 CLI `--engine`） |
| `agent.max_turns` | 20 | graph/react 检索循环轮次上限（retropus 用 `retropus.max_rounds`） |
| `agent.stagnation_rounds` | 3 | 连续 N 个零收获检索轮后提前终止（仅 graph/react） |
| `agent.filter_concurrency` | 8 | 过滤模型并发上限 |
| `index.max_num_files_per_repo` | None | 每仓库索引文件数上限（空间控制） |
| `index.enable_trigram` | True | trigram 字段开关（存储大头，≈原文 7 倍体积） |
| `milvus.collection_prefix` | `cs_` | 与其他产品共用 Milvus 实例时的命名空间隔离 |
| `milvus.schema_version` | `v1` | schema 演进版本，变更 schema 必须递增 |

### Retropus（`CodeSearchConfig.retropus`）

`engine=retropus` 时生效；由 `RetropusSearchAgentConfig.from_env()` 读取
`codesearch/.env` / 进程环境（进程环境优先）。模板见 [`.env.example`](.env.example)；
完整字段表见 [retropus-agent.md](docs/feature/framework/retropus-agent.md)。

| 配置 / 环境变量 | 默认 | 说明 |
|---|---|---|
| `retriever` / `RETRIEVER` | `bm25` | 仅支持 `bm25` |
| `max_rounds` / `MAX_ROUNDS` | 12 | LLM 决策轮次上限 |
| `max_tool_calls` / `MAX_TOOL_CALLS` | 24 | 工具调用总次数上限 |
| `max_final_spans` / `MAX_FINAL_SPANS` | 25 | 最终输出 span 上限 |
| `max_obs_chars` / `MAX_OBS_CHARS` | 6000 | 工具观察文本截断 |
| `max_read_lines` / `MAX_READ_LINES` | 400 | `read_file` 行数上限 |
| `max_ast_depth` / `MAX_AST_DEPTH` | 6 | KG AST 遍历深度 |
| `chunk_size` / `CHUNK_SIZE` | 1000 | 文本切块大小 |
| `chunk_overlap` / `CHUNK_OVERLAP` | 200 | 文本切块重叠 |
| `code_aware_tokenizer` / `CODE_AWARE_TOKENIZER` | false | 代码感知分词 |
| `tokenize_workers` / `TOKENIZE_WORKERS` | `cpu_count-1` | 分词并行度 |
| `min_spans_before_finish` / `MIN_SPANS_BEFORE_FINISH` | 3 | 配合 `IMP_ANTI_EARLY_FINISH` |
| `min_files_before_finish` / `MIN_FILES_BEFORE_FINISH` | 1 | 配合 `IMP_ANTI_EARLY_FINISH` |
| `min_mandatory_return_spans` / `MIN_MANDATORY_RETURN_SPANS`（或 `RETROPUS_MIN_MANDATORY_RETURN_SPANS`） | 0 | 结束时强制补齐到 N 条（0=关） |
| `IMP_*` / `IMP_ALL` | 见下 | 改进开关；仅 `IMP_INHERITS_EXPAND` 默认开 |

`IMP_*`：`BAN_TESTS` / `ANTI_EARLY_FINISH` / `SAME_FILE_EXPAND` /
`SECOND_FILE_PROBE` / `INHERITS_EXPAND`（默认开）。
`IMP_ALL=0|1` 可先统一关/开，再被单个 `IMP_*` 覆盖。

## 测试

```sh
.venv/bin/python -m pytest tests/unit -W ignore        # 零外部依赖（70+ 用例，含 agent 轨迹回放）
```

```sh
.venv/bin/python -m pytest tests/integration -W ignore  # 需 openjiuwen（graph 引擎，真实 Runner 驱动）
```

```sh
.venv/bin/python -m pytest -m e2e -W ignore             # 需本地 Milvus 实例
```

## ContextBench 评测

```sh
git submodule update --init --recursive     # 拉取 third_party/contextbench
```

将数据集 `contextbench_verified.parquet` 放入 `third_party/contextbench/data/`，然后：

```sh
.venv/bin/pip install -e '.[bench]' && OPENAI_API_KEY=sk-... .venv/bin/python -m benchmarks.contextbench.runner --num-instances 4
```

Retropus 引擎（需 `[retropus]`，无 Milvus；配置见 `.env` / 上表）：

```sh
.venv/bin/pip install -e '.[bench,retropus,llm]'
OPENAI_API_KEY=sk-... .venv/bin/python -m benchmarks.contextbench.runner --engine retropus --num-instances 5
```

预测 JSONL 与评分输出在 `./results/`。

## 项目分层（依赖方向只允许向左）

```
[base] ← domain ← config ← algorithm ← framework/openjiuwen ← api
 公共能力   纯模型    pydantic   纯算法+工具    图/编排/隔离      门面
                                  ↑ indexing / retrieval（索引与检索基建）
benchmarks/ 只依赖公共 API；核心包不得反向 import。
```

`base/`（同仓 openjiuwen-search-base 过渡包）承载 search 场景公共能力：
LLM 适配、embedding 客户端、Milvus expr 安全构造与命名约定、BaseNode 模板、
日志、运行注册表——位于依赖图最底层，不依赖任何产品包。

## Milvus 共用

默认支持与 deepsearch 等产品共用同一 Milvus 实例：collection 带 `cs_` 前缀 +
`__{schema_version}` 后缀、独立连接别名，只操作自己命名空间（e2e 用例锁定该行为）。
详见 [安装指导](docs/zh/2.安装指导/README.md)。

## 文档

- [docs/README.md](docs/README.md) — 文档地图（产品简介 / 安装 / 快速开始 / 开发指南 / FAQ，中英双语）
- [docs/feature/framework/codesearch-workflow.md](docs/feature/framework/codesearch-workflow.md) — 检索工作流设计（双引擎 / 图结构 / 运行隔离）
- [docs/feature/framework/retropus-agent.md](docs/feature/framework/retropus-agent.md) — Retropus 引擎（KG/BM25、工具隔离、全部 `retropus.*` / `IMP_*` 配置）
- [docs/feature/algorithm/search-agent.md](docs/feature/algorithm/search-agent.md) — 检索智能体设计（工具集 / 过滤 / 记忆）
- [docs/feature/runbook-server-indexing.md](docs/feature/runbook-server-indexing.md) — 共享服务器部署 runbook

## 常见问题

- **uv 安装报 a2a-sdk 预发布错误** → 加 `--prerelease=allow`（openjiuwen 的传递依赖含预发布版）。
- **search 返回 `index_not_ready`** → 该 collection/revision 尚无数据：先 `index`，且 `--revision`
  必须与索引时一致（默认都是 `local`）。
- **`docker ps` 权限不足**（Linux 服务器）→ `sudo usermod -aG docker $USER` 后重新登录。
- **索引大仓库担心空间** → 先 `--max-files 200` 小样实测外推；空间敏感再加 `--no-trigram`。
- **SSL 报错 `ssl_cert is required` / `SAFE_CERT_DIR is not set`** → 已由 SDK 自动处理
  （默认注入 certifi CA 包并注册其目录）；使用自签证书时配置 `LLMConfig.ssl_cert`。
- **graph 引擎报 `workflow execution exceeded time limit of 60 seconds`** → openjiuwen
  默认超时仅 60s，SDK 已按运行注入 `agent.time_limit_seconds`（默认 900s），调大即可。
