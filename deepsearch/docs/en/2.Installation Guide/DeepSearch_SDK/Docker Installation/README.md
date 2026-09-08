# Docker installation

Docker install guides by OS:

- [Windows Installation](./Windows%20Installation.md)
- [Linux Installation](./Linux%20Installation.md)
- [macOS Installation](./macOS%20Installation.md)

## Two HTTP services in one container

DeepSearch supports two runtime modes (`search_mode` in configuration):

| Mode | `search_mode` | Service | Container port |
| ---- | ------------- | ------- | ---------------- |
| **DeepResearch** | `research` | Main backend `start_backend.py` | **8000** |
| **DeepSearch** | `search` | Telemetry `server.telemetry_event_server` | **8089** |

Knowledge-base APIs use the main backend (8000). For **DeepResearch** only, mapping **8000** is enough. For the **DeepSearch** mode (`POST /runs`, telemetry event APIs), callers must reach **8089**.

The official `docker/Dockerfile` `CMD` starts **both** processes in one container. Do not change `CMD` to start only the main backend.

**Build** (repository root):

```bash
docker build -f docker/Dockerfile -t <image-tag> .
```

**Port mapping**:

- **DeepResearch** only: `-p 8000:8000` (8089 may stay internal).
- **DeepSearch** mode from the **host**: also `-p 8089:8089`.
- Integration on a **shared Docker network**: map 8000 and use `http://<service-name>:8089` for Telemetry.

For local (non-Docker) installs, start the main backend and Telemetry in separate terminals; see the local install guides.

See [DeepSearch REST API (Telemetry)](../../../3.Developer%20Guide/API%20Reference/deepsearch_rest_api.md).

## Docker Compose one-click deployment

Besides manual `docker build` / `docker run`, use Docker Compose to bring up the multi-service stack with one command. Run the following from the `deepsearch/docker/` directory.

### Configuration file location (important)

Every compose file reads container environment variables via `env_file: - .env`, and Compose `${...}` interpolation reads `.env` from the same directory as the compose file. **These are the same file**, located in `deepsearch/docker/`:

**Important**: `.env.example` sits in the `deepsearch/` root; you must **copy it to** `deepsearch/docker/`:

```bash
cd deepsearch/docker
cp ../.env.example .env   # creates deepsearch/docker/.env; edit to fill in secrets
```

Do **not** create `.env` under `deepsearch/` root; compose won't find it.

## Three deployment modes

### 1. Minimal — quick evaluation

```bash
cd deepsearch/docker

# 1. Prepare configuration (fill in LLM / search credentials)
cp ../.env.example .env   # creates deepsearch/docker/.env; edit it

# 2. One-click startup: deepsearch only
docker compose up -d
```

| Purpose | Quick evaluation, PoC demos |
|---------|------------------------------|
| Sessions | `CHECKPOINTER_TYPE=in_memory` (lost on restart) |
| Metadata | `DB_TYPE=sqlite` (stored in the mounted `data/`) |
| Knowledge base | **Unavailable** (connect an external Milvus via `INDEX_MANAGER_TYPE=milvus` + `MILVUS_HOST=<host>` if needed) |
| Dependencies | Only `deepsearch` container |

It maps port **8000** (main backend) and **8089** (Telemetry). Data may be lost or relies on local volumes only.

### 2. Full stack — single-node production

```bash
cd deepsearch/docker
cp ../.env.example .env   # fill in LLM / search keys; DB_PASSWORD is the MySQL root password
docker compose -f docker-compose.full.yml up -d
```

| Purpose | Single-instance, feature-complete production |
|---------|----------------------------------------------|
| Sessions | `CHECKPOINTER_TYPE=persistence` (local checkpointer DB; interactive state survives restart) |
| Metadata | `DB_TYPE=mysql` |
| Knowledge base | `INDEX_MANAGER_TYPE=milvus` with in-stack Milvus (`etcd` + `minio` + `milvus`) |
| Dependencies | `mysql` / `etcd` / `minio` / `milvus` / `deepsearch` |

Each dependency ships a `healthcheck`, and `deepsearch` uses `depends_on: condition: service_healthy` so it only starts after dependencies are ready. MySQL and Milvus data persist to named volumes (`mysql-data`, `milvus-data`, etc.).

> **Note**: the in-stack `minio` is Milvus's internal object-storage dependency, **not** the application-level OBS. The `persistence` session mode needs no OBS.

### 3. Distributed — horizontal multi-instance scaling

```bash
cd deepsearch/docker
cp ../.env.example .env   # fill in LLM / search keys; DB_PASSWORD is the MySQL root password
docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=3
```

