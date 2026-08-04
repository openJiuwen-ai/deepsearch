# whl 安装

推荐给生产环境。从 **正式发布地址** 下载并安装两个 wheel，无需源码树：

| 包 | 作用 |
|---|---|
| `openjiuwen-search-base` | search 场景公共能力（须先装或同时装） |
| `openjiuwen-codesearch` | CodeSearch 产品包（含 CLI 与 HTTP 服务） |

> **说明**：发布流水线会同时上传上述两个 wheel。请将下文
> `<WHL_BASE_URL>` 替换为正式下载根地址（由发布公告提供）。

## 一、环境准备

| 项 | 要求 |
|---|---|
| Python | >= 3.11（建议独立虚拟环境） |
| Milvus | >= 2.5（推荐 2.6.x） |
| LLM API Key | `OPENROUTER_API_KEY`（检索需要） |

> **待索引的代码仓**：须是运行 `codesearch` / `codesearch-server` 的机器上的
> **本地目录**。远程 git 仓库请先 clone，再对本地路径建索引。详见
> [安装指导总览 · 待索引仓库](./README.md#待索引仓库本地路径)。

## 二、安装方法

```sh
python3 -m venv .venv && source .venv/bin/activate

# 将 <WHL_BASE_URL> 换成正式发布地址，例如：
#   https://<host>/releases/openjiuwen-codesearch/0.2.0
export WHL_BASE_URL="https://<正式发布地址>"

pip install \
  "${WHL_BASE_URL}/openjiuwen_search_base-0.2.0-py3-none-any.whl" \
  "${WHL_BASE_URL}/openjiuwen_codesearch-0.2.0-py3-none-any.whl[milvus,llm,server]"
```

使用 uv 时需放行预发布传递依赖：

```sh
uv venv .venv
uv pip install --python .venv/bin/python \
  "${WHL_BASE_URL}/openjiuwen_search_base-0.2.0-py3-none-any.whl" \
  "${WHL_BASE_URL}/openjiuwen_codesearch-0.2.0-py3-none-any.whl[milvus,llm,server]" \
  --prerelease=allow
```

> openJiuwen 锁定了预发布包 `a2a-sdk==1.0.0a0`。`pip` 对精确 pin 的预发布
> 一般可直接安装；`uv` 需加 `--prerelease=allow`。

安装完成后应能直接调用：

```sh
codesearch --help
codesearch-server --help   # 或直接 codesearch-server 启动
```

## 三、索引 / 启动服务

在任意目录启动即可（不依赖源码树）：

```sh
git clone git@gitcode.com:openJiuwen/agent-core.git /data/repos/agent-core
export TARGET_REPO=/data/repos/agent-core

export OPENROUTER_API_KEY="your-key"
export MILVUS_HOST=localhost MILVUS_PORT=19530
export CODESEARCH_HOST=0.0.0.0 CODESEARCH_PORT=8100
export CODESEARCH_INDEX_ROOTS="/data/repos"

# CLI 索引（--repo = 本地路径；--collection = 自定集合名）
codesearch index --repo "$TARGET_REPO" --collection agent_core --revision local

codesearch-server
```

```sh
curl -sf http://127.0.0.1:8100/api/health

curl -sS -X POST http://127.0.0.1:8100/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"TypeError when calling foo() with empty list","collection":"agent_core","revision":"local","top_k":20}'
```

> **注意**
>
> - 未配置 `CODESEARCH_INDEX_ROOTS` 时，`POST /api/v1/index` 返回 **403**，
>   这是安全默认值，不是安装失败。启动日志也会打印相应告警。
> - 服务不含鉴权，生产环境请置于可信网络或网关之后。

## 四、常见问题

**只装了 codesearch 的 whl？**  
还需要同版本的 `openjiuwen-search-base` wheel，否则依赖无法满足。

**版本号不一致？**  
`base` 与 `codesearch` 按同系列版本发布（当前为 `0.2.0`），请成对下载安装。
