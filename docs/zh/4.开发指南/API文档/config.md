# openjiuwen_deepsearch.config.config

## class openjiuwen_deepsearch.config.config.LLMConfig
```python
class openjiuwen_deepsearch.config.config.LLMConfig()
```
**LLMConfig**是LLM模型配置类，用于配置大语言模型的参数。

**字段**：

- **model_name**(str, 可选)：模型名称。默认值：`""`。
- **model_type**(Literal["openai", "siliconflow"], 可选)：模型类型。默认值：`"openai"`。
- **base_url**(str, 可选)：模型服务地址。默认值：`""`。
- **api_key**(bytearray, 可选)：模型调用密钥。默认值：`bytearray("", encoding="utf-8")`。
- **hyper_parameters**(dict, 可选)：模型调用超参数设置，根据具体模型接口设置。默认值：`{}`。
- **extension**(dict, 可选)：模型扩展配置项，根据具体模型接口设置。默认值：`{}`。
- **model_config**(ConfigDict，内部配置)：Pydantic 模型配置；`arbitrary_types_allowed=True`。
- **timeout**(int, 可选)：请求超时时间（秒）。默认值：`600`。
- **max_tries**(int, 可选)：单次调用最大重试次数。默认值：`4`。
- **append_think_tags_to_messages**(bool, 可选)：是否在消息中追加 think 标签。默认值：`False`。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import LLMConfig

>>> # 样例1：创建LLM配置
>>> llm_config = LLMConfig(
...     model_name="gpt-4",
...     model_type="openai",
...     base_url="https://api.openai.com/v1",
...     api_key=bytearray("your_api_key", encoding="utf-8")
... )
>>> print(llm_config.model_name, llm_config.model_type)
gpt-4 openai

>>> # 样例2：使用默认配置
>>> llm_config = LLMConfig()
>>> print(llm_config.model_name)

>>> # 样例3：使用extension配置
>>> llm_config = LLMConfig(
...     model_name="gpt-4",
...     model_type="openai",
...     base_url="https://api.openai.com/v1",
...     api_key=bytearray("your_api_key", encoding="utf-8"),
...     extension={
            'extra_headers': {...} # 假设使用openai的extra_headers参数，还支持extra_body等
...     }
... )

```

## class openjiuwen_deepsearch.config.config.WebSearchEngineConfig
```python
class openjiuwen_deepsearch.config.config.WebSearchEngineConfig()
```
**WebSearchEngineConfig**是联网增强引擎配置类，用于配置网络搜索相关的参数。

**字段**：

- **search_engine_name**(Literal["tavily", "google", "xunfei", "petal", "custom", "bocha", "jina", "perplexity", "serper"], 可选)：联网增强引擎名称。默认值：`"tavily"`。
- **search_api_key**(bytearray, 可选)：联网增强引擎调用密钥。默认值：`bytearray("", encoding="utf-8")`。
- **search_url**(str, 可选)：联网增强引擎调用地址。默认值：`""`。对于支持默认公网地址的引擎，可留空由系统自动回退。
- **max_web_search_results**(int, 可选)：最大搜索结果数量，取值范围：[1, 10]。默认值：`5`。
- **extension**(dict, 可选)：联网增强引擎扩展配置项，根据具体联网增强引擎接口设置。默认值：`{}`。
- **model_config**(ConfigDict，内部配置)：Pydantic 模型配置；`arbitrary_types_allowed=True`。

**内置引擎说明**：

- `google` / `serper`：复用 GoogleSearchAPIWrapper。
- `tavily`：使用 TavilySearchAPIWrapper。
- `xunfei`：使用讯飞搜索 Wrapper。
- `petal`：使用小艺联网增强 Wrapper。
- `bocha` / `perplexity`：使用 harness `web_tools` 适配层。
- `jina`：使用直接 HTTP 调用的 JinaSearchAPIWrapper。
- `custom`：通过 `CustomWebSearchConfig` 动态加载。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import WebSearchEngineConfig

>>> # 样例1：创建联网增强引擎配置
>>> web_search_config = WebSearchEngineConfig(
...     search_engine_name="petal",
...     search_api_key=bytearray("your_api_key", encoding="utf-8"),
...     max_web_search_results=5
... )
>>> print(web_search_config.search_engine_name, web_search_config.max_web_search_results)
petal 5

>>> # 样例2：使用默认配置
>>> web_search_config = WebSearchEngineConfig()
>>> print(web_search_config.search_engine_name)
tavily

>>> # 样例3：配置 tavily 并指定搜索域名
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

>>> # 样例4：配置 jina，使用默认 search_url 并携带搜索区域参数
>>> web_search_config = WebSearchEngineConfig(
...     search_engine_name="jina",
...     search_api_key=bytearray("your_jina_key", encoding="utf-8"),
...     search_url="",
...     extension={
...         "gl": "us",
...         "hl": "en",
...         "location": "San Francisco",
...         "page": 2
...     }
... )
>>> print(web_search_config.search_engine_name, web_search_config.search_url)
jina

>>> # 样例5：配置 bocha，使用 harness 扩展参数
>>> web_search_config = WebSearchEngineConfig(
...     search_engine_name="bocha",
...     search_api_key=bytearray("your_bocha_key", encoding="utf-8"),
...     extension={
...         "timeout_seconds": 30,
...         "fetch_webpage": True
...     }
... )
>>> print(web_search_config.search_engine_name, web_search_config.extension["timeout_seconds"])
bocha 30
```

