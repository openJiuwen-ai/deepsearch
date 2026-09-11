**Read this in:** [简体中文](./README.md) | English

# Docker Compose Deployment

This directory provides three independent Compose configurations for local evaluation, a complete single-host deployment, and a multi-instance distributed deployment.

## Prerequisites

- Docker Engine >= 24.0
- Docker Compose >= 2.24.0 (the `docker compose` subcommand)
- Free disk: approximately 2 GB for the minimal stack and 8 GB for the full or distributed stack

## Prepare the Configuration

The Compose files read environment variables from `docker/.env`. From the repository's `deepsearch/docker` directory, run:

```bash
cp ../.env.example .env
# Edit .env and provide the settings required by your deployment.
```

`BACKEND_PORT=6000` in `.env.example` is intended for local source execution. All three Compose configurations explicitly use container port `8000`, matching the Dockerfile exposure and health check.

## Choose a Deployment

| Scenario | Compose file | Services |
|---|---|---|
| Local evaluation | `docker-compose.yml` | DeepSearch + SQLite + in-memory checkpointer |
| Complete single-host stack | `docker-compose.full.yml` | DeepSearch + MySQL + Milvus + etcd + MinIO |
| Multi-instance distributed stack | `docker-compose.distributed.yml` | Nginx + DeepSearch + Redis + MySQL + Milvus + etcd + MinIO |

### Minimal Stack

```bash
docker compose up -d
```

This mode does not start MySQL, Redis, or Milvus. Do not call knowledge-base endpoints unless an external Milvus instance is configured.

### Complete Single-Host Stack

```bash
docker compose -f docker-compose.full.yml up -d
```

### Distributed Stack

```bash
docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=3
```

After scaling, recreate Nginx so its upstream addresses are refreshed:

```bash
docker compose -f docker-compose.distributed.yml up -d \
  --scale deepsearch=3 --force-recreate nginx
```

## Ports and Persistence

- The API and telemetry endpoints use host ports `8000` and `8089` respectively.
- The full and distributed stacks bind MySQL, Redis, Milvus, and MinIO ports to `127.0.0.1` only.
- The minimal and full stacks mount `../data` and `../output` at `/app/data` and `/app/output`.
- The distributed stack uses the `deepsearch-data` and `deepsearch-output` named volumes. `/app/output` contains runtime logs, debug results, telemetry events, and other generated output.

## Production Security

> The example MySQL `root/root` and MinIO `minioadmin/minioadmin` credentials, and Redis without a password, are for local evaluation only. Production deployments must replace weak default credentials, enable Redis authentication, restrict `8000/8089` with firewall or security-group rules, and keep infrastructure ports off the public network.

## Verify and Troubleshoot

```bash
curl http://localhost:8000/api/health
curl http://localhost:8089
docker compose ps
docker compose logs -f deepsearch
```

API documentation is available at `http://localhost:8000/api/docs`.
