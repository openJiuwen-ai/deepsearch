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

See [DeepSearch REST API (Telemetry)](../../../4.Developer%20Guide/API%20Reference/deepsearch_rest_api.md).

## Docker Compose one-click deployment

Besides manual `docker build` / `docker run`, use Docker Compose to bring up the multi-service stack (backend + Redis + optional MySQL/Milvus) with one command. Run the following from the `deepsearch/docker/` directory.

### Minimal (default)

```bash
cd deepsearch/docker

# 1. Prepare configuration (fill in LLM / search credentials)
cp ../.env.example ./.env   # then edit .env

# 2. One-click startup: redis + deepsearch
docker compose up -d
```

The minimal stack only includes `redis` + `deepsearch`, covering DeepResearch and DeepSearch with default `sqlite` + `in_memory` settings. It maps port **8000** (main backend) and **8089** (Telemetry).

### Full stack (MySQL + Milvus vector knowledge base)

```bash
cd deepsearch/docker
docker compose -f docker-compose.full.yml up -d
```

The full stack additionally starts:

- **MySQL** (metadata / sessions) + **Redis** (session state)
- **etcd + minio + Milvus** (vector knowledge base; Milvus standalone requires etcd and minio)

Each dependency ships a `healthcheck`, and `deepsearch` uses `depends_on: condition: service_healthy` so it only starts after dependencies are ready. MySQL and Milvus data persist to named volumes (`mysql-data`, `milvus-data`, etc.).

> The full stack has `docker-compose.full.yml` inject environment overrides for the local defaults in `.env`: `DB_TYPE=mysql`, `DB_HOST=mysql`, `DB_PORT=3306`, `CHECKPOINTER_TYPE=redis`, `REDIS_URL=redis://redis:6379`, `INDEX_MANAGER_TYPE=milvus`, `MILVUS_HOST=milvus`. `DB_PASSWORD` doubles as the MySQL root password (default `root`; change it in production).
