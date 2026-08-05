# 安装指导

> 选择安装方式请先看 [快速指引](./快速指引.md)。

## 部署方式

| 方式 | 适用场景 | 文档 |
|---|---|---|
| [源码安装](./源码安装.md) | 开发与调试 | 本地 editable 安装 `base` + `codesearch` |
| [Docker 安装](./Docker安装.md) | 容器化交付 | 自行 `docker build`（镜像含 base） |
| [whl 安装](./whl安装.md) | 生产环境 | 从正式发布地址下载 **两个** wheel |
| HTTP 服务 | 以上三种均可 | 统一入口 `codesearch-server`，见下文 |

## 仓库与依赖关系

```text
<repo_root>/
├── base/           # openjiuwen-search-base（公共能力）
└── codesearch/     # openjiuwen-codesearch（本产品）
```

- **源码**：在 `codesearch/` 下执行 `pip/uv install -e ../base -e '.[...]'`。
- **Docker**：构建上下文为 `<repo_root>`，Dockerfile 内依次安装 `./base` 与
  `./codesearch[...]`。
- **whl**：发布物为两个文件，须安装
  `openjiuwen_search_base-*.whl` 与 `openjiuwen_codesearch-*.whl`。

## 待索引仓库（本地路径）

索引的对象是**运行环境可读的本地目录**，不是 git 远程 URL。

| 步骤 | 参数 | 含义 |
|---|---|---|
| 准备 | （自行 clone） | 网上仓库先 `git clone` 到本机（或挂进容器） |
| 索引 | `--repo` / `repo_path` | **本地绝对/相对路径**，指向仓库根目录 |
| 索引 | `--collection` / `collection` | 写入 Milvus 的**集合名**（由你命名，如 `agent_core`） |
| 检索 | `--collection` / `collection` | 只用集合名；**不再传仓库路径** |

示例：

```sh
# 远程仓 → 本地目录
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core

# 索引：路径 + 自定集合名
codesearch index --repo /data/repos/agent-core --collection agent_core

# 检索：只写集合名（agent_core），不要写 git URL 或本地路径
codesearch search --collection agent_core --query "..."
```

HTTP 形态同样：`POST /api/v1/index` 的 `repo_path` 必须是服务进程所见的本地目录，
且落在 `CODESEARCH_INDEX_ROOTS` 白名单内；`POST /api/v1/search` 只带 `collection`。

> 产品**不会**代为拉取远程仓库。若只看到文档里的 `agent_core` 这类名字，那是
> **集合名**，对应你在索引时 `--collection` 起的标签，不是仓库地址。

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | >= 3.11 | |
| Milvus | >= 2.5（推荐 2.6.x） | 索引与检索均需要；全文检索依赖 2.5+ 的 BM25 Function |
| LLM API Key | `CODESEARCH_LLM_API_KEY` + `CODESEARCH_LLM_BASE_URL`（OpenAI 兼容） | 仅检索需要；默认稀疏索引模式**不需要** |

> **语言范围**：当前语法切块器 **仅支持 Python（`.py`）**。对其它语言仓库执行
> 索引会得到 0 个文件，属预期行为，不是安装失败。

## 可选依赖分组

| 分组 | 内容 | 何时需要 |
|---|---|---|
| `milvus` | pymilvus | 索引与检索真实仓库 |
| `server` | fastapi、uvicorn、pydantic-settings | 以 HTTP 服务方式运行 |
| `llm` | openjiuwen | 工作流图引擎与真实模型调用 |
| `embed` | aiohttp | 启用稠密向量模式 |
| `bench` | pandas、pyarrow | 运行评测 |
| `dev` | pytest | 开发与测试 |

核心包仅依赖 pydantic；不安装任何分组也可运行单元测试与内存态检索器。
服务实现随包分发（`openjiuwen_codesearch/server/`），whl / 镜像装完即可用
`codesearch-server` 启动。

