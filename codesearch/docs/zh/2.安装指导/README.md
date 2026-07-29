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

codesearch 依赖同仓的 `base/`（openjiuwen-search-base，search 场景公共能力过渡包，
经相对路径解析——两产品**各自独立解析依赖**，不共享 lock）。

方式一：uv（`uv sync` 在本目录开箱即用；`uv pip` 需 `--prerelease=allow`）：

```sh
uv venv .venv && uv pip install --prerelease=allow -e ../base -e '.[dev,milvus,llm]'
```

方式二：pip（先装 base 再装本包）：

```sh
python3 -m venv .venv && .venv/bin/pip install -e ../base -e '.[dev,milvus,llm]'
```

可选依赖分组：`milvus`（pymilvus）/ `llm`（openjiuwen）/ `embed`（aiohttp，稠密向量模式）/
`bench`（pandas+pyarrow，ContextBench 评测）/ `dev`（pytest）。
核心包仅依赖 pydantic，不装 extra 也可运行单元测试。

> ⚠️ **与 deepsearch 不能共用同一个 venv**：gitcode agent-core 与 PyPI openjiuwen
> 是同一个发行名 `openjiuwen`，两产品当前锁定版本不同（v0.1.13 vs 0.1.10.post3），
> 同一虚拟环境装不下两者。同机使用两个产品请**分 venv/容器**
> （Milvus 层的共存不受影响，见下节）。

## Milvus 部署（默认：与其他产品共用一个实例）

codesearch 支持与 deepsearch 等产品**共用同一个 Milvus 实例**（考虑用户同时使用
两个服务时的资源占用）：codesearch 的 collection 一律带 `cs_` 产品前缀与
`__{schema_version}` 版本后缀，连接别名为 `codesearch`，只读写自己命名空间下的
collection，对实例上的其他 collection 零触碰（有 e2e 用例锁定该行为）。

> ⚠️ **`cs_` 前缀为 codesearch 的保留命名空间**：请勿将其他产品/自建的
> collection 命名为 `cs_*__v*` 形式——codesearch 的 `--reset` 会按此实名
> 重建自己的 collection。

**已有 Milvus（例如 deepsearch 已部署）** → 直接复用，无需任何额外配置：

```sh
curl -sf http://localhost:9091/healthz && echo " 复用现有实例即可（默认连接 localhost:19530）"
```

**尚无 Milvus** → 启动一个（单容器 embedded 版，最省内存）：

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

运维提示：开源版 Milvus 无 per-collection 资源配额，codesearch 的**批量重索引**
会推高共用实例的查询延迟——大规模索引建议错峰执行，或临时使用独立实例
（端口错开即可，`--milvus-port` 指定）。共享服务器部署（资源上限、空间控制、
清理流程）见 [feature/runbook-server-indexing.md](../../feature/runbook-server-indexing.md)。

## 验证安装

```sh
.venv/bin/python -m pytest tests/unit -W ignore     # 零外部依赖
.venv/bin/python -m pytest -m e2e -W ignore         # 需运行中的 Milvus
```
