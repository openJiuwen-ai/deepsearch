# AGENTS.md — openJiuwen-CodeSearch 工程规范

> 面向所有协作者（人类与 AI 助手）。修改代码前先读本文件与
> [docs/zh/4.开发指南](docs/zh/4.开发指南/README.md)。

## 分层纪律（依赖方向只允许向左，违反即架构缺陷）

```
[base] ← domain ← config ← algorithm ← framework/openjiuwen ← api
```

- `algorithm/` **禁止** import `framework/`；`domain/` 不 import 产品包内模块；
- `benchmarks/` 与 `openjiuwen_codesearch/server/` 只依赖公共 API（`api/`），核心包不得反向 import；
- search 场景公共能力放同仓 `base/`（openjiuwen-search-base）：base 不依赖
  任何产品包，重依赖一律 optional extras + guarded import。

## 行为契约（有测试锁定，改动即显式行为变更，需同步改测试与文档）

- 片段记忆渲染格式、最终结果排序 `(file_path, start_line)`、工具结果消息文本；
- 双引擎（graph/react）输出**逐字节一致**（集成测试锁定）；
- Milvus 共存：只读写 `cs_` 前缀 + `__{schema_version}` 后缀的 collection
  （e2e 用例锁定）；schema 变更必须递增 `schema_version`，禁止静默复用旧结构。

## 测试纪律

- `tests/unit`：零外部依赖（不得 import openjiuwen/pymilvus，fixture 回放）；
- `tests/integration`：需 openjiuwen；`tests/e2e`：需真实 Milvus（`MILVUS_PORT` 可覆盖）；
- 新功能必须带测试；改行为契约必须先改对应测试。

## 安全纪律

- 禁止 `verify_ssl=False` 默认值；Milvus expr 一律经 base 的安全构造函数
  （禁止 f-string 直拼用户/LLM 输入）；
- 密钥以 `bytearray` 存储于配置模型，仅在调用外部服务时经
  `openjiuwen_search_base.security.reveal_secret` 解码；禁止硬编码、禁止入日志；
- 运行产物（results/agent_logs/repos/tmp/缓存）不入库（见 .gitignore）。

## 文档义务

- 行为可见的变更需同步 `docs/`（中文为准）；特性级设计进 `docs/feature/`
  （按 [_template.md](docs/feature/_template.md)）；
- README 中的命令必须与真实 CLI 一致（改参数须同步）。

## 交付形态

四种：本地源码、whl 包、Docker 镜像、HTTP 服务。服务层随包分发，whl 装完可用 `codesearch-server` 启动
（`packages.find` 只收 `openjiuwen_codesearch*`），以源码或镜像部署。
发布前须通过 `python scripts/release_check.py`（版本一致性、依赖形态、base pin）。

## 配置纪律

- 全部配置经 pydantic 模型构造注入；**禁止任何全局可变配置**；
- 运行态一律挂 RunContext，禁止挂 Agent 实例 `self`（并发隔离）。
