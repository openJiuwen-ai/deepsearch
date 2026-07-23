# `openjiuwen_deepsearch.config.config`

## `LLMConfig`
```python
class openjiuwen_deepsearch.config.config.LLMConfig()
```
**LLMConfig** holds LLM endpoint and call settings.

**Fields**

- **model_name** (str, optional): Model id. Default `""`.
- **model_type** (`Literal["openai", "siliconflow"]`, optional): Backend type. Default `"openai"`.
- **base_url** (str, optional): API base URL. Default `""`.
- **api_key** (bytearray, optional): API key. Default empty `bytearray`.
- **hyper_parameters** (dict, optional): Extra generation parameters. Default `{}`.
- **extension** (dict, optional): Provider-specific extras. Default `{}`.
- **model_config** (`ConfigDict`, internal): Pydantic model config; `arbitrary_types_allowed=True`.
- **timeout** (int, optional): Request timeout in seconds. Default `600`.
- **max_tries** (int, optional): Maximum retry attempts for one LLM call. Default `4`.
- **append_think_tags_to_messages** (bool, optional): Whether to append think tags into messages. Default `False`.

**Examples**

```python
>>> from openjiuwen_deepsearch.config.config import LLMConfig
>>> llm_config = LLMConfig(
...     model_name="gpt-4",
...     model_type="openai",
...     base_url="https://api.openai.com/v1",
...     api_key=bytearray("your_api_key", encoding="utf-8"),
... )
>>> llm_config = LLMConfig()
>>> llm_config = LLMConfig(
...     extension={"extra_headers": {...}},  # e.g. OpenAI extra_headers / extra_body
... )
```

## `WebSearchEngineConfig`
```python
class openjiuwen_deepsearch.config.config.WebSearchEngineConfig()
```
**WebSearchEngineConfig** configures the web augmentation / search engine.

**Fields**

- **search_engine_name** (`Literal["tavily","google","xunfei","petal","custom","bocha","jina","perplexity","serper"]`, optional): Engine id. Default `"tavily"`.
- **search_api_key** (bytearray, optional): API key. Default empty.
- **search_url** (str, optional): Endpoint URL. Default `""`. Public engines may leave this empty and use built-in defaults.
- **max_web_search_results** (int, optional): Max hits, 1–10. Default `5`.
- **extension** (dict, optional): Engine-specific options. Default `{}`.
- **model_config** (`ConfigDict`, internal): Pydantic model config; `arbitrary_types_allowed=True`.

**Built-in engine notes**

- `google` / `serper`: routed to the Google/Serper wrapper.
- `tavily`: Tavily wrapper.
- `xunfei`: iFlytek wrapper.
- `petal`: Petal web augmentation wrapper.
- `bocha` / `perplexity`: harness `web_tools` adapter wrappers.
- `jina`: direct HTTP wrapper for Jina Search.
- `custom`: dynamically loaded external search tool.

**Examples**

```python
>>> from openjiuwen_deepsearch.config.config import WebSearchEngineConfig

>>> # Example 1: configure tavily with include_domains
>>> web_search_config = WebSearchEngineConfig(
...     search_engine_name="tavily",
...     search_api_key=bytearray("your_api_key", encoding="utf-8"),
...     max_web_search_results=8,
...     extension={
...         "include_domains": ["www.sz.gov.cn", "www.pku.edu.cn"]
...     }
... )
>>> print(web_search_config.search_engine_name, web_search_config.extension["include_domains"])
tavily ["www.sz.gov.cn", "www.pku.edu.cn"]

>>> # Example 2: configure jina with default search_url and locale options
>>> web_search_config = WebSearchEngineConfig(
...     search_engine_name="jina",
...     search_api_key=bytearray("your_jina_key", encoding="utf-8"),
...     search_url="",
...     extension={
...         "gl": "us",
...         "hl": "en",
...         "location": "San Francisco",
...         "page": 2,
...     },
... )
>>> # Example 3: configure bocha with harness extension options
>>> web_search_config = WebSearchEngineConfig(
...     search_engine_name="bocha",
...     search_api_key=bytearray("your_bocha_key", encoding="utf-8"),
...     extension={"timeout_seconds": 30, "fetch_webpage": True},
... )
```

