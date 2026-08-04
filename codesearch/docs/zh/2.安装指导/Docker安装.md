# Docker 安装

推荐给需要快速部署、隔离运行的用户。镜像默认以 **HTTP 服务**形态启动，
构建时会同时安装同仓的 `openjiuwen-search-base` 与本包。

## 一、环境准备

| 项 | 要求 |
|---|---|
| Docker | 可用的 Docker Engine / Desktop |
| Milvus | 宿主机或可达网络上的 Milvus ≥ 2.5 |
| LLM API Key | `OPENROUTER_API_KEY`（检索需要） |

> **待索引的代码仓**：须是容器内可见的**本地目录**（通常用 `-v` 挂载宿主机上
> 已 clone 的仓库）。不支持把 git URL 直接传给索引接口；请先在宿主机
> `git clone`，再挂载进容器。详见
> [安装指导总览 · 待索引仓库](./README.md#待索引仓库本地路径)。

## 二、构建镜像

构建上下文必须是 **monorepo 根目录**（需同时拷贝 `base/` 与 `codesearch/`）：

```sh
# 在 <repo_root>/ 下执行，不要进入 codesearch/ 子目录再 build
docker build -f codesearch/docker/Dockerfile -t openjiuwen-codesearch:0.2.0 .
```

## 三、运行服务

先准备本地代码仓，再挂载：

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
```

容器内访问宿主机 Milvus 时使用 `host.docker.internal`
（Linux 可加 `--add-host=host.docker.internal:host-gateway`）：

```sh
docker run --rm --name codesearch-server \
  -p 8100:8100 \
  -e OPENROUTER_API_KEY \
  -e MILVUS_HOST=host.docker.internal \
  -e MILVUS_PORT=19530 \
  -e CODESEARCH_INDEX_ROOTS=/repo \
  -v /data/repos/agent-core:/repo \
  openjiuwen-codesearch:0.2.0
```

```sh
curl -sf http://127.0.0.1:8100/api/health

# 索引：repo_path 是容器内路径 /repo
curl -sS -X POST http://127.0.0.1:8100/api/v1/index \
  -H 'Content-Type: application/json' \
  -d '{"repo_path":"/repo","collection":"agent_core","revision":"local","reset":true}'

# 检索：只用 collection 名
curl -sS -X POST http://127.0.0.1:8100/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"TypeError when calling foo() with empty list","collection":"agent_core","revision":"local","top_k":20}'
```

> **注意**：未设置 `CODESEARCH_INDEX_ROOTS` 时，`/api/v1/index` 返回 403
> （预期行为）。服务不含鉴权，请仅在可信网络或网关后暴露。

## 四、CLI 形态（覆盖入口）

镜像默认 `CMD` 为 `codesearch-server`。若要用 CLI，需显式指定入口：

```sh
docker run --rm \
  -e OPENROUTER_API_KEY \
  -e MILVUS_HOST=host.docker.internal \
  -e MILVUS_PORT=19530 \
  -v /data/repos/agent-core:/repo \
  --entrypoint codesearch \
  openjiuwen-codesearch:0.2.0 \
  index --repo /repo --collection agent_core --revision local --reset
```

```sh
docker run --rm \
  -e OPENROUTER_API_KEY \
  -e MILVUS_HOST=host.docker.internal \
  -e MILVUS_PORT=19530 \
  --entrypoint codesearch \
  openjiuwen-codesearch:0.2.0 \
  search --collection agent_core --query "TypeError when calling foo()" --top-k 10
```
