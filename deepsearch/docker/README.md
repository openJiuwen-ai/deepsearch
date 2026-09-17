**Read this in:** 简体中文 | [English](./README-en.md)

# Docker Compose 部署

本目录提供三种独立的 Compose 编排，分别用于本地试用、单机完整部署和多实例分布式部署。

## 前置要求

- Docker Engine ≥ 24.0
- Docker Compose ≥ 2.24.0（`docker compose` 子命令）
- 可用磁盘：最小栈约 2 GB，完整栈和分布式栈约 8 GB

## 准备配置

Compose 文件从 `docker/.env` 读取环境变量。在仓库的 `deepsearch/docker` 目录下执行：

```bash
cp ../.env.example .env
# 编辑 .env，填写运行所需的配置
```

`.env.example` 中的 `BACKEND_PORT=6000` 用于本地源码启动。三种 Compose 编排都会将容器内后端端口固定为 `8000`，与 Dockerfile 的暴露端口和健康检查保持一致。

## 选择部署方式

| 场景 | Compose 文件 | 服务 |
|---|---|---|
| 本地试用 | `docker-compose.yml` | DeepSearch + SQLite + 内存 checkpointer |
| 单机完整栈 | `docker-compose.full.yml` | DeepSearch + MySQL + Milvus + etcd + MinIO |
| 多实例分布式 | `docker-compose.distributed.yml` | Nginx + DeepSearch + Redis + MySQL + Milvus + etcd + MinIO |

### 最小栈

```bash
docker compose up -d
```

该模式不启动 MySQL、Redis 或 Milvus；请勿在未配置外部 Milvus 时调用知识库接口。

### 单机完整栈

```bash
docker compose -f docker-compose.full.yml up -d
```

### 分布式栈

```bash
docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=3
```

扩缩容后需重建 Nginx，使 upstream 地址刷新：

```bash
docker compose -f docker-compose.distributed.yml up -d \
  --scale deepsearch=3 --force-recreate nginx
```

## 端口与持久化

- 业务 API 和 Telemetry 分别使用宿主端口 `8000` 和 `8089`。
- 完整栈和分布式栈的 MySQL、Redis、Milvus 和 MinIO 端口仅绑定到 `127.0.0.1`。
- 最小栈和完整栈将 `../data` 与 `../output` 挂载到容器的 `/app/data` 和 `/app/output`。
- 分布式栈使用 `deepsearch-data` 和 `deepsearch-output` 命名卷。`/app/output` 包含运行日志、调试结果、Telemetry 事件和其他生成输出。

## 生产安全

> 示例中的 MySQL `root/root`、MinIO `minioadmin/minioadmin` 以及无密码 Redis 仅适用于本地试用。生产部署必须替换弱默认凭据、为 Redis 启用认证，限制 `8000/8089` 的防火墙或安全组规则，并保持基础设施端口不对公网暴露。

## 验证与排查

```bash
curl http://localhost:8000/api/health
curl http://localhost:8089
docker compose ps
docker compose logs -f deepsearch
```

API 文档地址：`http://localhost:8000/api/docs`。
