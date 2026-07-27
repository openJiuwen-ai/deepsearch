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
