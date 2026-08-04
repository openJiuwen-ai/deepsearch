# RetropusCodeSearchAgent

## 维护范围

覆盖 `engine="retropus"` 时的索引、工具注册表与检索循环；不覆盖 CodeSearch 默认
`react`/`graph` 引擎（见 [codesearch-workflow.md](./codesearch-workflow.md) /
[search-agent.md](../algorithm/search-agent.md)）。

## 功能目的

将 ContextBench Retropus 检索 agent 以产品内引擎形态接入 openjiuwen_codesearch：
使用 openjiuwen LLM 客户端，保留 Retropus 原有工具集与 KG/BM25 索引，同时与
CodeSearch 默认五工具注册表严格隔离。

## 核心流程

1. `CodeSearchConfig.agent.engine = "retropus"`（程序赋值、CLI / ContextBench
   `--engine retropus`，或 HTTP 请求体 `"engine": "retropus"`；**无** `ENGINE=`
   环境变量；默认仍为 `auto`，不会自动走 Retropus）
2. `index_repository(repo_path)` → vendored `build_index` + `build_retriever`（无 Milvus）
3. `search(query)` → `RetropusRunContext` + `RetropusCodeSearchAgent`
4. `AbstractReactEngine.run`：`reasoning_step` → `tool_step` →（终止时）`finalize`
5. `finalize`：`tools.final_spans()`（必要时 retriever pad）→ `spans_to_hits` →
   `CodeSearchResult.hits`

## HTTP 服务

`POST /api/v1/index` 与 `POST /api/v1/search` 接受可选字段 `engine`
（`auto` | `react` | `graph` | `retropus`，**默认 `auto`**）。Retropus 必须显式传
`"engine": "retropus"`；索引与检索须使用同一后端（retropus ↔ milvus 混用返回
**409**）。服务按 `(collection, engine)` 缓存检索器；Retropus 的 KG/BM25 驻留
进程内存，索引作业成功后不会 `close()` 该实例（与 Milvus 路径不同）。

## 可见行为

- 公共 API 仍返回 `CodeSearchResult`（hits 来自 pred_spans / final_spans）。
- CodeSearchAgent 的 `get_registry()` / `build_default_registry()` 不变，不含 retropus 工具。
- Retropus 默认工具：`search_code`、`search_text`、`get_repo_structure`、`read_file`、
  `add_context`、`finish`（及 flag 门控的 expand_* / `delete_snippets`）；不含
  CodeSearch 的 `search_codebase` 等。
- 未索引时 `search` 返回 `Termination.INDEX_NOT_READY`。
- 需安装可选依赖：`pip install 'openjiuwen-codesearch[retropus]'`。

## 配置（`CodeSearchConfig.retropus`）

全部字段在 `RetropusSearchAgentConfig`（`openjiuwen_codesearch/config/agent.py`）。
经 `CodeSearchConfig.from_env()` → `RetropusSearchAgentConfig.from_env()` 从
`codesearch/.env`（若存在）与进程环境加载；进程环境优先于 `.env`。
LLM 凭证仍在 `CodeSearchConfig.llm`（`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MODEL`），
不在本块。示例见仓库根 [`.env.example`](../../../.env.example)。

### 索引 / 检索后端

| 字段 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `retriever` | `RETRIEVER` | `bm25` | 仅支持 `bm25`；其他值在 `build_retriever` 抛错 |
| `max_ast_depth` | `MAX_AST_DEPTH` | `6` | KG 构建时 AST 遍历深度（需足以覆盖函数/类定义节点） |
| `chunk_size` | `CHUNK_SIZE` | `1000` | 非代码文本切块大小 |
| `chunk_overlap` | `CHUNK_OVERLAP` | `200` | 文本切块重叠 |
| `code_aware_tokenizer` | `CODE_AWARE_TOKENIZER` | `false` | BM25 用代码感知分词（拆标识符）而非 bm25s 默认分词 |
| `tokenize_workers` | `TOKENIZE_WORKERS` | `max(1, cpu_count-1)` | 语料分词并行度 |

### Agent 循环边界