**补充说明**：

- `tavily` 的 `extension.include_domains` / `extension.exclude_domains` 会透传给 Tavily 检索接口，用于**偏好或排除**指定站点，并非框架侧的硬性白名单。Tavily 在相关结果不足时仍可能返回`include_domains`列表外域名的页面。
- `jina` 的 `search_url` 为空时，会自动回退到 `https://s.jina.ai`；国内网络环境如无法访问该默认地址，可显式配置 `search_url="https://s.jinaai.cn"`，也可填入私有化部署或代理转发地址。
- `bocha`、`perplexity` 通过 harness `web_tools` 适配层访问搜索能力，仅当底层 provider 支持地址覆盖时，`search_url` 才会生效。国内网络环境如无法访问 Perplexity 默认服务，需要配置可访问的代理或转发地址，并通过 `search_url` 显式覆盖。
- `web_search_tool` 返回结果进入 Collector 前会统一归一化为 `title`、`url`、`content`、`type` 等字段；字段别名如 `link`、`source_url`、`snippet`、`summary`、`answer` 会在 Collector 中兼容处理。
- 预抓取网页正文以及最终进入 `run_doc_evaluation` 的内容都会按 `MAX_COLLECTOR_DOC_CONTENT_LENGTH` 进行裁剪，以限制网页搜索结果对后续评估链路的上下文占用。

## class openjiuwen_deepsearch.config.config.EmbedModelConfig
```python
class openjiuwen_deepsearch.config.config.EmbedModelConfig()
```
**EmbedModelConfig**是原生本地知识库 Embedding 模型配置类。

**字段**：

- **model_name**(str, 必填)：Embedding模型名称。
- **api_key**(bytearray, 必填)：Embedding模型密钥。
- **base_url**(str, 必填)：接口地址。
- **max_batch_size**(int, 必填)：最大批次大小。
- **timeout**(int, 可选)：请求超时时间。默认值：`60`。
- **max_retries**(int, 可选)：最大重试次数。默认值：`3`。
- **model_config**(ConfigDict，内部配置)：Pydantic 模型配置；`arbitrary_types_allowed=True`。

## class openjiuwen_deepsearch.config.config.VectorStoreConfig
```python
class openjiuwen_deepsearch.config.config.VectorStoreConfig()
```
**VectorStoreConfig**是向量数据库连接配置类。

**字段**：

- **uri**(str, 必填)：向量数据库连接地址。
- **token**(str, 必填)：连接令牌。
- **collection_name**(str, 必填)：集合名称。

## class openjiuwen_deepsearch.config.config.NativeKnowledgeBaseConfig
```python
class openjiuwen_deepsearch.config.config.NativeKnowledgeBaseConfig()
```
**NativeKnowledgeBaseConfig**是原生本地知识库配置类。

