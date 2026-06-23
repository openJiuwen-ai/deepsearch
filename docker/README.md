# Docker Compose 一键部署

> 英文文档：[README-en.md](./README-en.md)

通过 Docker Compose 一键编排 openJiuwen DeepSearch 后端及其依赖服务（MySQL / Redis / Milvus），免去手动逐个启动与配置。

## 前置要求

- Docker Engine ≥ 24.0
- Docker Compose v2（`docker compose` 子命令）
- 可用磁盘：minimal 约 2GB，distributed 约 8GB（含 Milvus + MinIO 镜像）

## 快速开始（最小栈）

最小栈仅启动后端容器，使用 SQLite + 内存 checkpointer，无需任何外部数据库：

```bash
# 1. 准备环境变量（至少填入 LLM / 搜索源 API key）
cp .env.example .env
#    编辑 .env，填写：
#      LLM_MODEL_NAME / LLM_API_KEY / LLM_BASE_URL
#      以及至少一个搜索源 key（JINA_API_KEY / SERPER_API_KEY / TAVILY_API_KEY 等）

# 2. 启动（后台）
docker compose -f docker/docker-compose.yml up -d

# 3. 验证
curl http://localhost:8000/api/health   # 后端健康检查
curl http://localhost:8089              # Telemetry 端点
# API 文档：http://localhost:8000/api/docs
```

默认对外端口：

| 服务 | 容器端口 | 默认宿主端口 | 说明 |
|------|----------|--------------|------|
| 后端 API | 8000 | 8000 | DeepResearch / DeepSearch 接口，`/api/docs` Swagger |
| Telemetry | 8089 | 8089 | `search_mode=search` 时的事件端点 |

如需改宿主端口，在 `.env` 里设：

```
BACKEND_PUBLISH_PORT=18000
TELEMETRY_PUBLISH_PORT=18089
```

## 三档部署

通过 `--profile` 选择依赖服务规模。后端容器始终启动，profile 仅控制额外服务。

### 1. minimal（默认）

```bash
docker compose -f docker/docker-compose.yml up -d
```

适用：个人试用、单实例、不需要持久化元数据。

`.env` 关键配置：

```
DB_TYPE=sqlite
SQLITE_DB_PATH=data/databases
CHECKPOINTER_TYPE=in_memory
INDEX_MANAGER_TYPE=milvus      # 可留 milvus 但不实际使用；tool_map=search 时不连
```

### 2. mysql —— 持久化元数据，仍单实例

```bash
docker compose -f docker/docker-compose.yml --profile mysql up -d
```

适用：需要持久化对话/报告元数据，但仍单实例运行。

`.env` 关键配置（compose 会把 mysql 容器名解析为 `mysql`）：

```
DB_TYPE=mysql
DB_HOST=mysql
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DEEPSEARCH_DB_NAME=openjiuwen_deepsearch
CHECKPOINTER_TYPE=in_memory       # 或 persistence
```

可选：`MYSQL_PUBLISH_PORT=3307` 改宿主映射端口。

### 3. distributed —— 多实例 + 知识库检索

```bash
docker compose -f docker/docker-compose.yml --profile distributed up -d
```

适用：多实例分布式部署，或需要本地知识库向量检索（`tool_map=retrieve`）。

启动的服务：deepsearch + mysql + redis + milvus（含 etcd + minio 依赖）。

`.env` 关键配置：

```
DB_TYPE=mysql
DB_HOST=mysql
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DEEPSEARCH_DB_NAME=openjiuwen_deepsearch

# 分布式 checkpointer 必须用 redis + mysql
CHECKPOINTER_TYPE=redis
REDIS_URL=redis://redis:6379
REDIS_CLUSTER_MODE=false

# 知识库向量检索
INDEX_MANAGER_TYPE=milvus
MILVUS_HOST=milvus
MILVUS_PORT=19530
MILVUS_TOKEN=

# ⚠️ distributed 模式下知识库文档必须写入 OBS，
# 需完整配置 OBS_*，否则服务无法启动
OBS_ACCESS_KEY_ID=...
OBS_SECRET_ACCESS_KEY=...
OBS_SERVER=...
OBS_REGION=...
OBS_BUCKET=...
```