| 字段 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `max_rounds` | `MAX_ROUNDS` | `12` | LLM 决策轮次上限（耗尽 → `MAX_TURNS`） |
| `max_tool_calls` | `MAX_TOOL_CALLS` | `24` | 工具调用总次数上限 |
| `max_final_spans` | `MAX_FINAL_SPANS` | `25` | `finalize` 输出 span 条数上限（再与 `top_k` 取小） |
| `max_obs_chars` | `MAX_OBS_CHARS` | `6000` | 单次工具观察文本截断长度 |
| `max_read_lines` | `MAX_READ_LINES` | `400` | `read_file` 单次可读行数上限 |

### Finish / 补齐策略

| 字段 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `min_spans_before_finish` | `MIN_SPANS_BEFORE_FINISH` | `3` | 仅当 `feat_anti_early_finish` 开启时：`finish` 前最少 span 数 |
| `min_files_before_finish` | `MIN_FILES_BEFORE_FINISH` | `1` | 仅当 `feat_anti_early_finish` 开启时：`finish` 前最少文件数 |
| `min_mandatory_return_spans` | `MIN_MANDATORY_RETURN_SPANS` 或 `RETROPUS_MIN_MANDATORY_RETURN_SPANS` | `0` | 结束时若 spans 少于 N，用 top retriever defs 补齐；`0` = 关闭强制补齐（仅在完全空结果时走 legacy top-5 fallback） |

### Feature flags（`FEAT_*`）

布尔环境变量接受 `1` / `true` / `yes` / `on`（大小写不敏感）。

`FEAT_ALL=0|1` 可先强制全部关闭/开启，再被单个 `FEAT_<NAME>` 覆盖。
模型字段默认如下（**仅 `inherits_expand` 默认为开**）：

| 字段 | 环境变量 | 默认 | 效果（开启时） |
|---|---|---|---|
| `feat_ban_tests` | `FEAT_BAN_TESTS` | `false` | 检索/选 span 时压制测试路径（issue 本身谈测试时除外） |
| `feat_anti_early_finish` | `FEAT_ANTI_EARLY_FINISH` | `false` | `finish` 前强制 `min_spans` / `min_files` |
| `feat_same_file_expand` | `FEAT_SAME_FILE_EXPAND` | `false` | 注册 `expand_file_defs`；finish 前可同文件扩展 |
| `feat_second_file_probe` | `FEAT_SECOND_FILE_PROBE` | `false` | 已选文件时对第二文件做探测；可注册相关 expand |
| `feat_inherits_expand` | `FEAT_INHERITS_EXPAND` | **`true`** | 注册 `expand_inheritance`（沿 KG `INHERITS` 边建议邻居；**不**阻挡 `finish`） |
| `feat_expand_imports` | `FEAT_EXPAND_IMPORTS` | `false` | 注册 `expand_imports`（沿 KG `IMPORTS` 边建议邻居；**不**阻挡 `finish`） |
| `feat_delete_snippets` | `FEAT_DELETE_SNIPPETS` | `false` | 注册 CodeSearch 的 `delete_snippets`（按 `add_context` 返回的 span id 删除误录 span；复用 `memory_tools.execute_delete`） |

KG 在索引时**始终**构建 `IMPORTS` 边（Python/Java/JS/TS/Go/Rust/C/C++，
regex 解析、仅 in-repo）；flag 只控制是否暴露工具与系统提示附录。

### 与 `SearchAgentConfig` 的交叉项

Retropus 循环使用 `config.retropus` 的 `max_rounds` / `max_tool_calls`，
**不**使用 `agent.max_turns` / `agent.stagnation_rounds`。
仍会读取：

| 字段 | 用途 |
|---|---|
| `agent.engine` | 必须为 `"retropus"` 才走本引擎（默认 `auto`，需显式设置） |
| `agent.trace_dir` | 非空时写 `retropus_*.jsonl` 轨迹（默认 `agent_logs`；空串关闭） |
| `agent.retrieve_topk` | ContextBench runner 的 `search(..., top_k=...)`；与 `max_final_spans` 共同限制输出 |

