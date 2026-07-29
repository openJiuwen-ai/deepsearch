**Read this in:** [简体中文](./README.md) | English

# Docker Compose One-Click Deployment

Orchestrate the openJiuwen DeepSearch backend together with its dependency services (MySQL / Redis / Milvus) via Docker Compose, removing the need to start and wire up each service manually.

## Prerequisites

- Docker Engine ≥ 24.0
- Docker Compose v2 (the `docker compose` subcommand)
- Free disk: ~2GB for minimal, ~8GB for distributed (includes Milvus + MinIO images)

## Quick Start (Minimal Stack)

The minimal stack launches only the backend container with SQLite and an in-memory checkpointer, requiring no external database:

```bash
# 1. Prepare environment variables (infrastructure: DB / Milvus / Redis / SSL, etc.)
cp .env.example .env
#    Edit .env and set DB_TYPE / DB_HOST / CHECKPOINTER_TYPE, etc. for the chosen tier.
#    (LLM and search-source keys are NOT in .env; after startup, configure them via the
#     frontend settings page or management API, where they are encrypted and stored in DB.)

# 2. Start (detached)
docker compose -f docker/docker-compose.yml up -d

# 3. Verify
curl http://localhost:8000/api/health   # backend health check
curl http://localhost:8089              # telemetry endpoint
# API docs: http://localhost:8000/api/docs
```

Default exposed ports:

| Service | Container port | Default host port | Description |
|---------|----------------|-------------------|-------------|
| Backend API | 8000 | 8000 | DeepResearch / DeepSearch endpoints, `/api/docs` Swagger |
| Telemetry | 8089 | 8089 | Event endpoint for `search_mode=search` |

To change host ports, set in `.env`:

```
BACKEND_PUBLISH_PORT=18000
TELEMETRY_PUBLISH_PORT=18089
```

## Three Deployment Tiers

Select the dependency footprint via `--profile`. The backend container always starts; profiles only control extra services.

### 1. minimal (default)

```bash
docker compose -f docker/docker-compose.yml up -d
```

Use for: personal trials, single instance, no persistent metadata.

Key `.env` settings:

```
DB_TYPE=sqlite
SQLITE_DB_PATH=data/databases
CHECKPOINTER_TYPE=in_memory
INDEX_MANAGER_TYPE=milvus      # may stay milvus but unused when tool_map=search
```

### 2. mysql — Persistent metadata, still single-instance

```bash
docker compose -f docker/docker-compose.yml --profile mysql up -d
```

Use for: persisting conversation/report metadata while running a single instance.

Key `.env` settings (compose resolves the mysql container name as `mysql`):

```
DB_TYPE=mysql
DB_HOST=mysql
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DEEPSEARCH_DB_NAME=openjiuwen_deepsearch
CHECKPOINTER_TYPE=in_memory       # or persistence
```

Optional: `MYSQL_PUBLISH_PORT=3307` to change the host mapping.

### 3. distributed — Multi-instance + knowledge base retrieval

```bash
docker compose -f docker/docker-compose.yml --profile distributed up -d
```

Use for: multi-instance distributed deployment, or local knowledge-base vector retrieval (`tool_map=retrieve`).

Services started: deepsearch + mysql + redis + milvus (with etcd + minio dependencies).

Key `.env` settings:

```
DB_TYPE=mysql
DB_HOST=mysql
DB_PORT=3306
DB_USER=root
DB_PASSWORD=root
DEEPSEARCH_DB_NAME=openjiuwen_deepsearch

# Distributed checkpointer requires redis + mysql
CHECKPOINTER_TYPE=redis
REDIS_URL=redis://redis:6379
REDIS_CLUSTER_MODE=false

# Knowledge-base vector retrieval
INDEX_MANAGER_TYPE=milvus
MILVUS_HOST=milvus
MILVUS_PORT=19530
MILVUS_TOKEN=

# ⚠️ In distributed mode, knowledge-base documents must be written to OBS.
# All OBS_* fields must be configured, otherwise the service will not start.
OBS_ACCESS_KEY_ID=...
OBS_SECRET_ACCESS_KEY=...
OBS_SERVER=...
OBS_REGION=...
OBS_BUCKET=...
```

Optional host port mappings:

```
MYSQL_PUBLISH_PORT=3307
REDIS_PUBLISH_PORT=6380
MILVUS_PUBLISH_PORT=19531
MINIO_PUBLISH_PORT=9002
```

## Service Orchestration Reference

| Service | Image | Profile | Container ports | Purpose |
|---------|-------|---------|-----------------|---------|
| deepsearch | built from this repo | always | 8000 / 8089 | FastAPI backend + telemetry |
| mysql | mysql:8.0 | mysql / distributed | 3306 | Metadata persistence |
| redis | redis:7-alpine | distributed | 6379 | Distributed checkpointer |
| milvus | milvusdb/milvus:v2.4.17 | distributed | 19530 | Vector retrieval |
| etcd | quay.io/coreos/etcd:v3.5.16 | distributed | 2379 | Milvus metadata store |
| minio | minio/minio | distributed | 9000 / 9001 | Milvus object storage |

## Data Persistence

All persistent data lives in named volumes and survives `docker compose down`; use `docker compose down -v` for a full cleanup:

| Volume | Mount point | Content |
|--------|-------------|---------|
| deepsearch-data | /app/data | SQLite databases, local knowledge-base index |
| deepsearch-logs | /app/output | Runtime logs, generated reports |
| mysql-data | /var/lib/mysql | MySQL data |
| redis-data | /data | Redis AOF |
| milvus-data | /var/lib/milvus | Milvus vector data |
| etcd-data | /etcd | Milvus metadata |
| minio-data | /minio_data | MinIO objects |

## Common Commands

```bash
# Tail logs
docker compose -f docker/docker-compose.yml logs -f deepsearch

# Restart the backend
docker compose -f docker/docker-compose.yml restart deepsearch

# Rebuild only the backend image (after code changes)
docker compose -f docker/docker-compose.yml build deepsearch && \
docker compose -f docker/docker-compose.yml up -d deepsearch

# Stop (keep data)
docker compose -f docker/docker-compose.yml down

# Stop and delete all data volumes
docker compose -f docker/docker-compose.yml down -v

# Inspect service health
docker compose -f docker/docker-compose.yml ps
```

## Build Acceleration Behind Slow Networks

`docker/Dockerfile` accepts `INDEX_URL` and `APT_MIRROR` build args. For users behind slow links, pick a closer mirror:

```bash
docker compose -f docker/docker-compose.yml build \
  --build-arg INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn \
  deepsearch
```

The example above uses Tsinghua mirrors; substitute any mirror reachable from your build host.

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| Backend container health check fails | `docker compose logs deepsearch` for startup errors; common causes are incorrect DB / Milvus settings in `.env`, or `SERVICE_MODE=product` without `SERVER_AES_MASTER_KEY` |
| `distributed` won't start | Confirm all `OBS_*` fields in `.env` are filled — distributed mode requires OBS for knowledge-base files |
| Milvus health check times out | Milvus is slow to start on first run (90s+); `start_period` is already relaxed. If it still fails, inspect `docker compose logs milvus` |
| Port already in use | Override the host mapping with the `*_PUBLISH_PORT` environment variables |
| Backend cannot reach mysql/redis | Confirm `.env` uses **container service names** (`DB_HOST=mysql`, `REDIS_URL=redis://redis:6379`), not `localhost` |

## Relationship With the Existing Dockerfile

`docker/Dockerfile` is unchanged; compose reuses it to build the backend image. Compose only adds an orchestration layer — it does not modify backend build logic or touch any business code.