**字段**：

- **id**(str, 必填)：知识库 ID。
- **index_type**(Literal["vector"], 可选)：索引类型。默认值：`"vector"`。
- **embed_model_config**(EmbedModelConfig, 必填)：Embedding模型配置。
- **vector_store**(VectorStoreConfig, 必填)：向量库配置。

## class openjiuwen_deepsearch.config.config.LocalSearchEngineConfig
```python
class openjiuwen_deepsearch.config.config.LocalSearchEngineConfig()
```
**LocalSearchEngineConfig**是本地搜索引擎配置类，用于配置本地搜索相关的参数。

**字段**：

- **search_engine_name**(Literal["openapi", "custom", "native"], 可选)：本地搜索引擎名称。默认值：`"openapi"`。
- **search_api_key**(bytearray, 可选)：本地搜索引擎调用密钥。默认值：`bytearray("", encoding="utf-8")`。
- **search_url**(str, 可选)：本地搜索引擎调用地址。默认值：`""`。
- **search_datasets**(list, 可选)：本地搜索引擎数据集配置。默认值：`[]`。
- **max_local_search_results**(int, 可选)：最大本地搜索结果数量，取值范围：[1, 10]。默认值：`5`。
- **recall_threshold**(float, 可选)：本地搜索文档召回相似度阈值。默认值：`0.5`。
- **search_mode**(Literal["doc", "keyword", "mix"], 可选)：检索策略模式，`doc`：语义检索，`keyword`：关键词检索，`mix`：混合检索。默认值：`"doc"`。
- **knowledge_base_type**(Literal["internal", "external"], 可选)：知识库类型。默认值：`"internal"`。
- **source**(Literal["KooSearch", "LakeSearch"], 可选)：知识库来源。默认值：`"KooSearch"`。
- **extension**(dict, 可选)：本地搜索引擎扩展配置项，根据具体搜索引擎接口设置。默认值：`{}`。
- **knowledge_base_configs**(List[NativeKnowledgeBaseConfig], 可选)：原生本地知识库配置。默认值：`[]`。
- **model_config**(ConfigDict，内部配置)：Pydantic 模型配置；`arbitrary_types_allowed=True`。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import LocalSearchEngineConfig

>>> # 样例1：创建本地搜索引擎配置
>>> local_search_config = LocalSearchEngineConfig(
...     search_engine_name="openapi",
...     search_api_key=bytearray("your_api_key", encoding="utf-8"),
...     max_local_search_results=5,
...     search_mode="mix",
...     recall_threshold=0.6
... )
>>> print(local_search_config.search_engine_name, local_search_config.search_mode)
openapi mix

>>> # 样例2：使用默认配置
>>> local_search_config = LocalSearchEngineConfig()
>>> print(local_search_config.search_engine_name, local_search_config.search_mode)
openapi doc
```

## class openjiuwen_deepsearch.config.config.CustomWebSearchConfig
```python
class openjiuwen_deepsearch.config.config.CustomWebSearchConfig()
```
**CustomWebSearchConfig**是自定义联网增强搜索配置类，用于配置自定义联网增强搜索工具的参数。

**字段**：

- **custom_web_search_file**(str, 可选)：自定义联网增强搜索工具文件路径。默认值：`""`。
- **custom_web_search_func**(str, 可选)：自定义联网增强搜索工具函数名称。默认值：`""`。
- **extension**(dict, 可选)：自定义联网增强搜索工具扩展配置项，根据具体联网增强引擎接口设置。默认值：`{}`。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import CustomWebSearchConfig

>>> # 样例1：创建自定义网络搜索配置
>>> custom_web_config = CustomWebSearchConfig(
...     custom_web_search_file="/path/to/custom_search.py",
...     custom_web_search_func="custom_web_search"
... )
>>> print(custom_web_config.custom_web_search_file, custom_web_config.custom_web_search_func)
/path/to/custom_search.py custom_web_search

>>> # 样例2：使用默认配置
>>> custom_web_config = CustomWebSearchConfig()
>>> print(custom_web_config.custom_web_search_file)
```


