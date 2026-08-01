# FAQ

**uv 安装报 a2a-sdk 预发布错误？**
加 `--prerelease=allow`（openjiuwen 的传递依赖含预发布版本）。

**search 返回 `index_not_ready`？**
该 collection/revision 尚无索引数据：先执行 `index`，且检索的 `--revision`
必须与索引时一致（默认均为 `local`）。

**SSL 报错 `ssl_cert is required` / `SAFE_CERT_DIR is not set`？**
SDK 已自动处理（默认注入 certifi CA 包并注册其目录）；使用自签证书时配置
`LLMConfig.ssl_cert`。

**graph 引擎报 `workflow execution exceeded time limit of 60 seconds`？**
openJiuwen workflow 默认超时仅 60s；SDK 已按运行注入
`SearchAgentConfig.time_limit_seconds`（默认 900s），按需调大。

**索引大仓库担心磁盘占用？**
先 `--max-files 200` 小样实测再外推（经验值约 72KB/chunk，trigram 字段为
存储大头）；空间敏感场景加 `--no-trigram`（省 ~85%，代价：精确子串检索失效）。

**与他人共用一个 Milvus 实例安全吗？**
collection 命名自带 `cs_` 产品前缀与 `__{schema_version}` 版本后缀，
连接别名默认 `codesearch`（不抢占 "default"），命名空间天然隔离；
资源层面建议独立实例并设置容器内存上限，详见
[runbook](../../feature/runbook-server-indexing.md)。

**索引非 Python 仓库得到 0 个文件？**
当前切块器为 Python-only（`**/*.py`）；多语言支持通过 `Chunker` 协议扩展。

**`docker ps` 权限不足（Linux）？**
`sudo usermod -aG docker $USER` 后重新登录。

**如何启用 Retropus？相关环境变量有哪些？**
安装 `pip install 'openjiuwen-codesearch[retropus]'`，并设置
`CodeSearchConfig.agent.engine = "retropus"`（或
`python -m benchmarks.contextbench.runner --engine retropus`）。
`ENGINE=` 不是环境变量。循环与索引参数见
`CodeSearchConfig.retropus`（`MAX_ROUNDS` / `MAX_TOOL_CALLS` / `IMP_*` 等），
完整表：[retropus-agent.md](../../feature/framework/retropus-agent.md)；
模板：[`.env.example`](../../../.env.example)。Retropus 不使用 Milvus。
