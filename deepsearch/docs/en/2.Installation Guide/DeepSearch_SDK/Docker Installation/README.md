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