**Notes**

- For `tavily`, `extension.include_domains` and `extension.exclude_domains` are forwarded to the Tavily search API to **prefer or exclude** specified sites; this is not a hard allowlist on the framework side. When relevant results are insufficient, Tavily may still return pages whose domains are outside the `include_domains` list.
- `jina` falls back to `https://s.jina.ai` when `search_url` is empty. In China network environments where that endpoint is unreachable, set `search_url="https://s.jinaai.cn"`; you can also set `search_url` for privately deployed or proxy-forwarded endpoints.
- `bocha` and `perplexity` honor `search_url` only when the underlying harness provider supports URL override. In China network environments where the default Perplexity service is unreachable, configure an accessible proxy or forwarding endpoint and explicitly set it through `search_url`.
- Search results are normalized before collector-side storage so aliases like `link`, `source_url`, `snippet`, `summary`, and `answer` are mapped into the common `title` / `url` / `content` / `type` shape.
- Prefetched webpage content and the later collector evaluation input are both bounded by `MAX_COLLECTOR_DOC_CONTENT_LENGTH` to prevent oversized search payloads from reaching downstream LLM evaluation unchanged.

## `EmbedModelConfig`
```python
class openjiuwen_deepsearch.config.config.EmbedModelConfig()
```
**EmbedModelConfig** configures embedding for native local KB.

**Fields**: **model_name**, **api_key**, **base_url**, **max_batch_size** (required); **timeout** (default `60`); **max_retries** (default `3`).
- **model_config** (`ConfigDict`, internal): Pydantic model config; `arbitrary_types_allowed=True`.

## `VectorStoreConfig`
```python
class openjiuwen_deepsearch.config.config.VectorStoreConfig()
```
**VectorStoreConfig**: **uri**, **token**, **collection_name** (all required).

## `NativeKnowledgeBaseConfig`
```python
class openjiuwen_deepsearch.config.config.NativeKnowledgeBaseConfig()
```
**NativeKnowledgeBaseConfig**: **id** (required); **index_type** (default `"vector"`); **embed_model_config**; **vector_store**.

## `LocalSearchEngineConfig`
```python
class openjiuwen_deepsearch.config.config.LocalSearchEngineConfig()
```
**LocalSearchEngineConfig** configures local / KB search.

**Fields**

- **search_engine_name** (`openapi` / `custom` / `native`, optional). Default `openapi`.
- **search_api_key**, **search_url**, **search_datasets**, **extension**.
- **max_local_search_results** (1–10, default `5`).
- **recall_threshold** (default `0.5`).
- **search_mode** (`doc` semantic / `keyword` / `mix`, default `doc`).
- **knowledge_base_type** (`internal` / `external`, default `internal`).
- **source** (`KooSearch` / `LakeSearch`, default `KooSearch`).
- **knowledge_base_configs** (`List[NativeKnowledgeBaseConfig]`, default `[]`).
- **model_config** (`ConfigDict`, internal): Pydantic model config; `arbitrary_types_allowed=True`.

## `CustomWebSearchConfig` / `CustomLocalSearchConfig`
Custom tool hooks: **custom_*_file**, **custom_*_func**, **extension** (defaults empty).

## `AgentConfig`
```python
class openjiuwen_deepsearch.config.config.AgentConfig()
```
**AgentConfig** is the user-facing agent/runtime toggle set.

**Fields**:

