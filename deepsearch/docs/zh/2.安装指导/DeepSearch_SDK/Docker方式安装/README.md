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

Telemetry API 说明见 [DeepSearch REST API（Telemetry）](../../../3.开发指南/API文档/deepsearch_rest_api.md)。

## Docker Compose 一键部署

除手动 `docker build` / `docker run` 外，也可用 Docker Compose 一键拉起多服务。根据部署需求选择合适的编排。以下命令均在 `deepsearch/docker/` 目录下执行。

### 配置文件位置（重要）

所有编排文件通过 `env_file: - .env` 读取容器环境变量，Compose 的 `${...}` 插值则读取 compose 文件同目录下的 `.env`。**两者是同一个文件**：均在 `deepsearch/docker/` 目录下。

**重要**：`.env.example` 位于 `deepsearch/` 根目录，需**复制到** `deepsearch/docker/` 下：

```bash
cd deepsearch/docker
cp ../.env.example .env   # 生成 deepsearch/docker/.env，再编辑填入密钥
```

**不要**在 `deepsearch/` 根目录创建 `.env`，否则 compose 读不到环境变量。

## 三种部署模式

### 1. 最小栈 — 快速体验

```bash
cd deepsearch/docker

# 1. 准备配置（填入 LLM / 搜索等密钥）
cp ../.env.example .env   # 生成 deepsearch/docker/.env，再编辑

# 2. 一键启动 deepsearch
docker compose up -d
```

| 定位 | 快速体验、PoC 演示 |
|------|-------------------|
| 会话 | `CHECKPOINTER_TYPE=in_memory`（重启丢会话） |
| 元数据 | `DB_TYPE=sqlite`（落在挂载的 `data/`） |
| 知识库 | **不可用**（需外接 Milvus 并设 `INDEX_MANAGER_TYPE=milvus`、`MILVUS_HOST=<地址>`） |
| 依赖 | 仅 `deepsearch` 一个容器 |

映射端口 **8000**（主后端）与 **8089**（Telemetry）。数据可丢或只靠本地卷。

### 2. 完整栈 — 单机生产

```bash
cd deepsearch/docker
cp ../.env.example .env   # 填入 LLM / 搜索密钥；DB_PASSWORD 即 MySQL root 密码
docker compose -f docker-compose.full.yml up -d
```

| 定位 | 单实例、能力完整的生产环境 |
|------|---------------------------|
| 会话 | `CHECKPOINTER_TYPE=persistence`（本地 checkpointer DB，重启可恢复交互状态） |
| 元数据 | `DB_TYPE=mysql` |
| 知识库 | `INDEX_MANAGER_TYPE=milvus` + 栈内 Milvus（`etcd` + `minio` + `milvus`） |
| 依赖 | `mysql` / `etcd` / `minio` / `milvus` / `deepsearch` |

编排内为每个依赖服务配置了 `healthcheck`，`deepsearch` 通过 `depends_on: condition: service_healthy` 保证依赖就绪后才启动；MySQL、Milvus 等数据分别持久化到命名卷（`mysql-data`、`milvus-data` 等）。

> **注意**：栈内的 `minio` 是 Milvus 的内部依赖（对象存储文件），**不是**面向应用的 OBS。`persistence` 会话无需 OBS。

### 3. 分布式 — 多实例水平扩展

```bash
cd deepsearch/docker
cp ../.env.example .env   # 填入 LLM / 搜索密钥；DB_PASSWORD 即 MySQL root 密码
docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=3
```

| 定位 | 多实例会话共享、分布式可扩展 |
|------|------------------------------|
| 会话 | `CHECKPOINTER_TYPE=redis`（所有实例共享会话状态） |
| 元数据 | `DB_TYPE=mysql`（所有实例共用同一库） |
| 知识库 | `INDEX_MANAGER_TYPE=milvus` + 栈内 Milvus + **MinIO 作 OBS**（KB 文件共享） |
| 依赖 | `redis` / `mysql` / `etcd` / `minio` / `milvus` / `deepsearch` |

**关键说明**：

- `CHECKPOINTER_TYPE=redis` 时，所有实例必须连接同一 MySQL（元数据）和 OBS（KB 文件），才能保证状态一致。
- 本编排使用栈内 MinIO 作为 OBS 后端（S3 兼容），`minio-init` 容器自动创建 bucket（`deepsearch-kb`），`deepsearch` 通过 `OBS_SERVER=http://minio:9000` 等环境变量访问。生产环境可改用外部托管 OBS（华为云 OBS / AWS S3 等），修改 `OBS_SERVER` 和凭据即可。
- **负载均衡**：内置 `nginx` 反向代理，`least_conn` 策略将宿主机 `8000/8089` 流量分发到 N 个 `deepsearch` 实例。外部只需访问宿主机端口。扩缩容后须同时 `--force-recreate nginx` 以刷新 upstream 地址（Docker DNS 变更不会自动触发 nginx 重新解析）：
  ```bash
  docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=N --force-recreate nginx
  ```