> 与其他基于 openJiuwen 的产品同机使用时，若两者锁定的框架版本不同，请分别
> 使用独立的虚拟环境或容器。向量库层面的共存不受影响（见下文）。

## HTTP 服务

三种安装方式均可启动：

```sh
codesearch-server          # 源码部署亦可用 python start_backend.py
```

默认监听 `0.0.0.0:8100`，接口文档 `/docs`，健康检查 `/api/health`。

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查 |
| `/api/v1/search` | POST | 同步检索，返回文件与行区间 |
| `/api/v1/index` | POST | 提交索引作业（长任务），返回 `job_id`；**未配置 `CODESEARCH_INDEX_ROOTS` 时返回 403** |
| `/api/v1/jobs/{job_id}` | GET | 查询索引作业状态 |

### 安全边界（必读）

> **服务不含鉴权**。`/api/v1/index` 读取的是**服务端本地目录**，
> `/api/v1/search` 会返回文件内容。因此：
>
> 1. 必须用 `CODESEARCH_INDEX_ROOTS` 显式限定可索引根目录（`:` 分隔）；
> 2. **未配置时索引接口一律返回 403**——这是安全默认值，不是服务损坏；
> 3. 启动时若未配置白名单，进程会打印 **WARNING** 日志提示；
> 4. 生产环境须部署在可信网络内，或置于带访问控制的网关之后。

## 环境变量

模板见 [.env.example](../../../.env.example)。SDK 侧变量在
`CodeSearchConfig.from_env()` 读取；服务侧为 `CODESEARCH_` 前缀。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CODESEARCH_LLM_API_KEY` | 空 | LLM API 密钥（检索必需） |
| `CODESEARCH_LLM_BASE_URL` | 空 | OpenAI 兼容端点（须显式配置，例如 `https://api.openai.com/v1`） |
| `CODESEARCH_LLM_MODEL` | `openai/gpt-5` | **main** 模型（多轮检索决策）；不设则用此默认 |
| `CODESEARCH_FILTER_LLM_MODEL` | `openai/gpt-5-mini` | **filter** 模型（逐行提取）；不设则用此默认 |
| `MILVUS_HOST` | `localhost` | 向量库地址 |
| `MILVUS_PORT` | `19530` | 向量库端口 |
| `MILVUS_TOKEN` | 空 | 向量库鉴权（`user:password` 或 API token） |
| `CODESEARCH_HOST` | `0.0.0.0` | 服务监听地址 |
| `CODESEARCH_PORT` | `8100` | 服务监听端口 |
| `CODESEARCH_LOG_LEVEL` | `INFO` | 服务日志级别 |
| `CODESEARCH_INDEX_ROOTS` | 空 | 允许索引的根目录白名单（`:` 分隔）；留空则 `/api/v1/index` 返回 403 |

也可以不使用环境变量，直接构造 `CodeSearchConfig`（字段与 deepsearch 一致：`model_name` / `base_url` / `api_key`）注入。检索固定使用 **main + filter** 两个模型；只配 key/base_url 时模型名取上表默认值，换端点时请同步改 `CODESEARCH_LLM_MODEL` / `CODESEARCH_FILTER_LLM_MODEL`（或代码里的 `model_name`）。

## Milvus 部署

### 与其他产品共用一个实例（默认）

集合命名为 `cs_{名称}__{模式版本}`，仅读写自身命名空间：

```sh
curl -sf http://localhost:9091/healthz && echo " 实例可用，直接复用"
```

> `cs_` 前缀为 CodeSearch 保留命名空间，请勿将其他集合命名为 `cs_*__v*`。

### 独立部署

```sh
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start
curl -sf http://localhost:9091/healthz && echo " milvus healthy"
```

> 建议将脚本中的镜像标签固定为稳定版本（如 `milvusdb/milvus:v2.6.18`）。

## 验证安装

```sh
pytest tests/unit -W ignore      # 无外部依赖
pytest -m e2e -W ignore          # 需要可访问的 Milvus
```