- **execute_mode** (Literal["commercial", "general"], optional): Execution mode. Default value: `"commercial"`.
- **execution_method** (Literal["dependency_driving", "parallel", "hybrid"], optional): Execution method. `parallel` runs the parallel research workflow, `dependency_driving` runs the dependency-driven workflow, and `hybrid` lets `IntentRecognitionNode` call an LLM router to choose the outline branch for the current query. Default value: `"parallel"`.
- **workflow_human_in_the_loop** (bool, optional): Whether to enable HITL before planning. Default value: `True`.
- **outliner_max_section_num** (int, optional): Maximum number of outline sections. Range: `[1, 15]`. Default value: `10`.
- **outline_interaction_enabled** (bool, optional): Whether to enable outline interaction. Default value: `True`.
- **outline_interaction_max_rounds** (int, optional): Maximum number of outline interaction rounds. Range: `[1, 100]`. Default value: `3`.
- **source_tracer_research_trace_source_switch** (bool, optional): Whether to enable citation tracing. Default value: `True`.
- **source_tracer_generated_citation_switch** (bool, optional): Whether to generate new citations from search results. When disabled, the system keeps only citations already present in the original report. Default value: `True`.
- **source_tracer_infer_switch** (bool, optional): Whether to enable provenance reasoning. Default value: `True`.
- **llm_config** (Dict[Literal["general", "plan_understanding", "info_collecting", "writing_checking", "vlm_chart_generating"], LLMConfig], optional): LLM model configuration. Default value: `dict()`.
- **info_collector_search_method** (Literal["web", "local", "all"], optional): Search method. `web` means web augmentation search, `local` means local search tool, and `all` means hybrid web + local search. Default value: `"web"`.
- **info_collector_webpage_enrich_enable** (bool, optional): Whether to enable webpage content enrichment during DeepResearch information collection. Default value: `False`.
- **web_search_engine_config** (WebSearchEngineConfig, optional): Web augmentation engine configuration. Default value: `WebSearchEngineConfig()`.
- **local_search_engine_config** (LocalSearchEngineConfig, optional): Local search engine configuration. Default value: `LocalSearchEngineConfig()`.
- **custom_web_search_config** (CustomWebSearchConfig, optional): Custom web augmentation engine configuration. Default value: `CustomWebSearchConfig()`.
- **custom_local_search_config** (CustomLocalSearchConfig, optional): Custom local search configuration. Default value: `CustomLocalSearchConfig()`.
- **search_mode** (`Literal["research", "search", "react"]`, optional): Agent operating mode. `research` for report generation workflow, `search` for DeepSearch graph, `react` for simple ReAct + search tools. Default value: `"research"`.
- **enable_question_router** (bool, optional): When `True` and `search_mode="search"`, route simple questions to `react` and complex ones to DeepSearch. Default value: `False`.
- **search_workflow_per_question_params** (`PerQuestionParams`, optional): Per-question control knobs for search/react runs (time, workers, tool map, limits, etc.). Default value: `PerQuestionParams()`.
- **search_workflow_milvus_config** (`MilvusConfig`, optional): Milvus/embedder settings used when retrieval tool path is selected. Default value: `MilvusConfig()`.
- **web_fetch_provider_config** (`WebFetchProviderConfig`, optional): Explicit DeepSearch fetch-provider config. Current v1 requires `provider_name="jina"` to enable `web_fetch`. Default value: `WebFetchProviderConfig()`.
- **model_config** (`ConfigDict`, internal): Pydantic model config; `arbitrary_types_allowed=True`.
- **web_search_max_qps** (float, optional): Maximum QPS for the web augmentation engine. `0` means no rate limit. Floating-point values such as `0.5` are supported and mean one request every 2 seconds. Default value: `0`.
- **user_feedback_processor_enable** (bool, optional): Whether to enable post-report local optimization. Default value: `False`.
- **user_feedback_processor_max_interactions** (int, optional): Maximum number of local optimization interactions. Default value: `100`. Allowed range: `1~100`.
- **stats_info_llm** (bool, optional): Whether to collect LLM call statistics. Default value: `False`.
- **api_tools_config** (`ApiToolsConfig`): runtime HTTP API tools injected for function calling outside built-in tools.
- **vlm_chart_generator_enable** (bool, optional): VLM iterative chart generation toggle; mutually exclusive with `visualization_enable`.
- **vlm_chart_generator_max_iterations** (int, optional): Max iterations for VLM chart optimization. Default `1`, range 1–3. Higher values increase latency.
- **agent_llm_timeouts** (`Dict[str, int]`, optional): business-layer wall-clock timeout rules for full LLM calls, matched by exact `agent_name`, then node-level prefix key, then `default`. The feature is active only when the dict is non-empty and contains `default`; a matched value of `0` disables the outer wall-clock timeout for that rule. Default value: `dict()`.

**Notes**:

