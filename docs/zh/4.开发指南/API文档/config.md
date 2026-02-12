# jiuwen_deepsearch.config.config

## class jiuwen_deepsearch.config.config.LLMConfig
```python
class jiuwen_deepsearch.config.config.LLMConfig()
```
**LLMConfig**是LLM模型配置类，用于配置大语言模型的参数。

**字段**：

- **model_name**(str, 可选)：模型名称。默认值：`""`。
- **model_type**(Literal["openai", "siliconflow"], 可选)：模型类型。默认值：`"openai"`。
- **base_url**(str, 可选)：模型服务地址。默认值：`""`。
- **api_key**(bytearray, 可选)：模型调用密钥。默认值：`bytearray("", encoding="utf-8")`。
- **hyper_parameters**(dict, 可选)：模型调用超参数设置，根据具体模型接口设置。默认值：`{}`。
- **extension**(dict, 可选)：模型扩展配置项，根据具体模型接口设置。默认值：`{}`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import LLMConfig

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
```

## class jiuwen_deepsearch.config.config.WebSearchEngineConfig
```python
class jiuwen_deepsearch.config.config.WebSearchEngineConfig()
```
**WebSearchEngineConfig**是网络搜索引擎配置类，用于配置网络搜索相关的参数。

**字段**：

- **search_engine_name**(Literal["tavily", "google", "xunfei", "petal", "custom"], 可选)：搜索引擎名称。默认值：`"tavily"`。
- **search_api_key**(bytearray, 可选)：搜索引擎调用密钥。默认值：`bytearray("", encoding="utf-8")`。
- **search_url**(str, 可选)：搜索引擎调用地址。默认值：`""`。
- **max_web_search_results**(int, 可选)：最大搜索结果数量，取值范围：[1, 10]。默认值：`5`。
- **extension**(dict, 可选)：搜索引擎扩展配置项，根据具体搜索引擎接口设置。默认值：`{}`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import WebSearchEngineConfig

>>> # 样例1：创建网络搜索引擎配置
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
```