> 完整栈与分布式的区别：完整栈是 `persistence`（单机生产，无需 Redis/OBS），分布式是 `redis`（多实例会话共享，必须 Redis + OBS）。

## 安全提示（生产环境必读）

编排默认端口均绑定宿主机 `127.0.0.1`（MySQL 3306、Milvus 19530/9091、Redis 6379、MinIO 9000/9001），仅供本机访问；`deepsearch` 的 **8000/8089** 对外提供业务入口。生产环境务必：

- 修改所有默认密码：`.env` 中设置 `DB_PASSWORD`、`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`（MySQL root、MinIO 默认均为 `root` / `minioadmin`）
- 用防火墙或安全组限制对外暴露的 8000/8089 端口访问
- 按需改用外部托管数据库/对象存储服务

## 环境变量配置详表

### 环境变量加载机制

Docker Compose 按以下优先级加载环境变量（从高到低）：

1. **compose 文件中的 `environment` 块**（优先级最高）
2. **`env_file` 指定的文件** (`.env`)
3. **容器镜像的 `ENV` 指令**

**本编排策略**:
- 固定配置（如 `BACKEND_PORT=8000`、`HOST=0.0.0.0`）在 `environment` 中强制覆盖
- 用户自定义配置（LLM 密钥、数据库密码等）通过 `.env` 文件注入

### 后端运行时配置

| 环境变量 | 说明 | 默认值 | 示例 |
|----------|------|--------|------|
| `BACKEND_PORT` | 主后端服务监听端口 | `6000` | `8000`（Docker Compose 中） |
| `HOST` | 后端监听地址 | `127.0.0.1` | `0.0.0.0`（Docker Compose 中） |

### 数据库配置

| 环境变量 | 说明 | 默认值 | 示例 |
|----------|------|--------|------|
| `DB_TYPE` | 数据库类型 | `sqlite` | `mysql`（完整栈 / 分布式） |
| `DB_HOST` | MySQL 服务器主机名 | — | `mysql`（容器内 DNS） |
| `DB_PORT` | MySQL 服务器端口 | — | `3306` |
| `DB_USER` | MySQL 用户名 | — | `root` |
| `DB_PASSWORD` | MySQL 密码（同时作为 MySQL root 密码） | — | 在 `.env` 中设置 |
| `DEEPSEARCH_DB_NAME` | DeepSearch 数据库名 | `openjiuwen_deepsearch` | — |

### 会话检查点（Checkpointer）配置

| 环境变量 | 说明 | 默认值 | 示例 |
|----------|------|--------|------|
| `CHECKPOINTER_TYPE` | 会话检查点存储类型 | `in_memory` | `persistence`（完整栈） / `redis`（分布式） |
| `CHECKPOINTER_DB_TYPE` | persistence 模式的存储后端 | `sqlite` | `sqlite` |
| `CHECKPOINTER_DB_PATH` | persistence 模式的 DB 路径 | `data/databases/checkpointer.db` | — |
| `REDIS_URL` | Redis 连接 URL（仅分布式需要） | — | `redis://redis:6379` |

### Milvus 向量库配置

| 环境变量 | 说明 | 默认值 | 示例 |
|----------|------|--------|------|
| `INDEX_MANAGER_TYPE` | 向量索引管理器类型 | `milvus` | `milvus`（完整栈 / 分布式） |
| `MILVUS_HOST` | Milvus 服务器主机名 | — | `milvus`（容器内 DNS） |
| `MILVUS_PORT` | Milvus gRPC 端口 | — | `19530` |
| `MILVUS_TOKEN` | Milvus 访问令牌（Milvus 2.4+ 可选） | — | 空字符串或具体令牌 |

### 对象存储配置（仅分布式需要）

当使用 `CHECKPOINTER_TYPE=redis` 进行分布式部署时，所有实例必须共享同一个知识库文件存储。分布式编排默认使用栈内 MinIO 作为对象存储后端。

| 环境变量 | 说明 | 默认值 | 示例 |
|----------|------|--------|------|
| `OBS_SERVER` | 对象存储服务器 URL | — | `http://minio:9000`（分布式栈内） |
| `OBS_REGION` | 对象存储区域 | — | `us-east-1` |
| `OBS_BUCKET` | 知识库文件存储的 bucket 名称 | `deepsearch-kb` | — |
| `OBS_ACCESS_KEY_ID` | 对象存储访问密钥 ID | — | MinIO 用户名（默认 `minioadmin`） |
| `OBS_SECRET_ACCESS_KEY` | 对象存储访问密钥 | — | MinIO 密码（默认 `minioadmin`） |