- `DeepSearchRequest.agent_llm_timeouts` is passed through into `AgentConfig.agent_llm_timeouts` and then merged into runtime session config.
- This setting controls the outer business-layer timeout for the whole streaming call; it does not replace provider/request-level timeout handling.
- For the current configurable `agent_name` / key list, see the `AgentLlmName` definition in `openjiuwen_deepsearch/utils/constants_utils/node_constants.py`; `default` is the fallback rule.

**Example**:

```python
>>> from openjiuwen_deepsearch.config.config import AgentConfig, LLMConfig, WebSearchEngineConfig
>>> agent_config = AgentConfig(
...     execute_mode="general",
...     execution_method="parallel",
...     llm_config={"general": LLMConfig(model_name="gpt-4", model_type="openai"),
...                 "plan_understanding": LLMConfig(model_name="qwen3-max", model_type="openai")},
...     web_search_engine_config=WebSearchEngineConfig(search_engine_name="petal"),
...     info_collector_search_method="all",
... )
```

## `ApiToolsConfig`
```python
class openjiuwen_deepsearch.config.runtime_api_models.ApiToolsConfig()
```
**ApiToolsConfig** describes runtime HTTP tools injected into the workflow via **`AgentConfig.api_tools_config`**.

**Fields**

- **query_understanding_tools** (`List[RuntimeApiToolConfig]`, optional): tools used in planner/outliner stages.
- **collector_tools** (`List[RuntimeApiToolConfig]`, optional): tools used in collector stages.

If tools are passed from the HTTP API with **`DeepSearchRequest.tools`**, the server normalizes them and fills both lists with the same normalized tool definitions.

## `RuntimeApiToolConfig`
```python
class openjiuwen_deepsearch.config.runtime_api_models.RuntimeApiToolConfig()
```
**RuntimeApiToolConfig** defines how one HTTP API tool is exposed to model function calling.

**Key fields**

- **tool_id**, **name**, **description**: tool identity and display metadata.
- **base_url**, **path**, **http_method**: request target and HTTP verb (`path` can also be a full URL).
- **headers**: default request headers.
- **request_params**: parameter list with routing (`send_method`: `header` / `query` / `body` / `none`), required flag, type, and default.
- **response_wrapper**: optional response shape adapter (for example `search_result` in collector flows).
- **response_params**: compatibility field retained in config; not used for the current response mapping pipeline.

**Example: configurable runtime function-call tool**

```python
from openjiuwen_deepsearch.config.config import AgentConfig
from openjiuwen_deepsearch.config.runtime_api_models import (
    ApiToolsConfig,
    RuntimeApiToolConfig,
    RuntimeApiToolParamConfig,
)

company_profile_tool = RuntimeApiToolConfig(
    tool_id="company_profile",
    name="company_profile",
    description="Fetch company profile by ticker symbol.",
    base_url="https://api.example.com",
    path="/v1/company/profile",
    http_method="get",
    request_params=[
        RuntimeApiToolParamConfig(
            key="symbol",
            description="Ticker symbol, e.g. AAPL",
            required=True,
            send_method="query",
            param_type="string",
        ),
        RuntimeApiToolParamConfig(
            key="x-api-key",
            description="API key for upstream service",
            required=False,
            send_method="header",
            default_value="",
            param_type="string",
        ),
    ],
    response_wrapper="search_result",
)

agent_config = AgentConfig(
    api_tools_config=ApiToolsConfig(
        query_understanding_tools=[company_profile_tool],
        collector_tools=[company_profile_tool],
    )
)
```

## `ServiceConfig`
```python
class openjiuwen_deepsearch.config.config.ServiceConfig()
```
**ServiceConfig** holds SDK/service defaults (timeouts, retries, telemetry).

**Fields**:

### Basic service settings
- **service_allow_origins** (List[str], optional): Allowed IP/CORS origin range. Default value: `[]`.

### Template parameters
- **template_max_generate_retry_num** (int, optional): Maximum retry count for template generation. Default value: `3`.

### Workflow parameters
- **workflow_execution_timeout** (int, optional): Workflow execution timeout in seconds. Default value: `7200`.
- **workflow_sub_graph_execution_timeout** (int, optional): Subgraph execution timeout. Default value: `6000`.
- **workflow_max_plan_executed_num** (int, optional): Maximum number of executed plans. Default value: `2`.
- **workflow_recursion_limit** (int, optional): Recursion limit. Default value: `30`.
- **workflow_max_gen_question_retry_num** (int, optional): Maximum retry count for question generation. Default value: `3`.
- **workflow_feedback_mode** (str, optional): User feedback channel. Available values: `["web", "cmd"]`. Default value: `"web"`.