## class jiuwen_deepsearch.config.config.LocalSearchEngineConfig
```python
class jiuwen_deepsearch.config.config.LocalSearchEngineConfig()
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

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import LocalSearchEngineConfig

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

## class jiuwen_deepsearch.config.config.CustomWebSearchConfig
```python
class jiuwen_deepsearch.config.config.CustomWebSearchConfig()
```
**CustomWebSearchConfig**是自定义网络搜索配置类，用于配置自定义网络搜索工具的参数。

**字段**：

- **custom_web_search_file**(str, 可选)：自定义Web搜索工具文件路径。默认值：`""`。
- **custom_web_search_func**(str, 可选)：自定义Web搜索工具函数名称。默认值：`""`。
- **extension**(dict, 可选)：自定义Web搜索工具扩展配置项，根据具体搜索引擎接口设置。默认值：`{}`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import CustomWebSearchConfig

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

## class jiuwen_deepsearch.config.config.CustomLocalSearchConfig
```python
class jiuwen_deepsearch.config.config.CustomLocalSearchConfig()
```
**CustomLocalSearchConfig**是自定义本地搜索配置类，用于配置自定义本地搜索工具的参数。

**字段**：

- **custom_local_search_file**(str, 可选)：自定义本地搜索工具文件路径。默认值：`""`。
- **custom_local_search_func**(str, 可选)：自定义本地搜索工具函数名称。默认值：`""`。
- **extension**(dict, 可选)：自定义本地搜索工具扩展配置项，根据具体搜索引擎接口设置。默认值：`{}`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import CustomLocalSearchConfig

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

## class jiuwen_deepsearch.config.config.AgentConfig
```python
class jiuwen_deepsearch.config.config.AgentConfig()
```
**AgentConfig**是Agent配置类，用于配置Agent的执行模式和参数。

**字段**：

- **execute_mode**(Literal["commercial", "general"], 可选)：执行模式，可选值：`["commercial", "general"]`。默认值：`"commercial"`。
- **execution_method**(Literal["dependency_driving", "parallel"], 可选)：执行方法，`dependency_driving`：依赖驱动工作流执行，`parallel`：并行工作流执行。默认值：`"parallel"`。
- **workflow_human_in_the_loop**(bool, 可选)：工作流是否启用人机交互。默认值：`True`。
- **outliner_max_section_num**(int, 可选)：最大规划章节数量，取值范围：[1, 10]。默认值：`5`。
- **source_tracer_research_trace_source_switch**(bool, 可选)：溯源功能开关。默认值：`True`。
- **llm_config**(LLMConfig, 可选)：LLM模型配置。默认值：`LLMConfig()`。
- **info_collector_search_method**(Literal["web", "local", "all"], 可选)：搜索方式，`web`：联网搜索，`local`：本地搜索工具搜索，`all`：联网+本地融合搜索。默认值：`"web"`。
- **web_search_engine_config**(WebSearchEngineConfig, 可选)：网络搜索引擎配置。默认值：`WebSearchEngineConfig()`。
- **local_search_engine_config**(LocalSearchEngineConfig, 可选)：本地搜索引擎配置。默认值：`LocalSearchEngineConfig()`。
- **custom_web_search_config**(CustomWebSearchConfig, 可选)：自定义网络搜索配置。默认值：`CustomWebSearchConfig()`。
- **custom_local_search_config**(CustomLocalSearchConfig, 可选)：自定义本地搜索配置。默认值：`CustomLocalSearchConfig()`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import AgentConfig, LLMConfig, WebSearchEngineConfig

>>> # 样例1：创建Agent配置
>>> llm_config = LLMConfig(model_name="gpt-4", model_type="openai")
>>> web_search_config = WebSearchEngineConfig(search_engine_name="petal")
>>> agent_config = AgentConfig(
...     execute_mode="general",
...     execution_method="parallel",
...     llm_config=llm_config,
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

## class jiuwen_deepsearch.config.config.ServiceConfig
```python
class jiuwen_deepsearch.config.config.ServiceConfig()
```
**ServiceConfig**是服务配置类，用于配置SDK服务的默认参数。

**字段**：

### 服务基础配置
- **service_allow_origins**(List[str], 可选)：允许的ip范围。默认值：`[]`。

### 工作流基础参数
- **workflow_execution_timeout**(int, 可选)：工作流执行超时时间，单位秒。默认值：`7200`。
- **workflow_max_plan_executed_num**(int, 可选)：最大计划执行数量。默认值：`2`。
- **workflow_max_gen_question_retry_num**(int, 可选)：最大生成问题执行数量。默认值：`3`。
- **workflow_feedback_mode**(str, 可选)：用户反馈途径，可选值：`["web", "cmd"]`。默认值：`"web"`。

### 大纲节点基础参数
- **outliner_max_generate_outline_retry_num**(int, 可选)：最大生成大纲重试次数。默认值：`3`。

### 规划节点基础参数
- **planner_max_step_num**(int, 可选)：最大步骤数量。默认值：`3`。
- **planner_max_retry_num**(int, 可选)：最大重试次数。默认值：`3`。

### 信息收集节点参数
- **info_collector_max_react_recursion_limit**(int, 可选)：React代理最大递归限制。默认值：`8`。
- **info_collector_initial_search_query_count**(int, 可选)：初始搜索查询数量。默认值：`3`。
- **info_collector_max_research_loops**(int, 可选)：最大研究循环次数。默认值：`2`。
- **info_collector_max_retry_num**(int, 可选)：最大重试次数。默认值：`3`。

### 报告节点参数
- **sub_report_classify_doc_infos_single_time_num**(int, 可选)：子报告中单次llm处理筛选收集到的数量。默认值：`60`。
- **sub_report_classify_doc_infos_res_top_k_num**(int, 可选)：子报告中单次llm处理返回的top_k数量。默认值：`10`。
- **report_max_generate_retry_num**(int, 可选)：生成内容最大重试次数。默认值：`3`。

### 模板参数
- **template_max_generate_retry_num**(int, 可选)：模板生成最大重试次数。默认值：`3`。

### 溯源节点参数
- **source_tracer_citation_verify_max_concurrency_num**(int, 可选)：溯源校验最大并发数量。默认值：`30`。
- **source_tracer_citation_verify_batch_size**(int, 可选)：溯源校验批次大小。默认值：`1`。

### 统计信息参数
- **stats_info_node_duration**(bool, 可选)：节点持续时间统计。默认值：`False`。
- **stats_info_llm**(bool, 可选)：LLM调用统计。默认值：`False`。
- **stats_info_search**(bool, 可选)：搜索工具调用统计。默认值：`False`。

### 大模型超时参数
- **llm_timeout**(int, 可选)：大模型调用超时时间，单位秒。默认值：`300`。

### Debug参数
- **debug_enable**(bool, 可选)：节点调试开关。默认值：`False`。

### Visualization参数
- **visualization_enable**(bool, 可选)：报告插图可视化开关。默认值：`True`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import ServiceConfig

>>> # 样例1：创建服务配置
>>> service_config = ServiceConfig(
...     workflow_execution_timeout=3600,
...     llm_timeout=600,
...     debug_enable=True
... )
>>> print(service_config.workflow_execution_timeout, service_config.debug_enable)
3600 True

>>> # 样例2：使用默认配置
>>> service_config = ServiceConfig()
>>> print(service_config.workflow_execution_timeout, service_config.llm_timeout)
7200 300
```

## class jiuwen_deepsearch.config.config.Config
```python
class jiuwen_deepsearch.config.config.Config()
```
**Config**是总配置类，包含Agent配置和服务配置。

**字段**：

- **agent_config**(AgentConfig, 可选)：对外开放的Agent参数。默认值：`AgentConfig()`。
- **service_config**(ServiceConfig, 可选)：SDK服务默认参数。默认值：`ServiceConfig()`。

**样例**：

```python
>>> from jiuwen_deepsearch.config.config import Config, AgentConfig, ServiceConfig

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