| Purpose | Multi-instance session sharing, distributed scalability |
|---------|----------------------------------------------------------|
| Sessions | `CHECKPOINTER_TYPE=redis` (all instances share session state) |
| Metadata | `DB_TYPE=mysql` (all instances use the same database) |
| Knowledge base | `INDEX_MANAGER_TYPE=milvus` with in-stack Milvus + **MinIO as OBS** (KB file sharing) |
| Dependencies | `redis` / `mysql` / `etcd` / `minio` / `milvus` / `deepsearch` |

**Key points**:

- When `CHECKPOINTER_TYPE=redis`, all instances must connect to the same MySQL (metadata) and OBS (KB files) to ensure state consistency.
- This stack uses in-stack MinIO as the OBS backend (S3-compatible). `deepsearch` accesses it via `OBS_SERVER=http://minio:9000` and other environment variables. In production, you can switch to external managed OBS (Huawei Cloud OBS / AWS S3 etc.) by changing `OBS_SERVER` and credentials.
- **MinIO bucket auto-creation**: `minio-init` one-shot container automatically creates the `${OBS_BUCKET}` (default `deepsearch-kb`) at startup; no manual `mc` commands needed.
- **Built-in nginx load balancer**: Exposes ports 8000/8089 and distributes traffic across all `deepsearch` instances using `least_conn` strategy. After scaling up/down, you must `--force-recreate nginx` to refresh upstream addresses (Docker DNS changes do not automatically trigger nginx re-resolution):
  ```bash
  docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=N --force-recreate nginx
  ```

> The difference between full stack and distributed: full stack uses `persistence` (single-node production, no Redis/OBS needed), while distributed uses `redis` (multi-instance session sharing, requires Redis + OBS).

## Security note (read before production)

Infrastructure ports bind to host `127.0.0.1` by default (MySQL 3306, Milvus 19530/9091, Redis 6379, MinIO 9000/9001 — local access only); `deepsearch` **8000/8089** are the externally-facing business entry points. In production you must:

- Change all default passwords: set `DB_PASSWORD`, `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` in `.env` (MySQL root and MinIO default to `root` / `minioadmin`)
- Restrict access to the exposed 8000/8089 with a firewall / security group
- Consider managed database / object-storage services where appropriate

## Environment Variables Reference

### Environment Variable Loading Mechanism

Docker Compose loads environment variables in the following priority order (highest to lowest):

1. **`environment` block** in the compose file (highest priority)
2. **`env_file`** specified files (`.env`)
3. **Container image `ENV` directives**

**Strategy in this orchestration**:
- Fixed configuration (e.g., `BACKEND_PORT=8000`, `HOST=0.0.0.0`) is explicitly set in the `environment` block to override defaults
- User-specific configuration (LLM keys, database passwords, etc.) is injected via `.env` file

### Backend runtime

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `BACKEND_PORT` | Main backend service port | `6000` | `8000` (Docker Compose) |
| `HOST` | Backend listen address | `127.0.0.1` | `0.0.0.0` (Docker Compose) |

### Database

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `DB_TYPE` | Database type | `sqlite` | `mysql` (full / distributed) |
| `DB_HOST` | MySQL host name | — | `mysql` (container DNS) |
| `DB_PORT` | MySQL port | — | `3306` |
| `DB_USER` | MySQL user | — | `root` |
| `DB_PASSWORD` | MySQL password (also MySQL root password) | — | Set in `.env` |
| `DEEPSEARCH_DB_NAME` | DeepSearch database name | `openjiuwen_deepsearch` | — |

### Session checkpointer

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `CHECKPOINTER_TYPE` | Session checkpoint storage | `in_memory` | `persistence` (full) / `redis` (distributed) |
| `CHECKPOINTER_DB_TYPE` | persistence backend | `sqlite` | `sqlite` |
| `CHECKPOINTER_DB_PATH` | persistence DB path | `data/databases/checkpointer.db` | — |
| `REDIS_URL` | Redis connection URL (distributed only) | — | `redis://redis:6379` |

### Milvus vector store

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `INDEX_MANAGER_TYPE` | Vector index manager | `milvus` | `milvus` (full / distributed) |
| `MILVUS_HOST` | Milvus host name | — | `milvus` (container DNS) |
| `MILVUS_PORT` | Milvus gRPC port | — | `19530` |
| `MILVUS_TOKEN` | Milvus token (optional on 2.4+) | — | empty string or token |

### Object storage (distributed only)

When using `CHECKPOINTER_TYPE=redis` for distributed deployments, all instances must share the same knowledge base file storage. The distributed stack uses in-stack MinIO as the OBS backend by default.

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `OBS_SERVER` | Object storage server URL | — | `http://minio:9000` (in-stack) |
| `OBS_REGION` | Object storage region | — | `us-east-1` |
| `OBS_BUCKET` | KB file bucket name | `deepsearch-kb` | — |
| `OBS_ACCESS_KEY_ID` | OBS access key ID | — | MinIO username (default `minioadmin`) |
| `OBS_SECRET_ACCESS_KEY` | OBS secret key | — | MinIO password (default `minioadmin`) |

