# openjiuwen-search-base

**openjiuwen-search-base** 提供 search 场景产品的公共基础能力，供
openJiuwen 系列检索产品复用。本包不依赖任何产品包，核心仅依赖 pydantic，
重量级依赖以可选分组提供并使用受保护导入。

## 模块

| 模块 | 能力 |
|---|---|
| `llm` | LLM 客户端协议、消息与工具调用的规范化模型、openJiuwen 模型适配（含 SSL 证书处理）、`LLMConfig` |
| `embedding` | OpenAI 兼容的 embedding 客户端：本地 SQLite 缓存、有限重试与指数退避、持久连接复用、可注入传输层 |
| `milvus` | 查询表达式安全构造（统一转义）、集合命名约定（产品前缀 + 模式版本）、通用存取客户端（连接管理、建库建索引、批量读写、两段式查询、检索执行、同步调用线程隔离） |
| `workflow` | 工作流节点模板（三段式）与分支路由构造 |
| `logging_utils` | 日志管理与敏感信息脱敏 |
| `runtime` | 运行注册表：以 run_id 在工作流中传递，活对象不进入可复制的工作流状态 |

## 安装

```sh
pip install -e .                       # 核心
pip install -e '.[workflow,milvus,embed]'   # 按需启用
```

| 分组 | 依赖 | 何时需要 |
|---|---|---|
| `workflow` | openjiuwen、certifi | 使用工作流节点模板或 LLM 适配 |
| `milvus` | pymilvus | 使用 Milvus 存取客户端 |
| `embed` | aiohttp | 使用 embedding 客户端 |

## 设计约定

- **依赖方向**：base 不依赖任何产品包，产品依赖 base；
- **可选重依赖**：核心导入路径不触碰 openjiuwen / pymilvus / aiohttp，
  未安装对应分组时仍可导入并测试纯逻辑部分；
- **命名空间隔离**：`versioned_collection_name` 生成 `{前缀}{名称}__{模式版本}`
  形式的集合名，使多个产品可安全共用同一 Milvus 实例；
- **表达式安全**：所有 Milvus 查询表达式必须经 `milvus.expr` 构造，禁止字符串拼接。

## 测试

```sh
pytest tests -W ignore
```

## 许可证

[Apache License 2.0](LICENSE)