可选宿主端口映射：

```
MYSQL_PUBLISH_PORT=3307
REDIS_PUBLISH_PORT=6380
MILVUS_PUBLISH_PORT=19531
MINIO_PUBLISH_PORT=9002
```

## 服务编排一览

| 服务 | 镜像 | profile | 容器端口 | 用途 |
|------|------|---------|----------|------|
| deepsearch | 本仓库构建 | 始终 | 8000 / 8089 | FastAPI 后端 + Telemetry |
| mysql | mysql:8.0 | mysql / distributed | 3306 | 元数据持久化 |
| redis | redis:7-alpine | distributed | 6379 | 分布式 checkpointer |
| milvus | milvusdb/milvus:v2.4.17 | distributed | 19530 | 向量检索 |
| etcd | quay.io/coreos/etcd:v3.5.16 | distributed | 2379 | Milvus 元数据存储 |
| minio | minio/minio | distributed | 9000 / 9001 | Milvus 对象存储 |

## 数据持久化

所有持久数据走命名卷，`docker compose down` 不删除；彻底清理用 `docker compose down -v`：

| 卷 | 挂载点 | 内容 |
|----|--------|------|
| deepsearch-data | /app/data | SQLite 数据库、知识库本地索引 |
| deepsearch-logs | /app/output | 运行日志、生成的报告 |
| mysql-data | /var/lib/mysql | MySQL 数据 |
| redis-data | /data | Redis AOF |
| milvus-data | /var/lib/milvus | Milvus 向量数据 |
| etcd-data | /etcd | Milvus 元数据 |
| minio-data | /minio_data | MinIO 对象 |

## 常用命令

```bash
# 查看日志
docker compose -f docker/docker-compose.yml logs -f deepsearch

# 重启后端
docker compose -f docker/docker-compose.yml restart deepsearch

# 仅重建后端镜像（代码变更后）
docker compose -f docker/docker-compose.yml build deepsearch && \
docker compose -f docker/docker-compose.yml up -d deepsearch

# 停止（保留数据）
docker compose -f docker/docker-compose.yml down

# 停止并删除所有数据卷
docker compose -f docker/docker-compose.yml down -v

# 查看各服务健康状态
docker compose -f docker/docker-compose.yml ps
```

## 国内构建加速

`docker/Dockerfile` 已支持 `INDEX_URL` 与 `APT_MIRROR` 构建参数。国内构建：

```bash
docker compose -f docker/docker-compose.yml build \
  --build-arg INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn \
  deepsearch
```

## 故障排查

| 现象 | 排查 |
|------|------|
| 后端容器健康检查失败 | `docker compose logs deepsearch` 看启动报错；常见是 `.env` 缺 `LLM_API_KEY` 或 `SERVICE_MODE=product` 时未设 `SERVER_AES_MASTER_KEY` |
| `distributed` 起不来 | 先确认 `.env` 里 `OBS_*` 完整，distributed 模式知识库必须用 OBS |
| Milvus 健康检查超时 | Milvus 首次启动较慢（90s+），`start_period` 已放宽；仍失败查 `docker compose logs milvus` |
| 端口被占用 | 用 `*_PUBLISH_PORT` 环境变量改宿主映射 |
| 后端连不上 mysql/redis | 确认 `.env` 里 `DB_HOST=mysql` / `REDIS_URL=redis://redis:6379` 用的是**容器服务名**而非 `localhost` |

## 与现有 Dockerfile 的关系

`docker/Dockerfile` 保持不变，compose 直接复用它构建后端镜像。compose 仅新增编排层，不修改后端构建逻辑、不改动任何业务代码。
