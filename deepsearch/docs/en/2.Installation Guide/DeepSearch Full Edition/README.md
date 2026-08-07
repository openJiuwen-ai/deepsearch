This guide covers installing the **DeepSearch Full Edition**, which has two parts:

- **Studio**: Web UI and backend management services.
- **DeepSearch SDK**: DeepSearch SDK backend service.

Two installation approaches:

* **Docker**: Best for quick deployment and trying the product; dependencies are containerized.
* **Local install**: Best for developers, contributors, or custom builds; you install dependencies manually for debugging and changes.

# Docker installation

OS-specific guides to start Studio and DeepSearch SDK together:

* [Windows Installation](./Windows%20Installation.md)
* [Linux Installation](./Linux%20Installation.md)
* [macOS Installation](./macOS%20Installation.md)

# Local manual installation

Manual full-edition setup requires installing dependencies, fetching source, and installing both Studio and DeepSearch SDK.

> **Note**:
> - You must install both Studio and DeepSearch SDK.
> - If you only need the DeepSearch service, optional Studio parts can be skipped.
> - Keep these aligned across both projects’ `.env` files: (1) Studio `DEEPSEARCH_AGENT_HOST` = DeepSearch `HOST`; (2) Studio `DEEPSEARCH_AGENT_PORT` = DeepSearch `BACKEND_PORT`.

Manual install guides by OS:

- **Windows**
  - [Studio](https://gitcode.com/openJiuwen/agent-studio/blob/v0.1.8/docs/zh/2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC/%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85/Windows%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85.md): see section “Method 2: Full manual installation”.
  - [DeepSearch SDK](../DeepSearch_SDK/Local%20Installation/Windows%20Installation.md): see section “Method 2: Full manual installation”.
- **Linux**
  - [Studio](https://gitcode.com/openJiuwen/agent-studio/blob/v0.1.8/docs/zh/2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC/%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85/Linux%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85.md): see section “Method 2: Full manual installation”.
  - [DeepSearch SDK](../DeepSearch_SDK/Local%20Installation/Linux%20Installation.md): see section “Method 2: Full manual installation”.
- **macOS**
  - [Studio](https://gitcode.com/openJiuwen/agent-studio/blob/v0.1.8/docs/zh/2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC/%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85/MacOS%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85.md): see section “Method 2: Full manual installation”.
  - [DeepSearch SDK](../DeepSearch_SDK/Local%20Installation/macOS%20Installation.md): see section “Method 2: Full manual installation”.
