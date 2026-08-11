**阅读语言：** [English](./README_search.md) | 简体中文

# Search Agent 使用文档

## 目录

- [概述](#概述)
- [核心特性](#核心特性)
- [架构设计](#架构设计)
- [工作流程](#工作流程)
- [配置参数详解](#配置参数详解)
- [快速开始](#快速开始)
- [知识库索引构建](#browsecomp-plus-的-milvus-索引构建)


## 概述

Search Agent（DeepSearchAgent）是基于 openJiuwen 框架开发的智能搜索代理，支持深度搜索和推理能力。该代理能够处理复杂的查询问题，通过多步推理和工具调用生成准确的答案。采用状态空间搜索机制，通过维护和探索状态空间来寻找答案，并使用异步并发执行策略，能够同时探索多个推理路径，大幅提高搜索效率。


## 核心特性

### 主要特性：逻辑推理

1. **实体认知**：理解问题中的"谁、什么、关系"
2. **树状推理**：像人类一样多角度思考
3. **并行探索**：同时尝试多种解决方案

### 1. 实体认知引擎

- **动态实体图构建**：自动识别问题中的关键实体，构建实体间关系网络
- **状态轨迹追踪**：为每个实体维护完整的状态演变历史，实现推理过程可视化

### 2. 树状推理网络

- **推理分支管理**：将复杂问题分解为多角度推理树，支持并发探索
- **分支剪枝优化**：自动识别并剪除低价值推理路径，集中资源探索高潜力分支

### 3. 智能行动探索

- **广度-深度平衡**：平衡深度探索与最佳路径利用，防止推理过早收敛到局部最优，保持解决方案的多样性
- **概率引导采样**：基于置信度分数动态调整行动选择策略，采用加权随机采样策略，优先选择高分行动，同时保障探索的多样性
- **异步并发推理**：利用 `asyncio` 并发调度多个 `state_creation` 任务并行探索多条路径；`ActionPool` 在单协程上下文中使用，非线程安全

### 4. 其他特性

- **智能推理**：基于状态机的多步推理机制
- **工具集成**：支持网页搜索、本地检索、信息提取等多种工具
- **可配置性**：灵活的配置系统，支持自定义搜索参数


## 架构设计

### 核心组件

1. **三个工作流（Workflows）**
   - `init_state`: 初始化状态工作流，解析用户问题并创建初始搜索状态
   - `find_action`: 查找可行 action 工作流，基于当前状态生成候选搜索动作
   - `state_creation`: 状态创建和扩展工作流，执行 action 并扩展状态空间

2. **状态管理**
   - `State`: 表示搜索过程中的一个状态，包含变量列表、深度、ID、检索证据ID等
   - `Action`: 表示一个可执行的搜索动作，包含问题、状态和提案信息
   - `ActionPool`: 在异步搜索循环中管理和采样 actions 的池（单协程使用，非线程安全；采样权重受 `SearchWorkflowConfig.action_sampling` 控制）

3. **工具系统**
   - `WebSearch`: 网络搜索工具，通过搜索引擎获取相关信息
   - `WebFetch`: 网页内容获取工具，提取并分析网页内容
   - `Retrieve`: 检索工具，用于向量检索和知识库查询

### 工作流结构

在 `search` 模式下，Agent 执行以下流程：

1. **状态初始化**: 解析用户问题，创建初始搜索状态（实体识别、状态构建）
2. **动作空间探索**: 生成可能的搜索动作（推理分支生成）
3. **动作执行**: 并发调用工具执行搜索操作（工具调用）
4. **答案生成**: 生成最终答案

```
┌─────────────┐
│  init_state │  → 初始化初始状态（实体识别、状态构建）
└─────────────┘
      ↓
┌─────────────┐
│ find_action │  → 基于状态生成候选 actions（推理分支生成）
└─────────────┘
      ↓
┌─────────────┐
│state_creation│ → 并发执行 action，扩展状态空间（工具调用、状态验证）
└─────────────┘
      ↓
   (循环：继续探索或找到答案)
```

## 工作流程

### 1. 初始化阶段

```python
# 1. 初始化状态
init_state = await Runner.run_workflow("init_state_1", {
    "query": query,
    "config": init_config,
    ...
})

# 2. 生成初始 actions
actions = await Runner.run_workflow("find_action_1", {
    "state": init_state,
    "query": query,
    ...
})

# 3. 将 actions 添加到 action pool
action_pool.add(actions)
```

### 2. 搜索循环

```python
while not final_answer and time < time_limit:
    # 1. 从 action pool 中采样 actions
    sampled_actions = action_pool.sample(available_slots)
    
    # 2. 并发执行 state_creation
    for action in sampled_actions:
        task = asyncio.create_task(run_state_creation_workflow(action, tool_map, semaphore))
        running_tasks.add(task)
    
    # 3. 等待任务完成
    done, _ = await asyncio.wait(running_tasks, ...)
    
    # 4. 处理结果
    for task in done:
        result = await task
        if result.found_answer:
            return result  # 找到答案，退出
        
        # 5. 基于新状态生成新的 actions
        for new_state in result.new_states:
            new_actions = await Runner.run_workflow("find_action_1", {
                "state": new_state,
                "query": query,
                ...
            })
            action_pool.add(new_actions)
```


### 3. Action 采样策略

`ServiceConfig.search_workflow.action_sampling`（`ActionSamplingConfig`）与 `ActionPool` 配合：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `depth_weight` | bool | True | 是否启用深度惩罚/浅层奖励（与 `action_pool.py` 中阈值一致） |
| `promote_unique_states` | bool | False | 是否对池中重复状态哈希降权，促进多样性 |
| `random_sample` | bool | False | 为 True 时从池中均匀随机取出 action，跳过加权采样 |

`ActionPool` 上的智能采样行为包括：

- **评分加权**：基于 `action.proposal.score` 与各变量 `candidate_strength` 累加
- **深度惩罚**：深度 > 5 时应用惩罚，深度 < 2 时给予奖励，平衡探索深度
- **唯一状态提升**：当 `promote_unique_states` 为 True 时，对同一状态哈希出现多次的 action 降权
- **随机采样**：当 `random_sample` 为 True 时使用完全随机采样


## 配置参数详解

所有配置均在 `openjiuwen_deepsearch/config/config.py` 中定义，分为 **AgentConfig**（用户配置）与 **ServiceConfig**（服务参数）两类，下面分别介绍。

---

### 一、AgentConfig（用户配置）

**说明**：AgentConfig 是需要用户按业务填写的参数，对外暴露，供调用方传入或修改（如 LLM、检索、工具密钥、单题参数等）。**Search 工作流结构（Initial State、Find Action、State Creation 等）属于 ServiceConfig，见下文二、ServiceConfig。**

AgentConfig 下与 Search 相关的主要包括：

- **单题参数**：`search_workflow_per_question_params`（PerQuestionParams），如 `tool_map`、`time_limit` 等
- **Milvus/Embedder（retrieve 模式）**：`search_workflow_milvus_config`（MilvusConfig）

以下为 AgentConfig 中上述子配置的详细参数说明。

#### 1. PerQuestionParams（AgentConfig.search_workflow_per_question_params）

单个问题（一次搜索/推理过程）的控制参数。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_workers` | int | 5 | 并发执行 action 的最大协程数，根据系统资源和 API 限制调整 |
| `retry_count_on_empty_action_space` | int | 3 | action pool 为空且无在跑任务时，重新执行 find_action 的最大次数 |
| `time_limit` | int | 4800 | 单个问题最大运行时间（秒），默认 80 分钟 |
| `tool_map` | Literal["search_fetch", "retrieve"] | "search_fetch" | 工具映射模式 |
| `actions_explored_limit` | int | 200 | 每完成一次 `state_creation` 计一次探索；`>0` 且达到该值时终止。需要更长搜索时可调大 |
| `fail_limit` | int | 0 | 最大连续失败次数；`0` 表示无限制 |
| `answer_mode_top_k` | int | 1 | 收集多少个候选答案后再择优；`<=1` 为找到即返回 |
| `provide_best_guess` | bool | False | 超时未确认答案时，是否按 `candidate_strength` 返回最佳猜测 |

**工具模式说明**：

1. search_fetch 模式：使用 `WebSearch` 和 `WebFetch` 工具进行网络搜索和内容获取。

适用场景：
- 需要实时网络信息的问题
- 需要访问最新网页内容的问题
- 开放域问答

配置要求：
- `web_search_engine_config`: 配置 `search_engine_name`、`search_api_key`、可选的 `search_url` 和 `max_web_search_results`。
- `web_fetch_provider_config`: 配置 `provider_name`、`api_key` 和可选的 `base_url`。当前内置网页抓取 provider 为 Jina。

2. retrieve 模式：使用 `Retrieve` 工具进行向量检索。

适用场景：
- 基于知识库的问答
- 需要精确文档检索的问题
- 封闭域问答

配置要求：
- 需要先构建知识库索引（见 [知识库索引构建](#browsecomp-plus-的-milvus-索引构建)）。
- 在 **AgentConfig.search_workflow_milvus_config**（MilvusConfig）中配置向量库与 Embedding：`milvus_host`、`milvus_port`、`database_name`、`collection_name`、`embedder_model_name`、`embedder_api_key`、`embedder_base_url`、`embedder_timeout`。与索引脚本中的环境变量 / 常量（如 `MILVUS_URI`、`MILVUS_DB_NAME`、`EMBED_*`）对应；索引脚本侧重环境变量，运行时 retrieve 使用 `AgentConfig` 中的 MilvusConfig。


#### 2. MilvusConfig（AgentConfig.search_workflow_milvus_config，retrieve 模式）

当 `tool_map == "retrieve"` 时，使用 `AgentConfig.search_workflow_milvus_config`（类型为 `MilvusConfig`）配置向量库与 Embedding 服务。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `milvus_host` | `str` | `"localhost"` | Milvus 主机地址，例如：`localhost`。 |
| `milvus_port` | `int` | `19530` | Milvus 端口，例如：`19530`。 |
| `database_name` | `str` | `"deepsearch_benchmarks"` | 数据库名称，例如：`deepsearch_benchmarks`。 |
| `collection_name` | `str` | `"browsecompplus_with_bm25"` | 集合名称，例如：`browsecompplus_with_bm25`。 |
| `embedder_model_name` | `str` | `""` | Embedding 模型名称，须与构建索引时使用的模型一致。 |
| `embedder_api_key` | `bytearray` | - | Embedding 模型 API Key；未填写或为空时，在创建 retrieve 工具会报错。 |
| `embedder_base_url` | `str` | `""` | Embedding 服务地址，例如：`http://localhost:11450/v1/embeddings`；未填写会报错。 |
| `embedder_timeout` | `int` | `100` | Embedding 请求超时时间（秒），例如：`60`。 |

**说明**：retrieve 模式下，`embedder_model_name`、`embedder_api_key`、`embedder_base_url`、`embedder_timeout` 需在配置中正确填写，否则创建检索工具时会报错。


**检索模式说明**：
- `dense`: 密集检索（向量相似度）
- `sparse`: 稀疏检索（关键词匹配）
- `hybrid`: 混合检索（结合密集和稀疏，推荐）

**检索与合并逻辑：**

1. **初始检索**
   根据所选的 `mode`，系统先检索相似度最高的 `top_k * top_k_multiply_factor` 个文本块。

2. **合并（Merging）**
   来自同一文档的数据块（例如来自 `browsecompplus` 数据集）会被合并成一个“文档块”：

   * 若同一文档中命中了多个块，这些块会被合并。
   * **注意**：如果某个文档的首块未出现在初始结果中，但该文档的其他块命中了，则系统会自动补齐并合并首块，以提供必要的上下文。

3. **最终筛选**
   合并后的文档块会按照其最大相似度得分（即其内部各块中的最高分）进行排序，最终选取得分最高的 `top_k` 个文档块返回给 Agent。

**参数配置方式（AgentConfig）**：用户可在 `config.py` 中修改默认值，或在代码中通过 `Config().agent_config.model_dump()` 取得配置后按需覆盖再传入 Agent。

---

### 二、ServiceConfig（服务参数）

**说明**：ServiceConfig 是服务端/运行时使用的参数，不暴露给用户，一般由部署方在代码或配置中维护。用于控制工作流超时、各节点重试与并发、统计与调试开关等。

其中 **Search 工作流**由 `ServiceConfig.search_workflow`（类型为 `SearchWorkflowConfig`）定义，**其下才包含**：初始化状态（init_state_agent）、动作发现（find_action_agent）、状态创建（state_creation_agent）等子配置。以下先说明 SearchWorkflowConfig 及其子配置，再列出 ServiceConfig 其余参数概览。

#### SearchWorkflowConfig 及其子配置（ServiceConfig.search_workflow）

`SearchWorkflowConfig` 包含 **action 采样** 与 **三个 Agent 子配置**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `action_sampling` | ActionSamplingConfig | ActionPool 采样：深度权重、唯一状态降权、随机采样（见上文 [Action 采样策略](#3-action-采样策略)） |
| `init_state_agent` | InitStateAgentConfig | 初始化状态 Agent |
| `find_action_agent` | FindActionAgentConfig | 查找 Action Agent |
| `state_creation_agent` | StateCreationAgentConfig | 状态创建/扩展 Agent（内含 retrieval_settings、validator_agent 等） |

各子 Agent 的 `llm_config` 类型均为 **`Dict[Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig]`**（键到具体 `LLMConfig` 的映射），与根 `AgentConfig.llm_config` 类似但不含 `vlm_chart_generating`。

**InitStateAgentConfig**（`search_workflow.init_state_agent`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_tries` | int | 10 | 最大重试次数 |
| `llm_config` | 见上 | `{}` | 按类别键配置的 LLM |

**FindActionAgentConfig**（`search_workflow.find_action_agent`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `llm_config` | 见上 | `{}` | 按类别键配置的 LLM |
| `action_proposals_limit` | int | 5 | 最大 action 提案数，控制每次生成的候选 action 数量，值越大探索空间越大 |
| `action_pool_depleted_strategy` | Literal["simple_retry", "dependent_retry"] | `"dependent_retry"` | action pool 耗尽时：`simple_retry` 无额外上下文重跑 find_action；`dependent_retry` 附带已探索方向等上下文 |

**ValidatorAgentConfig**（`state_creation_agent.validator_agent`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `validate_new_states` | bool | False | 是否验证新状态 |
| `validate_answer` | bool | False | 是否验证答案 |
| `llm_config` | 见上 | `{}` | 按类别键配置的 LLM |

**RetrievalSettingsConfig**（`state_creation_agent.retrieval_settings`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `retrieval_prompt` | Literal["retrieve", "retrieve_given_multihop_query"] | "retrieve" | 配置 LLM 调用检索工具时使用的提示词。<br> - `"retrieve"`：生成简单的
基于关键词的 Web 搜索查询（用于 BrowseComp+，默认选项）。<br> - `"retrieve_given_multihop_query"`：引导 LLM 生成多跳查询（适用于类似 GEAR 的检索器，性能尚未充分测试）。 |
| `top_k` | int | 3 | 最终返回给 Agent 的文档块（document chunks）数量。 |
| `top_k_multiply_factor` | int | 5 | 初始检索阶段使用的放大因子（初始候选数 = `top_k × top_k_multiply_factor`），用于提升召回率。 |
| `add_instruction` | bool | True | 是否在 LLM 生成检索查询时追加额外指令。<br>使用带指令前缀的 embedding 后端时开启，通常有助于召回与查询稳定性。 |
| `mode` | Literal["dense", "sparse", "hybrid"] | "hybrid" | 检索模式：<br> - `"dense"`：仅使用向量检索（Embedding-based）。<br> - `"sparse"`：仅使用稀疏检索
（如 BM25）。<br> - `"hybrid"`：融合向量检索与稀疏检索结果（推荐）。 |


**StateCreationAgentConfig**（`search_workflow.state_creation_agent`）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `log_fetch` | bool | False | 是否记录检索日志 |
| `log_search` | bool | False | 是否记录搜索日志 |
| `web_fetch_log_file` | str | "gnosis/tool_log/web_fetch_log.jsonl" | 检索日志文件路径 |
| `web_search_log_file` | str | "gnosis/tool_log/web_search_log.jsonl" | 搜索日志文件路径 |
| `use_candidate_strength` | bool | True | 是否使用候选强度，启用后会在 action 采样时考虑候选强度，提高搜索质量 |
| `discovered_clues_mode` | Literal["report", "blacklist"] | "blacklist" | 发现线索模式，`"report"`: 报告发现的线索；`"blacklist"`: 将发现的线索加入黑名单，避免重复探
索 |
| `max_llm_calls_per_run` | int | 100 | 单次 state creation 最大 LLM 调用数，控制单次 state creation 的 LLM 调用上限，防止无限循环 |
| `context_limit_reached_strategy` | Literal["fail", "reduced_retrieval_request", "delete_tool_responses", "delete_tool_input_and_responses"] | `"reduced_retrieval_request"` | run-action 时触达上下文上限后的策略；`reduced_retrieval_request` 会减半 `top_k` / `top_k_multiply_factor` 并重试（仅对检索工具有效） |
| `llm_config` | 见上 | `{}` | 按类别键配置的 LLM |
| `retrieval_settings` | RetrievalSettingsConfig | - | 检索设置（**不含** Milvus 连接信息；Milvus 在 `AgentConfig.search_workflow_milvus_config`） |
| `validator_agent` | ValidatorAgentConfig | - | 校验 Agent 配置 |

LLMConfig 的通用字段包括：`model_name`、`model_type`、`base_url`、`api_key`、`hyper_parameters`、`timeout`、`max_tries`、`append_think_tags_to_messages` 等，详见 `openjiuwen_deepsearch/config/config.py` 中的 `LLMConfig`。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model_name` | str | "" | 模型名称（必填） |
| `model_type` | Literal["openai", "siliconflow"] | "openai" | 模型类型（与 `main.py` CLI 说明一致） |
| `base_url` | str | "" | 模型服务地址 |
| `api_key` | bytearray | bytearray("") | 模型调用密钥（必填） |
| `hyper_parameters` | dict | {} | 模型调用超参数（如 temperature, max_tokens 等） |
| `extension` | dict | {} | 模型扩展配置项 |
| `timeout` | int | 600 | 请求超时时间（秒） |
| `max_tries` | int | 4 | 最大重试次数 |
| `append_think_tags_to_messages` | bool | False | 是否在消息中追加 think 标签 |

#### ServiceConfig 其余参数概览

完整字段与默认值请查看 `openjiuwen_deepsearch/config/config.py` 中的 `ServiceConfig` 类。

---

## 快速开始

### 安装依赖

```bash
# 在根目录下运行
pip install .
```

### 测试与覆盖率（search-mode）

```bash
# 默认：跳过需要真实 LLM / 联网的用例（CI 推荐）
pytest -q tests/search_agent -m "not llm"

# 运行全部 search_agent 测试（含 llm，需密钥与显式开关）
export RUN_LLM_TESTS=1
pytest -q tests/search_agent

# 仅运行 mock 编排层集成测试
pytest -q tests/search_agent -m integration

# 仅运行真实 LLM + search_fetch（需 OPENROUTER_API_KEY、JINA_API_KEY、SERPER_API_KEY）
# 全部 llm e2e（含 gpt_mini_stack + small_qwen）
RUN_LLM_TESTS=1 pytest -q tests/search_agent -m llm

# 仅跑 qwen 分支（benchmarking/qwen_config.json + small_qwen）；日志落到固定目录
export LLM_E2E_LOG_DIR="$PWD/tmp_llm_e2e_logs"
export RUN_LLM_TESTS=1
pytest -q tests/search_agent/test_llm_search_fetch_e2e.py -m llm -k "small_qwen"

# 带覆盖率报告（建议在 CI 使用，不含 llm）
pytest -q tests/search_agent -m "not llm" \
  --cov=openjiuwen_deepsearch \
  --cov-report=term-missing \
  --cov-fail-under=40
```

说明：
- `pytest-cov` 已在开发依赖中配置。
- `--cov-fail-under` 建议从较低阈值起步（如 `40`），随着测试完善逐步上调。
- 标记：`unit`（快速）、`integration`（mock 编排）、`llm`（真实 API）；`llm` 用例需 `RUN_LLM_TESTS=1` 且环境变量就绪，否则自动 skip。
- 可选：`LLM_E2E_LOG_DIR` 指向本机目录时，`test_llm_search_fetch_e2e` 会把日志写到 `该目录/<profile>/`（如 `gpt_mini_stack`、`small_qwen`）；不设则仍用 pytest 的 `tmp_path`。
- 仅 qwen 配置：对上述 e2e 文件加 `-k small_qwen`（匹配参数 `qwen_config.json` / `small_qwen`），见下方示例。

### 环境配置

按运行方式设置密钥（应用代码通常从 `AgentConfig` / CLI 读入，环境变量便于本地与 CI）：

```bash
# 通用：LLM 端点取决于你的部署（OpenAI 兼容网关等）
export OPENAI_API_KEY="your_openai_api_key"

# search_fetch：通过 AgentConfig 或 CLI 配置任一受支持的 web 搜索引擎及网页抓取 provider。
# 需要真实联网测试时，提供所选 provider 的密钥；密钥字段分别为
# web_search_engine_config.search_api_key 和 web_fetch_provider_config.api_key。

# retrieve：Embedding 密钥由 AgentConfig.search_workflow_milvus_config.embedder_api_key 提供；
# 若你本地脚本使用 EMBEDDER_API_KEY 命名，请在合并进 agent_config 时映射到 embedder_api_key
export EMBEDDER_API_KEY="your_embedder_api_key"
```

### 基本使用

```python
import asyncio
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.config.config import Config, AgentConfig

async def main():
    # 使用 AgentFactory 创建 agent
    agent_factory = AgentFactory()
    
    # 获取默认配置并修改
    agent_config = Config().agent_config.model_dump()
    agent_config["search_mode"] = "search"  # 设置为 search 模式
    agent_config["..."] = "..." # 自定义其他参数
    
    # 验证并创建 agent
    candidate_config = AgentConfig.model_validate(agent_config)
    agent_config = candidate_config.model_dump()
    agent = agent_factory.create_agent(agent_config)
    
    # 运行 agent
    query = "..."
    async for chunk in agent.run(
        message=query, 
        conversation_id="test_session_001",
        agent_config=agent_config
    ):
        print(chunk)

asyncio.run(main())
```

### 运行示例

两种自定义参数方式：

1. **在 `openjiuwen_deepsearch/config/config.py` 中更改默认值**：修改配置类的默认值  
2. **通过 `main.py` CLI**：`main.py` 会基于 `Config().agent_config.model_dump()` 再覆盖 LLM、search、Milvus 等参数

**retrieve 模式**：需先构建知识库索引（见下文），并在 **`AgentConfig.search_workflow_milvus_config`（`MilvusConfig`）** 中填写 Milvus 与 Embedder；**不要**在 `RetrievalSettingsConfig` 中配置 Milvus（该类型仅含 `top_k`、`mode` 等检索行为参数）。

**命令行（search 模式）**：`main.py` 要求提供完整 LLM 参数；`search_fetch` 时必须提供搜索引擎和抓取 provider 的名称及密钥。查询通过 **`--query`** 传入（可多个词，会拼接为空格分隔的一句）。

```bash
python -m main \
  --mode query \
  --search_mode search \
  --tool_map search_fetch \
  --query "你的问题" \
  --llm_model_name "your-model" \
  --llm_model_type openai \
  --llm_base_url "https://api.example.com/v1" \
  --llm_api_key "your-llm-key" \
  --web_search_engine_name "jina" \
  --web_search_api_key "your-search-key" \
  --fetch_provider_name "jina" \
  --fetch_api_key "your-fetch-key"
```

本仓库默认 **不包含** `scripts_will_be_deleted_later.run_8_queries` 等打榜批跑脚本；若你本地另有该模块，可按其 README 自行调用。

## BrowseComp-Plus 的 Milvus 索引构建

本项目提供了一整套工具，用于下载 **BrowseComp-Plus** 数据集，并将其索引到 **Milvus** 中，以支持深度研究（Deep-Research）场景下的基准测试。
整体流程包括：从 Hugging Face 下载并解密数据集，到创建支持 **混合检索（Dense + BM25）** 的 Milvus 可搜索集合。

---

### 1. 概览

本仓库用于处理 [BrowseComp-Plus](https://huggingface.co/datasets/Tevatron/browsecomp-plus) 基准的数据处理与索引构建，主要步骤：

* **解密（Decryption）**：从 Hugging Face 安全下载并去混淆数据集
* **处理（Processing）**：将长网页文档切分为适合检索的文本块（使用 `TokenizerChunker`）
* **索引（Indexing）**：将文档写入 Milvus，支持语义检索（Dense Vector）与 BM25 稀疏检索

#### 快速开始

1. 按照官方文档部署 Milvus：  
   [https://milvus.io/docs/install_standalone-docker.md](https://milvus.io/docs/install_standalone-docker.md)

2. 部署与 **`OpenJiuwenAPIEmbedder`** 兼容的 Embedding HTTP 服务（OpenAI 兼容 embeddings 接口即可）。

3. 在 **`openjiuwen_deepsearch/algorithm/search_index/create_browsecompplus_index.py`** 中通过**环境变量或文件顶部常量**配置（脚本优先读环境变量；**索引前须设置**）：
   * **必须**：`EMBED_API_URL`、`EMBED_API_KEY`、`EMBED_MODEL_NAME`、`HUGGINGFACE_MODEL_NAME`（为空则运行时报错）
   * **常用可选**：`DATA_LOCATION`、`MILVUS_URI`、`MILVUS_TOKEN`、`MILVUS_DB_NAME`、`MILVUS_COLLECTION_NAME`、`EMBED_TIMEOUT`、`BATCH_SIZE` 等（见下方配置说明）

4. 执行命令：

```bash
uv add datasets transformers
# 若有单独的数据集下载/解密脚本，请先执行（路径以仓库为准）
uv run -m openjiuwen_deepsearch.algorithm.search_index.create_browsecompplus_index
```

（数据文件需为解密后的 `browsecompplus.jsonl`，路径与脚本内 `DATA_LOCATION` 一致。）

---

#### 数据集结构

数据集中每条样本包含以下字段：

* `query_id`：查询的唯一标识
* `query`：需要复杂推理的问题文本
* `answer`：标准答案
* `evidence_docs`：用于回答问题的证据文档列表

  * `docid`、`text`、`url`
* `gold_docs`：包含最终答案的文档列表

  * `docid`、`text`、`url`
* `negative_docs`：用于挑战检索器的困难负样本文档

  * `docid`、`text`、`url`

---

### 2. 数据集说明

BrowseComp-Plus 是一个用于 **Deep-Research 系统** 的基准数据集，重点在于**隔离检索器（Retriever）与 LLM Agent 的影响**。
该数据集基于一个固定且精心筛选的约 **10 万篇网页文档** 语料进行评估。

数据集托管在 Hugging Face 上，并经过混淆处理，以防止数据泄露。

---

### 3. 安装

```bash
uv add datasets transformers pymilvus requests tqdm
```

此外需要：
* 正在运行的 Milvus 实例（参考 [Milvus 安装文档](https://milvus.io/docs/install_standalone-docker.md)）
* 已部署的、与 **`OpenJiuwenAPIEmbedder`** 兼容的 Embedding HTTP 服务（OpenAI 兼容 `/embeddings` 等）

---

### 4. 使用方法

#### 索引到 Milvus

在准备好解密后的数据文件后，配置 `DATA_LOCATION`、`EMBED_API_URL`、`EMBED_API_KEY` 及 `MILVUS_*` 等（见下表），然后执行：

```bash
uv run -m openjiuwen_deepsearch.algorithm.search_index.create_browsecompplus_index
```

该脚本会执行以下操作：

1. 连接 Milvus（默认 `MILVUS_URI`=`http://localhost:19530`，可与 `MILVUS_TOKEN` 等一并配置），按需创建数据库并切换
2. 使用 **`OpenJiuwenAPIEmbedder`** 初始化 Embedding 模型，需配置 `api_url`、`api_token`、`timeout` 及与索引一致的 `pretrained_model`
3. 加载 `DATA_LOCATION` 指定的 JSONL 文件（如 `browsecompplus.jsonl`）
4. 使用 `BrowseCompChunker`（基于 `TokenizerChunker`，最大 2048 token）对文档切块
5. 批量调用 Embedding API 生成向量，并将文本块与 BM25 稀疏向量写入 Milvus 集合（集合名由 `MILVUS_COLLECTION_NAME` 指定）

---

### 5. 配置说明

`openjiuwen_deepsearch/algorithm/search_index/create_browsecompplus_index.py` 中的变量由 **`_env("KEY", default)`** 从环境变量读取；未导出环境变量时使用脚本内默认值（**`EMBED_MODEL_NAME`、`HUGGINGFACE_MODEL_NAME` 无默认，须设置**）。下表与当前代码一致：

| 变量（环境变量名） | 说明 | 代码中典型默认 |
|------|------|----------------|
| `DATA_LOCATION` | 解密后的 JSONL 路径 | `browsecompplus.jsonl` |
| `MILVUS_URI` | Milvus 服务 URI | `http://localhost:19530` |
| `MILVUS_TOKEN` | Milvus 认证 token | `root:Milvus` |
| `MILVUS_DB_NAME` | Milvus 数据库名 | `deepsearch_benchmarks` |
| `MILVUS_COLLECTION_NAME` | Milvus 集合名 | `browsecompplus_with_bm25` |
| `HUGGINGFACE_MODEL_NAME` | 分词用 Hugging Face 模型 | `Qwen/Qwen3-Embedding-8B` |
| `EMBED_MODEL_NAME` | Embedding 模型 id（须与向量维度及服务端一致） | **空**（须通过环境变量设置） |
| `EMBED_API_URL` | Embedding 服务地址 | **空**（必须配置，否则运行报错） |
| `EMBED_API_KEY` | Embedding API 密钥 | **空**（必须配置，否则运行报错） |
| `EMBED_TIMEOUT` | 请求超时（秒） | `60` |
| `BATCH_SIZE` | 每批文档数 | `10` |
| `INDEX_MAX_RECORDS` | 仅索引前 N 条，`0` 表示不限制 | `0` |

---

### 6. 索引流程说明

`create_browsecompplus_index.py` 的 `if __name__ == "__main__":` 主流程步骤（与当前代码一致）：

1. **初始化 Milvus 客户端**  
   使用 `MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN, ...)` 连接；`uri`、`token` 来自环境变量或脚本顶部常量（见上表）。

2. **数据库管理**  
   检查 `MILVUS_DB_NAME` 是否存在，不存在则创建并 `using_database`。

3. **Embedding 模型初始化**  
   * 校验 `EMBED_API_URL`、`EMBED_API_KEY` 已填写（未填写则报错）。
   * 校验 `EMBED_MODEL_NAME`、`HUGGINGFACE_MODEL_NAME` 已填写（未填写则报错）。
   * 使用 `OpenJiuwenAPIEmbedder(pretrained_model=EMBED_MODEL_NAME, api_token=EMBED_API_KEY, api_url=EMBED_API_URL, timeout=...)` 创建编码器；若 `EMBED_TIMEOUT` 未设置或 ≤0，则使用 60 秒。  
   * 使用 `HUGGINGFACE_MODEL_NAME` 加载 Hugging Face tokenizer，用于 `BrowseCompChunker` 的 token 统计与切分（应与所用 embedding 的 tokenizer 对齐）。

4. **数据加载与解析**  
   * 通过 `read_jsonl(DATA_LOCATION)` 读取 JSONL。
   * 构建 `doc_id2doc`、`doc_id2gold_query_id`、`doc_id2evidence_query_id` 等映射。

5. **集合（Collection）配置**  
   * `setup_milvus_collection()`：定义 Schema（主键 `id`、稠密向量 `embedding`、`content`、稀疏向量 `content_sparse`、BM25 等），稠密向量用 `AUTOINDEX` + `COSINE`，稀疏用 `SPARSE_INVERTED_INDEX` + BM25 function。

6. **文档索引构建**  
   * 使用 `BrowseCompChunker` 对文档切块，`index_documents_milvus()` 批量调用 `encoder_model.encode()` 并写入 Milvus。

---

### 7. 编码器与指令调优（Instruction Tuning）

默认的 **`OpenJiuwenAPIEmbedder`** 在查询侧可对输入追加任务指令（见 `encode(..., is_query=True)`），部分 **instruction-tuned** 的向量模型在该模式下检索效果更好。

#### 工作机制说明

* **查询阶段（Query Time）**  
  在检索时，会在查询前添加指令，例如：  
  *“Given a web search query, retrieve relevant passages that answer the query”*  
  以帮助模型理解检索任务语义。

* **索引阶段（Indexing Time）**  
  在构建索引时，**必须关闭指令**，仅使用原始文档内容生成向量，以保证向量表示的稳定性和一致性。

代码中通过 `encode()` 方法的 `is_query` 参数控制该行为：

* `is_query=True`：自动添加任务指令
* `is_query=False`：直接编码原始文本（索引阶段默认）

---

### 8. Schema 详情

Milvus 集合包含以下字段（与 `create_browsecompplus_index.py` 中 `setup_milvus_collection()` 一致）：

* `id`：主键（字符串，格式：`{docid}__{chunk_idx}`）
* `embedding`：稠密向量字段，维度须与构建索引时所用 Embedding 模型一致，使用 `COSINE` 相似度
* `content`：原始文本块（用于 BM25 分析）
* `content_sparse`：由 Milvus 内置 BM25 函数生成的稀疏向量
* `docid`：原始文档 ID
* `title`、`authors`、`datetime`：从文档中抽取的元数据
* `gold_query_id` / `evidence_query_id`：该文档相关的查询 ID 列表
