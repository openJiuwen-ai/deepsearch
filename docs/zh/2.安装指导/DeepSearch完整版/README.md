本指南介绍安装 DeepSearch 完整版。DeepSearch 完整版包含两个部分：
- **Studio**：提供前端 UI 服务和后端管理服务；
- **DeepSearch SDK**：提供 DeepSearch SDK 后端服务。

为帮助您快速上手，社区提供了如下两种安装方式：
* **Docker方式安装**：推荐给需要快速部署、快速体验的用户，社区已将所有依赖打包到容器中。
* **本地安装**：推荐给开发者、贡献者或需要代码定制的用户，需手动设置依赖，可控制开发环境以便调试和修改。

# Docker 方式安装
社区提供了以下 3 种操作系统的安装指南，可快速同时启动 Studio 和 DeepSearch SDK：
* [Windows系统安装](./Windows系统安装.md)
* [Linux系统安装](./Linux系统安装.md)
* [MacOS系统安装](./MacOS系统安装.md)

# 本地手动安装

手动安装DeepSearch完整版，需要手动完成所需依赖安装、源码获取、Studio和DeepSearch SDK安装等步骤。
> **注意**：
> - 本地手动安装需要同时安装 Studio 和 DeepSearch SDK；
> - 如果仅使用 DeepSearch 服务，Studio 中的可选部分可直接跳过；
> - 请确保两个项目 .env 中的两个参数保持一致：1）Studio 的 DEEPSEARCH_AGENT_HOST 与 DeepSearch 的 HOST；2）Studio 的 DEEPSEARCH_AGENT_PORT 与 DeepSearch 的 BACKEND_PORT。

社区提供了以下 3 种操作系统的手动安装：
- **Windows 系统安装**
  - [Studio](https://gitcode.com/openJiuwen/agent-studio/blob/v0.1.4/docs/zh/2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC/%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85/Windows%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85.md)：参考章节《方法二：全部手动安装》
  - [DeepSearch SDK](./DeepSearch_SDK/本地安装/Windows系统安装.md)：参考章节《方法二：全部手动安装》
- **Linux 系统安装**
  - [Studio](https://gitcode.com/openJiuwen/agent-studio/blob/v0.1.4/docs/zh/2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC/%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85/Linux%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85.md)：参考章节《方法二：全部手动安装》
  - [DeepSearch SDK](./DeepSearch_SDK/本地安装/Linux系统安装.md)：参考章节《方法二：全部手动安装》
- **MacOS 系统安装**
  - [Studio](https://gitcode.com/openJiuwen/agent-studio/blob/v0.1.4/docs/zh/2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC/%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85/MacOS%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85.md)：参考章节《方法二：全部手动安装》
  - [DeepSearch SDK](./DeepSearch_SDK/本地安装/MacOS系统安装.md)：参考章节《方法二：全部手动安装》
