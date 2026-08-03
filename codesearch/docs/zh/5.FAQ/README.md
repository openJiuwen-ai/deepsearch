# FAQ

## 安装

**安装依赖时报预发布版本错误？**
openJiuwen 的传递依赖包含预发布版本，使用 uv 时加 `--prerelease=allow`
（本包已在 `[tool.uv]` 中默认放行）。

**能否与其他基于 openJiuwen 的产品装在同一个虚拟环境？**
若两者锁定的框架版本不同则不能——Python 同一环境中一个发行包只能有一个版本。
请分别使用独立虚拟环境或容器。向量库层面的共存不受影响。

## 索引

**索引一个非 Python 仓库得到 0 个文件？**
当前的语法切块器仅支持 Python。多语言可通过实现 `Chunker` 协议扩展，
见[开发指南](../4.开发指南/README.md)的扩展点一节。

**索引大型仓库时如何控制磁盘占用？**
先用 `--max-files 200` 做小样索引并测量实际占用，再按比例外推。对空间敏感的
场景可加 `--no-trigram` 跳过三元组字段（该字段约为原文的 7 倍体积，是存储
主要来源），代价是失去精确子串检索能力。

**重复索引同一仓库会重复占用空间吗？**
不会。索引按文件内容哈希去重，未变更的文件只会追加版本标签，不重复入库。
重复索引时输出中的 `reused` 计数即复用的文件数。

**索引需要 LLM API Key 吗？**
默认稀疏索引模式不需要——稀疏向量由 Milvus 服务端的 BM25 Function 生成。
仅在启用稠密向量模式时需要 embedding 服务。

## 检索

**检索返回 `index_not_ready`？**
该集合或该版本尚无索引数据。请先执行 `index`，并确认检索时的 `--revision`
与索引时一致（两者默认均为 `local`）。

**检索结果的终止方式是什么意思？**

| 取值 | 含义 |
|---|---|
| `submitted` | 智能体主动提交结论（正常路径） |
| `stagnated` | 连续多轮检索无新增发现，提前终止并返回已有结果 |
| `max_turns` | 达到轮次上限，返回已收集的结果 |
| `no_tool_call` / `llm_error` | 模型未继续调用工具或调用异常，返回已收集的结果 |
| `index_not_ready` | 索引未就绪，未进入检索循环 |

**如何调整检索的深度与 token 开销？**
`SearchAgentConfig` 中的 `max_turns`（轮次上限）、`stagnation_rounds`
（提前终止阈值）、`search_topk`（单次检索条数）、`retrieve_topk`（最终返回数）
共同决定检索深度与 token 消耗，见[开发指南](../4.开发指南/README.md)的配置一节。
每次检索的结果里包含 `total_input_tokens` 与 `total_output_tokens`，
可据此按所用端点的单价折算费用（结果本身不含金额）。

**能否使用本地模型或其他厂商的模型？**
可以。`LLMConfig` 接受任意 OpenAI 兼容端点，主模型与筛选模型可分别配置。

**如何启用 Retropus？相关环境变量有哪些？**
安装 `pip install 'openjiuwen-codesearch[retropus]'`，并设置
`CodeSearchConfig.agent.engine = "retropus"`（或
`python -m benchmarks.contextbench.runner --engine retropus`）。
`ENGINE=` 不是环境变量。循环与索引参数见
`CodeSearchConfig.retropus`（`MAX_ROUNDS` / `MAX_TOOL_CALLS` / `FEAT_*` 等），
完整表：[retropus-agent.md](../../feature/framework/retropus-agent.md)；
模板：[`.env.example`](../../../.env.example)。Retropus 不使用 Milvus。

## 运行

**报错提示证书或 SSL 相关配置缺失？**
SDK 默认使用 certifi 提供的 CA 证书包并自动完成相关配置。使用自签证书时，
通过 `LLMConfig.ssl_cert` 指定证书路径。

**工作流报执行超时？**
框架的工作流默认超时较短，SDK 已按运行注入 `SearchAgentConfig.time_limit_seconds`
（默认 900 秒），检索特别深的场景可调大该值。

**Linux 下 `docker ps` 提示权限不足？**
将当前用户加入 docker 组后重新登录：`sudo usermod -aG docker $USER`。