## Prompt caching

Retropus binds a stable OpenAI/OpenRouter `prompt_cache_key` once per run from
`hash(system_prompt + tool_schemas)` (`retropus:{sha256[:24]}`) and passes it on
every `main_llm.invoke`. Issue text stays in the user message so the static
prefix can be reused across rounds and instances that share the same IMP flags.
This is quality-neutral (cost/latency only). Providers/SDKs that reject the field
are handled by a transparent retry without the key in
`openjiuwen_search_base.llm.OpenJiuwenLLMClient`.

## 数据契约与依赖

- LLM：仅 `LLMClient.invoke`（openjiuwen；可传 `prompt_cache_key=`）；不使用上游
  `retropus.llm.LLMClient`。
- 索引：进程内 KG + BM25，缓存于 `CodeSearchRetriever` 实例（按 `repo_dir`；无 Milvus）。
- 可选依赖：`tree-sitter` / `bm25s` 等，见 `pyproject.toml` 的 `[retropus]` extra。

## 边界与错误处理

- retropus 额外依赖缺失：`index_repository` / 构建路径抛出带安装提示的 `ImportError`。
- LLM 异常 → `Termination.LLM_ERROR`；轮次/工具预算耗尽 → `MAX_TURNS`；无工具调用 →
  `NO_TOOL_CALL`（含 nudge 后仍无 spans 时走 BM25 fallback）。
- `min_mandatory_return_spans`：见上表。

## 测试与验证

- `tests/unit/test_retropus_registry.py`：注册表隔离
- `tests/unit/test_retropus_agent.py`：假 LLM 回放、hits 映射、INDEX_NOT_READY、跳过 Milvus
- `tests/unit/test_retropus_agent_config.py`：`from_env` 读取 `MAX_*` / `FEAT_*` / 补齐相关变量
- `tests/unit/test_retropus_expand_imports.py`：`IMPORTS` 边解析、`expand_imports` 工具与 schema 门控
- `tests/unit/test_retropus_inherits_finish.py`：`inherits_expand` 不阻挡 finish
- `tests/unit/test_retropus_prompt_cache.py`：`prompt_cache_key` 稳定性与 invoke 透传
- `tests/unit/test_retropus_mandatory_return_spans.py`：强制补齐与 legacy fallback
- `tests/unit/test_retropus_text_splitter.py`：文本切块

## 关键代码路径

| 文件 | 内容 |
|---|---|
| `framework/openjiuwen/agent.py` | `AbstractReactEngine` + `RetropusCodeSearchAgent` + `spans_to_hits` |
| `framework/openjiuwen/retropus_context.py` | 运行态（含 `pending_calls`） |
| `utils/log_utils` | Retropus 与产品共用 `get_logger`（LogManager） |
| `algorithm/search_tools/retropus_registry.py` | `RetrievalTools` + 独立 ToolSpec 注册表 |
| `algorithm/search_tools/graph_tools.py` | Retropus expand_* ToolSpec + `GraphExpandTools` mixin |
| `algorithm/prompts/{system,inherits,expand_imports,...}.md` + `retropus.py` | Retropus 系统/工具观察提示词 |
| `retropus/graph/imports.py` | 多语言 `IMPORTS` 边构建与 `ImportIndex` |
| `retropus/` | 厂商化 KG / BM25 索引运行时 |
| `config/agent.py` | `RetropusSearchAgentConfig` + `from_env` |
| `api/retriever.py` | engine 分支与索引缓存；`engine_keeps_index_in_process` |
| `server/schemas.py` / `server/routers/api.py` | HTTP `engine` 字段、进程内缓存与跨后端 409 |

## 已知限制与待办 / 相关文档

- 无 graph 形态 Retropus。
- 上游 ContextBench bench runner 不随包发布；本仓
  `python -m benchmarks.contextbench.runner --engine retropus` 走产品 API。
- 相关： [codesearch-workflow.md](./codesearch-workflow.md) /
  [search-agent.md](../algorithm/search-agent.md) /
  [README 关键配置](../../../README.md#关键配置)。
