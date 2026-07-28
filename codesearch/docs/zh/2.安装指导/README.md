# 安装指导

## 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | >= 3.11 | |
| Milvus | >= 2.5（推荐 2.6.x） | BM25 Function 依赖 2.5+；默认 `localhost:19530` |
| LLM API Key | `OPENROUTER_API_KEY` 环境变量 | 仅检索需要；默认稀疏索引**不需要** |

## 安装

```sh
git clone <本仓库> && cd codesearch/codesearch
```

方式一：uv（openjiuwen 依赖含预发布版，需 `--prerelease=allow`）：

```sh
uv venv .venv && uv pip install --prerelease=allow -e '.[dev,milvus,llm]'
```

方式二：pip：

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,milvus,llm]'
```

可选依赖分组：`milvus`（pymilvus）/ `llm`（openjiuwen）/ `embed`（aiohttp，稠密向量模式）/
`bench`（pandas+pyarrow，ContextBench 评测）/ `dev`（pytest）。
核心包仅依赖 pydantic，不装 extra 也可运行单元测试。

## 启动 Milvus（单机）

单容器 embedded 版（最省内存）：

```sh
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start
```

健康检查：

```sh
curl -sf http://localhost:9091/healthz && echo " milvus healthy"
```

> 注意：脚本 master 分支可能默认拉取 beta 镜像，建议将脚本中的镜像 tag
> 固定为稳定版（如 `milvusdb/milvus:v2.6.18`）。

在**共享服务器**上部署（资源上限、命名隔离、空间控制、清理流程）：
见 [feature/runbook-server-indexing.md](../../feature/runbook-server-indexing.md)。

## 验证安装

```sh
.venv/bin/python -m pytest tests/unit -W ignore     # 零外部依赖
.venv/bin/python -m pytest -m e2e -W ignore         # 需运行中的 Milvus
```