## class openjiuwen_deepsearch.config.config.CustomLocalSearchConfig
```python
class openjiuwen_deepsearch.config.config.CustomLocalSearchConfig()
```
**CustomLocalSearchConfig**是自定义本地搜索配置类，用于配置自定义本地搜索工具的参数。

**字段**：

- **custom_local_search_file**(str, 可选)：自定义本地搜索工具文件路径。默认值：`""`。
- **custom_local_search_func**(str, 可选)：自定义本地搜索工具函数名称。默认值：`""`。
- **extension**(dict, 可选)：自定义本地搜索工具扩展配置项，根据具体搜索引擎接口设置。默认值：`{}`。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import CustomLocalSearchConfig

>>> # 样例1：创建自定义本地搜索配置
>>> custom_local_config = CustomLocalSearchConfig(
...     custom_local_search_file="/path/to/custom_local_search.py",
...     custom_local_search_func="custom_local_search"
... )
>>> print(custom_local_config.custom_local_search_file, custom_local_config.custom_local_search_func)
/path/to/custom_local_search.py custom_local_search

>>> # 样例2：使用默认配置
>>> custom_local_config = CustomLocalSearchConfig()
>>> print(custom_local_config.custom_local_search_file)
```

## class openjiuwen_deepsearch.config.config.AgentConfig
```python
class openjiuwen_deepsearch.config.config.AgentConfig()
```
**AgentConfig**是Agent配置类，用于配置Agent的执行模式和参数。

**字段**：

- **execute_mode**(Literal["commercial", "general"], 可选)：执行模式，可选值：`["commercial", "general"]`。默认值：`"commercial"`。
- **execution_method**(Literal["dependency_driving", "parallel", "hybrid"], 可选)：执行方法，`parallel`：并行工作流执行，`dependency_driving`：依赖驱动工作流执行，`hybrid`：混合大纲路由模式，由意图识别节点调用 LLM 选择普通大纲或依赖驱动大纲。默认值：`"parallel"`。
- **workflow_human_in_the_loop**(bool, 可选)：工作流是否启用人机交互。默认值：`True`。
- **outliner_max_section_num**(int, 可选)：最大规划章节数量，取值范围：[1, 15]。默认值：`10`。
- **outline_interaction_enabled**(bool, 可选)：大纲交互功能开关，开启后用户可对生成的大纲进行多轮修改。默认值：`True`。
- **outline_interaction_max_rounds**(int, 可选)：大纲交互最大轮次，取值范围：[1, 100]。默认值：`3`。
- **source_tracer_research_trace_source_switch**(bool, 可选)：溯源功能开关。默认值：`True`。
- **source_tracer_generated_citation_switch**(bool, 可选)：新增引用生成开关；关闭时仅保留原文已有引用，不基于搜索结果生成新引用。默认值：`True`。
- **source_tracer_infer_switch**(bool, 可选)：溯源推理功能开关。默认值：`True`。
- **llm_config**(Dict[Literal["general", "plan_understanding", "info_collecting", "writing_checking", "vlm_chart_generating"], LLMConfig], 可选)：LLM模型配置。默认值：`dict()`。
- **info_collector_search_method**(Literal["web", "local", "all"], 可选)：搜索方式，`web`：联网增强搜索，`local`：本地搜索工具搜索，`all`：联网增强+本地融合搜索。默认值：`"web"`。
- **info_collector_webpage_enrich_enable**(bool, 可选)：DeepResearch 信息收集阶段是否启用网页正文增强节点。默认值：`False`。
- **web_search_engine_config**(WebSearchEngineConfig, 可选)：联网增强引擎配置。默认值：`WebSearchEngineConfig()`。
- **local_search_engine_config**(LocalSearchEngineConfig, 可选)：本地搜索引擎配置。默认值：`LocalSearchEngineConfig()`。
- **custom_web_search_config**(CustomWebSearchConfig, 可选)：自定义联网增强引擎配置。默认值：`CustomWebSearchConfig()`。
- **custom_local_search_config**(CustomLocalSearchConfig, 可选)：自定义本地搜索配置。默认值：`CustomLocalSearchConfig()`。
- **search_mode**(Literal["research", "search", "react"], 可选)：运行模式。`research`：研究报告主流程，`search`：DeepSearch 图流程，`react`：简单 ReAct + 与 search 相同工具。默认值：`"research"`。
- **enable_question_router**(bool, 可选)：当为 `True` 且 `search_mode="search"` 时，先经 LLM 路由简单问题到 `react`、复杂问题到 DeepSearch。默认值：`False`。
- **search_workflow_per_question_params**(PerQuestionParams, 可选)：search/react 单问题控制参数（时间、并发、工具映射、上限等）。默认值：`PerQuestionParams()`。
- **search_workflow_milvus_config**(MilvusConfig, 可选)：`retrieve` 工具路径使用的 Milvus/Embedding 配置。默认值：`MilvusConfig()`。
- **web_fetch_provider_config**(WebFetchProviderConfig, 可选)：显式 DeepSearch 抓取 provider 配置。当前 v1 需设置 `provider_name="jina"` 才能启用 `web_fetch`。默认值：`WebFetchProviderConfig()`。
- **model_config**(ConfigDict，内部配置)：Pydantic 模型配置；`arbitrary_types_allowed=True`。
- **web_search_max_qps**(float, 可选)：联网增强引擎最大 QPS，0 表示不限流，支持浮点数如 0.5 表示每 2 秒 1 个请求。默认值：`0`。
- **user_feedback_processor_enable**(bool, 可选)：是否启用报告生成后的局部优化能力。默认值：`False`。
- **user_feedback_processor_max_interactions**(int, 可选)：局部优化最大交互次数。默认值：`100`,可设置范围为1~100。
- **stats_info_llm**(bool, 可选)：LLM调用统计开关。默认值：`False`。
- **api_tools_config**(ApiToolsConfig，可选)：运行时 HTTP API 工具配置，用于在默认工具之外注入可调用工具。默认值：`ApiToolsConfig`。
- **vlm_chart_generator_enable**(bool, 可选)：vlm迭代生成图功能开关，与`visualization_enable`功能互斥。
- **vlm_chart_generator_max_iterations**(int, 可选)：vlm生成图迭代优化最大次数。默认值：`1`，可设置范围为1~3，数值越大，耗时越长。
- **agent_llm_timeouts**(Dict[str, int], 可选)：按 `agent_name` / 节点级 key 配置的 LLM 总墙钟超时。命中优先级为 `agent_name` 精确匹配 > 节点级 key 前缀匹配 > `default`。仅当字典非空且包含 `default` 时生效；命中值为 `0` 时表示对该规则禁用业务层总超时。默认值：`dict()`。

**说明**：

- HTTP 服务入口 `DeepSearchRequest.agent_llm_timeouts` 会透传到 `AgentConfig.agent_llm_timeouts`，并在运行时进入 session 全局配置。
- 该配置只控制业务层对整次 LLM 流式调用施加的 wall-clock timeout，不替代底层 provider/request 级超时。
- 当前可配置的 `agent_name` / key 清单详见openjiuwen_deepsearch/utils/constants_utils/node_constants.py的AgentLlmName定义，`default` 用于兜底规则。


**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import AgentConfig, LLMConfig, WebSearchEngineConfig

>>> # 样例1：创建Agent配置
>>> gpt_4 = LLMConfig(model_name="gpt-4", model_type="openai")
>>> qwen3_max = LLMConfig(model_name="qwen3-max", model_type="openai")
>>> llm_configs = {
...    "general": gpt_4,
...    "plan_understanding": qwen3_max,
... }
>>> web_search_config = WebSearchEngineConfig(search_engine_name="petal")
>>> agent_config = AgentConfig(
...     execute_mode="general",
...     execution_method="parallel",
...     llm_config=llm_configs,
...     web_search_engine_config=web_search_config,
...     info_collector_search_method="all"
... )
>>> print(agent_config.execute_mode, agent_config.execution_method)
general parallel

>>> # 样例2：使用默认配置
>>> agent_config = AgentConfig()
>>> print(agent_config.execute_mode, agent_config.workflow_human_in_the_loop)
commercial True
```

