# Docker 方式安装指导

社区提供了以下三种操作系统的 Docker 方式安装指南：

- [Windows 系统安装](./Windows系统安装.md)
- [Linux 系统安装](./Linux系统安装.md)
- [MacOS 系统安装](./MacOS系统安装.md)

## 镜像内的两个 HTTP 服务

DeepSearch 提供两类运行模式（对应配置中的 `search_mode`）：

| 模式 | `search_mode` | 依赖的服务 | 容器端口 |
| ---- | ------------- | ---------- | -------- |
| **DeepResearch** | `research` | 主后端 `start_backend.py` | **8000** |
| **DeepSearch** | `search` | Telemetry `server.telemetry_event_server` | **8089** |

知识库等能力走主 API（8000）。仅使用 **DeepResearch** 时，对外映射 **8000** 即可。需要使用 **DeepSearch** 模式（`POST /runs`、运行事件流等）时，还须保证调用方能访问 **8089**。

官方 `docker/Dockerfile` 的 `CMD` 会在**同一容器**内同时启动上述两个进程，执行 `docker build` / `docker run` 时**无需**再写第二条启动命令（请勿将 `CMD` 改成只启动主后端）。

**构建镜像**（源码根目录）：

```bash
docker build -f docker/Dockerfile -t <镜像标签> .
```

**端口映射建议**：

- 仅 **DeepResearch**：`-p 8000:8000`（8089 仍在容器内运行，可不映射到宿主机）。
- 需要 **DeepSearch** 模式，且从**宿主机**访问 Telemetry：增加 `-p 8089:8089`。
- 与其他容器在同一 Docker 网络内集成：可只映射 8000，通过 `http://<服务名>:8089` 访问 Telemetry。

**本地源码安装**（非 Docker）须分别启动主后端与 Telemetry，见各平台 [本地安装](../本地安装/Linux系统安装.md) 文档。

Telemetry API 说明见 [DeepSearch REST API（Telemetry）](../../../4.开发指南/API文档/deepsearch_rest_api.md)。

## Docker Compose 一键部署

除手动 `docker build` / `docker run` 外，也可用 Docker Compose 一键拉起多服务（后端 + Redis + 可选 MySQL/Milvus）。以下命令均在 `deepsearch/docker/` 目录下执行。

### 最小化（默认）

```bash
cd deepsearch/docker

# 1. 准备配置（填入 LLM / 搜索等密钥）
cp ../.env.example ./.env   # 然后编辑 .env

# 2. 一键启动 redis + deepsearch
docker compose up -d
```

最小化编排只包含 `redis` + `deepsearch`，满足 DeepResearch 与 DeepSearch 默认（`sqlite` + `in_memory`）运行需求，映射端口 **8000**（主后端）与 **8089**（Telemetry）。

### 完整栈（含 MySQL + Milvus 向量知识库）

```bash
cd deepsearch/docker
docker compose -f docker-compose.full.yml up -d
```

完整栈在最小化基础上额外拉起：

- **MySQL**（元数据/会话）+ **Redis**（会话状态）
- **etcd + minio + Milvus**（向量知识库，Milvus standalone 必须依赖 etcd 与 minio）

编排内为每个依赖服务配置了 `healthcheck`，`deepsearch` 通过 `depends_on: condition: service_healthy` 保证依赖就绪后才启动；MySQL、Milvus 等数据分别持久化到命名卷（`mysql-data`、`milvus-data` 等）。

> 完整栈由 `docker-compose.full.yml` 注入环境变量覆盖 `.env` 中的本地默认值：`DB_TYPE=mysql`、`DB_HOST=mysql`、`DB_PORT=3306`、`CHECKPOINTER_TYPE=redis`、`REDIS_URL=redis://redis:6379`、`INDEX_MANAGER_TYPE=milvus`、`MILVUS_HOST=milvus`。`DB_PASSWORD` 同时作为 MySQL root 密码（默认 `root`，生产环境务必修改）。
