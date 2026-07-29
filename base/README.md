# openjiuwen-search-base（过渡包）

search 场景的公共能力，自 codesearch 提取（2026-07-29，leader 裁决："后续规划一个
base 包，把所有 search 场景需要的公共能力都使用 base 包来提供"）。
**当前唯一消费者为 codesearch**；deepsearch 未迁移（有各自实现），正式 base 包的
接口与命名待两产品团队对齐后定稿。

## 模块

| 模块 | 能力 | 对应 deepsearch 的既有实现（未动） |
|---|---|---|
| `llm` | LLM 客户端协议、消息/响应/工具调用规范化模型、openJiuwen 适配（含 SSL 证书与 SAFE_CERT_DIR 处理）、`LLMConfig` | `llm/llm_wrapper.py`、`framework/openjiuwen/llm/llm_adapter.py` |
| `embedding` | OpenAI 兼容 embedding 客户端（SQLite 缓存、有限重试指数退避、查询前缀） | `algorithm/search_tools/retrieval/embedder.py` |
| `milvus` | expr 安全构造（统一转义，防注入）、collection 命名约定（产品前缀 + schema 版本，**共用实例隔离的基础**）、`store.MilvusCollectionClient`——schema 无关的存取基建：连接/建库+索引、批式 insert/upsert、两段式查询（防 payload 上限）、ann/hybrid 检索执行、release、同步调用线程隔离 | `utils` 内散落实现 + 各自 store |
| `workflow` | BaseNode 三段式模板 + `init_router`（BranchRouter 按 next_node 分支） | `framework/openjiuwen/agent/base_node.py` |
| `logging_utils` | LogManager（敏感脱敏开关） | `utils/log_utils/` |
| `runtime` | 泛型运行注册表（per-run 活对象注入，workflow state 不携带大负载/锁对象） | `DeepSearchRunContext` + contextvar 模式 |

## 依赖方向

`base` 不依赖任何产品包；核心仅 pydantic，重依赖（openjiuwen/pymilvus/aiohttp）
为可选分组、guarded import。

## 发布前 TODO

1. **共同发布**：codesearch 的发布物依赖 `openjiuwen-search-base==0.1.*`；
   workspace source 只在仓内开发生效，发布时 base 必须一并发布到同一索引源
   （或改为 vendor 进 codesearch 发布物），否则用户安装即失败。
2. **版本策略**：产品版本号与 deepsearch 同步（0.2.0），base 当前独立计数
   （0.1.0）——正式 release notes 中 base 的版本归属需与 leader 确认。
   同步目前仅是注释约定，**发布 checklist/CI 应校验两产品 pyproject 的
   version 一致**（人肉维持会漂）。
3. `workflow` extra 的 openjiuwen 兼容性：两个 API 面
   （`core.foundation.llm` 与 `core.workflow`）已在 **PyPI
   openjiuwen==0.1.10.post3 与 gitcode agent-core v0.1.13 双版本上验证共存**
   （2026-07-28 spike + 全量测试）；其他版本未验证（上界开放，升级需回归）。
4. **openjiuwen 依赖的发布路径**：codesearch 当前锁
   `git+https://gitcode.com/...@v0.1.13`——git direct reference **不能进入
   发布到公共索引的 wheel 元数据**（PyPI 会拒）。发布前需：agent-core v0.1.13
   发布到 PyPI/内部索引，或改锁 PyPI `openjiuwen==0.1.10.post3`（已验证兼容）。
5. **密钥处理对齐**：deepsearch 惯例为 `api_key` 用 bytearray + 用后清零
   （`zero_secret`）；base/codesearch 当前为普通 str。安全审视前对齐
   （涉及 LLMConfig/EmbedderSettings 类型与 openjiuwen 入参适配，独立任务）。
6. **semver 纪律**：消费方按 `==0.1.*` pin——因此任何 **breaking change
   必须升 minor（0.1.x → 0.2.0）**；0.1.x 内只做向后兼容的修复与新增。
7. **第三方声明**：codesearch/base 引入的 pymilvus/aiohttp/certifi 需补入
   仓库根 `Open_Source_Software_Notice.txt`（随整体项目 release 的法务清单）。