## class openjiuwen_deepsearch.config.runtime_api_models.ApiToolsConfig
```python
class openjiuwen_deepsearch.config.runtime_api_models.ApiToolsConfig()
```
**ApiToolsConfig** 描述注入到工作流中的两类运行时 HTTP 工具列表，对应 **`AgentConfig.api_tools_config`**。

**字段**：

- **query_understanding_tools**(List[RuntimeApiToolConfig], 可选)：用于 **planner、outliner** 等与 query understanding 相关的阶段（实现上经 `build_runtime_api_tools(..., response_model=Plan|Outline)` 合并）。默认值：`[]`。
- **collector_tools**(List[RuntimeApiToolConfig], 可选)：用于 **信息收集（collector）** 阶段。默认值：`[]`。

**说明**：HTTP 服务若使用 **`DeepSearchRequest.tools`** 传入工具列表，服务端会将同一组规范化后的工具**同时**填入上述两个列表。

## class openjiuwen_deepsearch.config.runtime_api_models.RuntimeApiToolConfig
```python
class openjiuwen_deepsearch.config.runtime_api_models.RuntimeApiToolConfig()
```
**RuntimeApiToolConfig** 描述单个 HTTP API 工具的调用方式与参数，由 **`build_runtime_api_tools`** 转为 `LocalFunction`。