### Outline node parameters
- **outliner_max_generate_outline_retry_num** (int, optional): Maximum retry count for outline generation. Default value: `3`.

### Planner node parameters
- **planner_max_step_num** (int, optional): Maximum number of planner steps. Default value: `3`.
- **planner_max_retry_num** (int, optional): Maximum retry count. Default value: `3`.

### Information collection parameters
- **info_collector_max_search_query_count** (int, optional): Maximum number of search queries in one collector loop. Default value: `5`.
- **info_collector_max_research_loops** (int, optional): Maximum number of research loops. Default value: `2`.
- **info_collector_max_tool_call_turns_per_query** (int, optional): Maximum tool-call turns for each collector query. Default value: `2`.
- **info_collector_max_retry_num** (int, optional): Maximum retry count. Default value: `3`.
- **info_collector_webpage_enrich_max_urls** (int, optional): Maximum number of URLs to fetch and enrich per collector loop. Default value: `3`.
- **info_collector_webpage_enrich_fetch_timeout_seconds** (int, optional): Timeout in seconds for fetching one webpage during webpage enrichment. Default value: `45`.

### Reporting parameters
- **sub_report_classify_doc_infos_res_top_k_num** (int, optional): Top-k number returned by the LLM classification in one pass for a sub-report. Default value: `20`.
- **report_max_generate_retry_num** (int, optional): Maximum retry count for content generation. Default value: `3`.
- **visualization_enable** (bool, optional): Whether to enable visualization illustrations in reports. Default value: `False`.

### Provenance parameters
- **source_tracer_citation_verify_max_concurrency_num** (int, optional): Maximum concurrency for citation verification. Default value: `30`.
- **source_tracer_citation_verify_batch_size** (int, optional): Batch size for citation verification. Default value: `1`.

### Statistics parameters
- **stats_info_node_duration** (bool, optional): Whether to collect node duration statistics. Default value: `False`.
- **stats_info_search** (bool, optional): Whether to collect search tool call statistics. Default value: `False`.

### LLM timeout parameter
- **llm_timeout** (int, optional): provider/request-level LLM call timeout in seconds. Default value: `300`.

**Notes**:

- `service_config.llm_timeout` is still passed to the underlying LLM client.
- If `agent_llm_timeouts` is also configured, DeepSearch adds an outer wall-clock timeout around the whole streaming call; when that outer timeout is hit, it raises `LLM_WALL_CLOCK_TIMEOUT` (`211204`).

### LLM thinking mode parameter
- **llm_thinking_enabled** (bool, optional): whether to enable model thinking mode. Default value: `False`. This SDK-internal setting is applied only when `DeepresearchAgent` initializes LLMs and injects provider-specific thinking on/off parameters for supported providers; `DeepSearchAgent`, `SimpleReactSearchAgent`, and REST API requests do not use this field.

**Notes**:

- `service_config.llm_thinking_enabled` is not an `LLMConfig` field and is not exposed as a REST API request parameter. For providers that do not support a thinking switch, the runtime only logs a warning and keeps the original extension parameters.
- If thinking-related fields are manually configured in `LLMConfig.extension`, the internal unified thinking switch overrides those fields and writes a warning log.

### Debug parameters
- **node_debug_enable** (bool, optional): Whether to enable formatted node debug logs. Default value: `False`.
- **export_intermediate_results** (bool, optional): Whether to export intermediate workflow results for visualization. Default value: `False`.

## `Config`
```python
class openjiuwen_deepsearch.config.config.Config()
```
**Config** bundles **agent_config** (`AgentConfig()`) and **service_config** (`ServiceConfig()`).

```python
>>> from openjiuwen_deepsearch.config.config import Config, AgentConfig, ServiceConfig
>>> Config(agent_config=AgentConfig(execute_mode="general"),
...        service_config=ServiceConfig(workflow_execution_timeout=3600))
>>> Config()
```
