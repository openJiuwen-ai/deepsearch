# 安装指导

## 部署方式

| 方式 | 适用场景 |
|---|---|
| 本地源码 | 开发与调试 |
| whl 包 | 生产环境安装 |
| Docker 镜像 | 容器化交付与隔离运行（默认以服务形态启动） |
| HTTP 服务 | 以后端服务方式对外提供检索能力 |

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | >= 3.11 | |
| Milvus | >= 2.5（推荐 2.6.x） | 索引与检索均需要；全文检索依赖 2.5+ 的 BM25 Function；默认 `localhost:19530` |
| LLM | `OPENAI_API_KEY` + `OPENAI_BASE_URL`（默认 `https://openrouter.ai/api/v1`） | 仅检索需要；默认稀疏索引**不需要** |


## 方式一：本地源码

本包依赖同仓的 `openjiuwen-search-base`（search 场景公共能力包），需一并安装。

使用 uv：

```sh
uv venv .venv && uv pip install -e ../base -e '.[dev,milvus,llm]'
```

使用 pip：

```sh
python3 -m venv .venv && .venv/bin/pip install -e ../base -e '.[dev,milvus,llm]'
```

可选依赖分组：

| 分组 | 内容 | 何时需要 |
|---|---|---|
| `milvus` | pymilvus | 索引与检索真实仓库 |
| `server` | fastapi、uvicorn、pydantic-settings | 以 HTTP 服务方式运行 |
| `llm` | openjiuwen | 工作流图引擎与真实模型调用 |
| `embed` | aiohttp | 启用稠密向量模式 |
| `retropus` | tree-sitter、bm25s | `engine=retropus` 的 KG/BM25 索引 |
| `bench` | pandas、pyarrow | 运行评测 |
| `dev` | pytest | 开发与测试 |

核心包仅依赖 pydantic，不安装任何分组也可运行单元测试与内存态检索器。
Retropus 配置见 [`.env.example`](../../../.env.example) 与
[feature/framework/retropus-agent.md](../../feature/framework/retropus-agent.md)。

> 与其他基于 openJiuwen 的产品同机使用时，若两者锁定的框架版本不同，请分别
> 使用独立的虚拟环境或容器（Python 发行包在同一环境中只能存在一个版本）。
> 向量库层面的共存不受影响，见下文。

## 方式二：whl 包

```sh
python -m build && pip install dist/openjiuwen_codesearch-*.whl
```

whl 包含库、`codesearch` 命令行与 HTTP 服务。安装后直接用 `codesearch-server`
启动服务，不需要源码树。

> 在源码目录之外用 `uv pip install` 安装 `llm` 附加依赖会因预发布版本报错——
> openJiuwen 锁定了 `a2a-sdk==1.0.0a0`。可加 `--prerelease=allow`，
> 或改用 `pip`（pip 允许被精确锁定的预发布版本）。源码安装不受影响，
> `pyproject.toml` 的 `[tool.uv]` 已放行。

## 方式三：Docker

构建上下文需为仓库根目录（镜像同时包含 base 包）：

```sh
docker build -f codesearch/docker/Dockerfile -t openjiuwen-codesearch:0.2.0 .
```

```sh
docker run --rm -e OPENAI_API_KEY -e OPENAI_BASE_URL -e MILVUS_HOST=host.docker.internal \
  -v /path/to/repo:/repo -v $(pwd)/output:/app/output \
  openjiuwen-codesearch:0.2.0 index --repo /repo --collection demo
```

## 方式四：HTTP 服务

```sh
pip install -e '.[milvus,llm,server]'
codesearch-server          # 源码部署亦可用 python start_backend.py
```

默认监听 `0.0.0.0:8100`，接口文档 `/docs`，健康检查 `/api/health`。

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查 |
| `/api/v1/search` | POST | 同步检索，返回文件与行区间 |
| `/api/v1/index` | POST | 提交索引作业（长任务），返回 `job_id`；**需先配置 `CODESEARCH_INDEX_ROOTS`，否则返回 403** |
| `/api/v1/jobs/{job_id}` | GET | 查询索引作业状态 |

服务参数通过 `CODESEARCH_` 前缀的环境变量配置（见下表）。
服务实现随包分发（`openjiuwen_codesearch/server/`），因此源码、whl、镜像三种
方式都能起服务。

## 环境变量

所有环境变量在 `CodeSearchConfig.from_env()` 调用时读取，模板见
[.env.example](../../../.env.example)：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | 空 | LLM API 密钥（检索必需） |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI 兼容端点 Base URL |
| `MODEL` | `openai/gpt-5` | 模型 id（OpenRouter 用 `provider/model`） |
| `MILVUS_HOST` | `localhost` | 向量库地址 |
| `MILVUS_PORT` | `19530` | 向量库端口 |
| `MILVUS_TOKEN` | 空 | 向量库鉴权（`user:password` 或 API token） |
| `CODESEARCH_HOST` | `0.0.0.0` | 服务监听地址（仅服务形态） |
| `CODESEARCH_PORT` | `8100` | 服务监听端口（仅服务形态） |
| `CODESEARCH_LOG_LEVEL` | `INFO` | 服务日志级别（仅服务形态） |
| `CODESEARCH_INDEX_ROOTS` | 空 | **允许索引的根目录白名单**（`:` 分隔）；留空则 `/api/v1/index` 返回 403 |

> **服务形态的安全边界**：服务本身不带鉴权，`/api/v1/index` 读取的是服务端本地
> 目录、`/api/v1/search` 会返回文件内容。因此索引范围必须用
> `CODESEARCH_INDEX_ROOTS` 显式限定（未配置时索引接口直接拒绝），
> 且服务应部署在可信网络内或置于带访问控制的网关之后。

也可以完全不使用环境变量，直接构造 `CodeSearchConfig` 传入所有配置。

## Milvus 部署

### 与其他产品共用一个实例（默认）

CodeSearch 的集合统一命名为 `cs_{名称}__{模式版本}`，使用独立连接别名，
仅读写自身命名空间，不会触碰实例上的其他集合。因此可以直接复用环境中
已有的 Milvus：

```sh
curl -sf http://localhost:9091/healthz && echo " 实例可用，直接复用"
```

> `cs_` 前缀为 CodeSearch 保留的命名空间，请勿将其他集合命名为 `cs_*__v*`。

如需更强的隔离，可通过 `MilvusConfig.database_name` 使用 Milvus 2.2+ 的
database 能力。

### 独立部署一个实例

```sh
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start
```

```sh
curl -sf http://localhost:9091/healthz && echo " milvus healthy"
```

> 建议将脚本中的镜像标签固定为稳定版本（如 `milvusdb/milvus:v2.6.18`）。
> 使用非默认端口时，通过 `--milvus-port` 或 `MILVUS_PORT` 指定。

运行提示：Milvus 社区版没有集合级资源配额，大批量重建索引会影响同实例上
其他集合的查询延迟，建议错峰执行，或为批量作业临时使用独立实例。

## 验证安装

```sh
pytest tests/unit -W ignore
```

```sh
pytest -m e2e -W ignore
```

前者不依赖任何外部服务；后者需要可访问的 Milvus 实例。