**字段**（节选，完整定义见源码 `runtime_api_models.py`）：

- **tool_id**、**name**、**description**：工具标识与展示信息。
- **path**、**base_url**、**http_method**：请求 URL 与动词；`path` 可为完整 URL。
- **headers**：默认请求头列表。
- **request_params**：请求参数（含 `send_method`：`none` / `header` / `query` / `body` 等）。
- **response_wrapper**：响应包装类型（如 collector 侧 `search_result`）；与 planner/outliner 上传入的 **`response_model`** 路径不同，详见 `runtime_api.py` 实现。
- **response_params**：保留字段，不参与当前返回映射逻辑。

## class openjiuwen_deepsearch.config.config.ServiceConfig
```python
class openjiuwen_deepsearch.config.config.ServiceConfig()
```
**ServiceConfig**是服务配置类，用于配置SDK服务的默认参数。

**字段**：

### 服务基础配置
- **service_allow_origins**(List[str], 可选)：允许的ip范围。默认值：`[]`。

### 模板参数
- **template_max_generate_retry_num**(int, 可选)：模板生成最大重试次数。默认值：`3`。

### 工作流基础参数
- **workflow_execution_timeout**(int, 可选)：工作流执行超时时间，单位秒。默认值：`7200`。
- **workflow_sub_graph_execution_timeout**(int, 可选)：子图执行超时时间。默认值：`6000`。
- **workflow_max_plan_executed_num**(int, 可选)：最大计划执行数量。默认值：`2`。
- **workflow_recursion_limit**(int, 可选)：递归限制。默认值：`30`。
- **workflow_max_gen_question_retry_num**(int, 可选)：最大生成问题执行数量。默认值：`3`。
- **workflow_feedback_mode**(str, 可选)：用户反馈途径，可选值：`["web", "cmd"]`。默认值：`"web"`。

### 大纲节点基础参数
- **outliner_max_generate_outline_retry_num**(int, 可选)：最大生成大纲重试次数。默认值：`3`。

### 规划节点基础参数
- **planner_max_step_num**(int, 可选)：最大步骤数量。默认值：`3`。
- **planner_max_retry_num**(int, 可选)：最大重试次数。默认值：`3`。

### 信息收集节点参数
- **info_collector_max_search_query_count**(int, 可选)：单轮最大搜索查询数量。默认值：`5`。
- **info_collector_max_research_loops**(int, 可选)：最大研究循环次数。默认值：`2`。
- **info_collector_max_tool_call_turns_per_query**(int, 可选)：单个 collector query 最大工具调用轮次。默认值：`2`。
- **info_collector_max_retry_num**(int, 可选)：最大重试次数。默认值：`3`。
- **info_collector_webpage_enrich_max_urls**(int, 可选)：网页正文增强每轮最多抓取并增强的 URL 数量。默认值：`3`。
- **info_collector_webpage_enrich_fetch_timeout_seconds**(int, 可选)：网页正文增强单个 URL 抓取超时时间，单位秒。默认值：`45`。

