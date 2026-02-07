本指南介绍安装Deepsearch完整版。DeepSearch完整版包含2个部分: 
- **Studio**: 提供前端ui服务和后端管理服务；
- **DeepSearch SDK**: 提供DeepSearch sdk后端服务。

为帮助您快速上手，社区提供了如下两种安装方式：
* ​**Docker方式安装**​：推荐给需要快速部署、快速体验的用户，社区已将所有依赖打包到容器中。
* **​本地安装**​：推荐给开发者、贡献者或需要代码定制的用户，需手动设置依赖，可控制开发环境以便调试和修改。

# Docker方式安装
社区提供了以下3种操作系统的安装指南, 可快速同时启动Studio和DeepSearch sdk：
* [Windows系统安装](https://www.openjiuwen.com/docs-page?version=studio-v0.1.3-zh-Goo4vjOp&path=2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC%2FDocker%E6%96%B9%E5%BC%8F%E5%AE%89%E8%A3%85%2FWindows%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85)
* [Linux系统安装](https://www.openjiuwen.com/docs-page?version=studio-v0.1.3-zh-Goo4vjOp&path=2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC%2FDocker%E6%96%B9%E5%BC%8F%E5%AE%89%E8%A3%85%2FLinux%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85)
* [MacOS系统安装](https://www.openjiuwen.com/docs-page?version=studio-v0.1.3-zh-Goo4vjOp&path=2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC%2FDocker%E6%96%B9%E5%BC%8F%E5%AE%89%E8%A3%85%2FMacOS%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85)

# ​本地安装
社区提供了**一键安装**和**手动安装**2种本地安装方式。

## 一键安装
一键安装脚本可以自动完成基础工具检查、代码拉取、环境配置和服务启动等步骤，大幅简化安装流程。社区提供了以下3种操作系统的一键安装, 可快速同时启动Studio和DeepSearch sdk。

该功能正在测试中，将在后续版本推出，敬请期待。

## 手动安装
手动安装DeepSearch完整版，需要手动完成所需依赖安装、源码获取、Studio和DeepSearch SDK安装等步骤。
> **注意**：<br>
> 本地手动安装需要同时安装Studio和DeepSearch SDK；<br>
> 如果仅使用DeepSearch服务，Studio中的可选部分可直接跳过；<br>
> 请确保2个项目.env中的2个参数保持一致：1）Studio的DEEPSEARCH_AGENT_HOST 与 DeepSearch的HOST；2）Studio的DEEPSEARCH_AGENT_PORT 与 DeepSearch的BACKEND_PORT。<br>

社区提供了以下3种操作系统的手动安装。
* **Windows系统安装**
  * [Studio](https://www.openjiuwen.com/docs-page?version=studio-v0.1.3-zh-Goo4vjOp&path=2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC%2F%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85%2FWindows%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85): 参考章节《方法二：手动安装全部依赖》
  * [DeepSearch SDK](./DeepSearch_SDK/本地安装/Windows系统安装.md): 参考章节《方法二：全部手动安装》
* **Linux系统安装**
  * [Studio](https://www.openjiuwen.com/docs-page?version=studio-v0.1.3-zh-Goo4vjOp&path=2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC%2F%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85%2FLinux%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85): 参考章节《方法二：手动安装全部依赖》
  * [DeepSearch SDK](./DeepSearch_SDK/本地安装/Linux系统安装.md): 参考章节《方法二：全部手动安装》
* **MacOS系统安装**
  * [Studio](https://www.openjiuwen.com/docs-page?version=studio-v0.1.3-zh-Goo4vjOp&path=2.%E5%AE%89%E8%A3%85%E6%8C%87%E5%AF%BC%2F%E6%9C%AC%E5%9C%B0%E5%AE%89%E8%A3%85%2FMacOS%E7%B3%BB%E7%BB%9F%E5%AE%89%E8%A3%85): 参考章节《方法二：手动安装全部依赖》
  * [DeepSearch SDK](./DeepSearch_SDK/本地安装/MacOS系统安装.md): 参考章节《方法二：全部手动安装》