**注意**：最小栈与完整栈无需 OBS（SQLite + in_memory / persistence，文件仅存本地）。

### 其他配置

详见 `.env.example` 和代码 `openjiuwen_deepsearch/config/config.py`。

## 故障排查

### 端口冲突

如果 `docker compose up -d` 报错「address already in use」，说明本地端口被占用。解决方案：

1. **查看占用的进程**：
   ```bash
   # Linux/Mac
   lsof -i :6379   # 查看 Redis 端口占用
   lsof -i :3306   # 查看 MySQL 端口占用
   lsof -i :19530  # 查看 Milvus 端口占用

   # Windows
   netstat -ano | findstr :6379
   ```

2. **修改 Docker Compose 端口映射**（临时方案）：
   ```bash
   # 编辑对应 compose 文件，将 127.0.0.1:6379:6379 改为 127.0.0.1:16379:6379
   docker compose -f <文件名> up -d
   ```

3. **清理已停止的容器**：
   ```bash
   docker compose down
   docker volume prune  # 警告：删除未使用的数据卷
   ```

### 数据卷权限问题

如果容器启动后报「permission denied」错误：

1. **检查数据卷所有者**：
   ```bash
   docker volume inspect mysql-data
   docker volume ls
   ```

2. **使用 `docker compose down` 重新初始化**：
   ```bash
   docker compose down -v  # -v 删除所有数据卷并重建
   docker compose up -d
   ```

3. **检查 Linux 宿主机的文件权限**（如果使用本地路径挂载）：
   ```bash
   sudo chown -R 1000:1000 ./output ./data
   ```

### 容器启动缓慢或无响应

1. **查看日志**：
   ```bash
   docker compose logs -f deepsearch    # 查看 deepsearch 容器日志
   docker compose logs -f mysql          # 查看 MySQL 日志
   docker compose logs -f milvus         # 查看 Milvus 日志
   ```

2. **检查网络连通性**（MySQL、Milvus 启动较慢，有 `start_period` 延时容限）：
   ```bash
   docker compose exec deepsearch ping mysql
   docker compose exec deepsearch ping milvus
   ```

3. **增加 healthcheck 超时时间**（如果网络较慢）：
   编辑对应 compose 文件，增大 `timeout` 和 `retries` 值。

### 分布式模式数据持久化

分布式栈中，`deepsearch` 服务的 `output/` 和 `data/` 目录使用 **命名卷（named volumes）** 持久化：

```yaml
volumes:
  deepsearch-output:   # 生成报告、日志等临时输出
  deepsearch-data:     # 本地 SQLite 数据库、缓存等
```

**为什么不用 bind mount（挂载到宿主机目录）？**

- **多实例并发写风险**：当运行多个 `deepsearch` 实例时，bind mount 会让所有实例共享同一个宿主机目录。并发写入同一文件（如 SQLite 数据库、日志文件）会导致：
  - **文件锁竞争**：SQLite 等不支持多进程并发的数据库会报 "database is locked" 错误
  - **数据覆盖/丢失**：多个实例同时写入同一文件时，后写入的内容会覆盖先写入的内容
  - **文件系统性能下降**：频繁的文件锁操作会显著降低 I/O 性能

- **容器隔离性**：命名卷让每个容器拥有独立的文件系统命名空间，避免路径冲突和权限问题

**分布式场景的数据共享策略**：

- **会话状态** → Redis（所有实例共享，支持并发访问）
- **知识库文件** → MinIO OBS（S3 兼容对象存储，支持并发读写）
- **本地临时数据** → 容器内命名卷（每个实例独立，重启后保留）

如需查看或备份卷数据：

```bash
# 查看卷的存储位置
docker volume inspect deepsearch-output
docker volume inspect deepsearch-data

# 备份卷数据
docker run --rm -v deepsearch-data:/data -v $(pwd):/backup alpine tar czf /backup/deepsearch-data-backup.tar.gz -C /data .
```

分布式模式首次启动时，MinIO 内没有预创建 bucket。如果 `deepsearch` 日志报 `NoSuchBucket`，需手动创建：

```bash
# 方法 1：通过 MinIO 控制台创建（浏览器访问 http://127.0.0.1:9001，用 minioadmin/minioadmin 登录）
# 方法 2：通过 mc 命令行工具
docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker compose exec minio mc mb local/deepsearch-kb
```

### 环境变量未生效

1. **确认 .env 文件存在且路径正确**：
   ```bash
   ls -la .env   # 应在 deepsearch/docker/ 目录下，与 compose 文件同目录
   ```

2. **检查 .env 文件编码**（避免 BOM 或非 UTF-8 编码）：
   ```bash
   file .env  # 应为 UTF-8 Unicode text
   ```

3. **验证环境变量注入**：
   ```bash
   docker compose config | grep BACKEND_PORT  # 查看最终生效的配置
   ```