### 报告节点参数
- **sub_report_classify_doc_infos_res_top_k_num**(int, 可选)：子报告中单次llm处理返回的top_k数量。默认值：`20`。
- **report_max_generate_retry_num**(int, 可选)：生成内容最大重试次数。默认值：`3`。
- **visualization_enable**(bool, 可选)：报告插图可视化开关。默认值：`False`。

### 溯源节点参数
- **source_tracer_citation_verify_max_concurrency_num**(int, 可选)：溯源校验最大并发数量。默认值：`30`。
- **source_tracer_citation_verify_batch_size**(int, 可选)：溯源校验批次大小。默认值：`1`。

### 统计信息参数
- **stats_info_node_duration**(bool, 可选)：节点持续时间统计。默认值：`False`。
- **stats_info_search**(bool, 可选)：搜索工具调用统计。默认值：`False`。

### 大模型超时参数
- **llm_timeout**(int, 可选)：底层 provider/request 级的大模型调用超时时间，单位秒。默认值：`300`。

**说明**：

- `service_config.llm_timeout` 会继续传给底层 LLM client。
- 若同时配置 `agent_llm_timeouts`，业务层会在整次流式调用外再包一层总墙钟超时；命中新机制时会抛出 `LLM_WALL_CLOCK_TIMEOUT`（状态码 `211204`）。

### 大模型思考模式参数
- **llm_thinking_enabled**(bool, 可选)：是否开启大模型思考模式。默认值：`False`。该配置仅在 SDK 内部的 `DeepresearchAgent` 初始化 LLM 时生效，用于对支持的厂商注入关闭/开启思考参数；`DeepSearchAgent`、`SimpleReactSearchAgent` 以及 REST API 入参不使用该字段。

**说明**：

- `service_config.llm_thinking_enabled` 不是 `LLMConfig` 字段，也不会作为服务化 REST API 的公开请求参数；如模型厂商不支持思考开关，系统仅记录 warning 并保持原始扩展参数。
- 若 `LLMConfig.extension` 中已手动配置思考相关字段，内部统一思考开关会覆盖这些字段，并记录 warning 日志。

### Debug参数
- **node_debug_enable**(bool, 可选)：节点格式化记录debug日志开关。默认值：`False`。
- **export_intermediate_results**(bool, 可选)：可视化任务执行中间结果开关。默认值：`False`。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import ServiceConfig

>>> # 样例1：创建服务配置
>>> service_config = ServiceConfig(
...     workflow_execution_timeout=3600,
...     llm_timeout=600,
...     llm_thinking_enabled=False,
...     node_debug_enable=True
... )
>>> print(service_config.workflow_execution_timeout, service_config.node_debug_enable)
3600 True

>>> # 样例2：使用默认配置
>>> service_config = ServiceConfig()
>>> print(service_config.workflow_execution_timeout, service_config.llm_timeout)
7200 300
```

## class openjiuwen_deepsearch.config.config.Config
```python
class openjiuwen_deepsearch.config.config.Config()
```
**Config**是总配置类，包含Agent配置和服务配置。

**字段**：

- **agent_config**(AgentConfig, 可选)：对外开放的Agent参数。默认值：`AgentConfig()`。
- **service_config**(ServiceConfig, 可选)：SDK服务默认参数。默认值：`ServiceConfig()`。

**样例**：

```python
>>> from openjiuwen_deepsearch.config.config import Config, AgentConfig, ServiceConfig

>>> # 样例1：创建总配置
>>> agent_config = AgentConfig(execute_mode="general")
>>> service_config = ServiceConfig(workflow_execution_timeout=3600)
>>> config = Config(
...     agent_config=agent_config,
...     service_config=service_config
... )
>>> print(config.agent_config.execute_mode, config.service_config.workflow_execution_timeout)
general 3600

>>> # 样例2：使用默认配置
>>> config = Config()
>>> print(config.agent_config.execute_mode, config.service_config.workflow_execution_timeout)
commercial 7200
```