**Note**: Minimal and full stacks need no OBS (SQLite + in_memory / persistence, files stay local only).

### Other settings

See `.env.example` and `openjiuwen_deepsearch/config/config.py`.

## Troubleshooting

### Port conflicts

If `docker compose up -d` reports "address already in use", a port is taken. Solutions:

1. **Check which process uses the port**:
   ```bash
   # Linux/macOS
   lsof -i :6379   # Redis port
   lsof -i :3306   # MySQL port
   lsof -i :19530  # Milvus port

   # Windows
   netstat -ano | findstr :6379
   ```

2. **Modify the port mapping** (temporary):
   ```bash
   # Edit the compose file, change 127.0.0.1:6379:6379 to 127.0.0.1:16379:6379
   docker compose -f <file> up -d
   ```

3. **Clean up stopped containers**:
   ```bash
   docker compose down
   docker volume prune  # WARNING: deletes unused volumes
   ```

### Volume permission errors

If containers fail with "permission denied":

1. **Check volume ownership**:
   ```bash
   docker volume inspect mysql-data
   docker volume ls
   ```

2. **Reinitialize with `docker compose down -v`**:
   ```bash
   docker compose down -v  # -v removes and rebuilds volumes
   docker compose up -d
   ```

3. **Fix file ownership on Linux** (for local mount paths):
   ```bash
   sudo chown -R 1000:1000 ./output ./data
   ```

### Slow or unresponsive startup

1. **Check logs**:
   ```bash
   docker compose logs -f deepsearch    # deepsearch container
   docker compose logs -f mysql          # MySQL logs
   docker compose logs -f milvus         # Milvus logs
   ```

2. **Test network connectivity** (MySQL and Milvus have startup delays with `start_period`):
   ```bash
   docker compose exec deepsearch ping mysql
   docker compose exec deepsearch ping milvus
   ```

3. **Increase healthcheck timeouts** (if network is slow):
   Edit the compose file and increase `timeout` and `retries` values.

### Distributed mode: data persistence

In the distributed stack, the `deepsearch` service's `output/` and `data/` directories use **named volumes** for persistence:

```yaml
volumes:
  deepsearch-output:   # generated reports, logs, and other temporary outputs
  deepsearch-data:     # local SQLite databases, caches, etc.
```

**Why not use bind mounts (mounting to host directories)?**

- **Multi-instance concurrent write risks**: When running multiple `deepsearch` instances, bind mounts cause all instances to share the same host directory. Concurrent writes to the same file (e.g., SQLite databases, log files) result in:
  - **File lock contention**: databases like SQLite that don't support multi-process concurrency will report "database is locked" errors
  - **Data corruption/loss**: multiple instances writing to the same file simultaneously can cause data corruption or loss
  - **File system performance degradation**: frequent file lock operations significantly degrade I/O performance

- **Container isolation**: named volumes give each container its own file system namespace, avoiding path conflicts and permission issues

**Data sharing strategy in distributed mode**:

- **Session state** → Redis (shared by all instances, supports concurrent access)
- **Knowledge base files** → MinIO OBS (S3-compatible object storage, supports concurrent read/write)
- **Local temporary data** → container named volumes (each instance has its own, persists across restarts)

To inspect or back up volume data:

```bash
# View volume storage location
docker volume inspect deepsearch-output
docker volume inspect deepsearch-data

# Back up volume data
docker run --rm -v deepsearch-data:/data -v $(pwd):/backup alpine tar czf /backup/deepsearch-data-backup.tar.gz -C /data .
```

### Distributed mode: MinIO bucket not found

On first startup of the distributed stack, MinIO has no pre-created bucket. If `deepsearch` logs show `NoSuchBucket`, create it manually:

```bash
# Method 1: via MinIO console (browser: http://127.0.0.1:9001, login: minioadmin/minioadmin)
# Method 2: via mc CLI
docker compose exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker compose exec minio mc mb local/deepsearch-kb
```

### Environment variables not taking effect

1. **Verify `.env` file exists at the correct path**:
   ```bash
   ls -la .env   # should sit in deepsearch/docker/, same dir as the compose files
   ```

2. **Check `.env` encoding** (avoid BOM or non-UTF-8):
   ```bash
   file .env  # should be UTF-8 Unicode text
   ```

3. **Verify variable injection**:
   ```bash
   docker compose config | grep BACKEND_PORT  # check final config
   ```
