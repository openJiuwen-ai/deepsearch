# 初始化 DeepResearch 配置

---
配置参数类`Config`包括两种类型的参数变量，一是`AgentConfig`类，这些参数是通过对外接口，用户可修改的配置参数；二是`ServiceConfig`类，这些参数涵盖系统各模块的主要核心配置参数，且已配置默认值。

初始化配置参数时，可根据功能需求，对`AgentConfig`的参数，进行赋值。

```python
from openjiuwen_deepsearch.config.config import Config

agent_config = Config().agent_config.model_dump()

# 1. 配置至少一个可用的 LLM
agent_config["llm_config"]["general"]["model_name"] = ""
agent_config["llm_config"]["general"]["model_type"] = ""
agent_config["llm_config"]["general"]["base_url"] = ""
agent_config["llm_config"]["general"]["api_key"] = ""

# 2. 配置联网增强引擎
agent_config["web_search_engine_config"]["search_engine_name"] = ""
agent_config["web_search_engine_config"]["search_url"] = ""
agent_config["web_search_engine_config"]["search_api_key"] = ""

# 3. 按需覆盖执行参数
agent_config["workflow_human_in_the_loop"] = False
agent_config["outline_interaction_enabled"] = False
agent_config["search_mode"] = "research"
agent_config["execution_method"] = "parallel"  # 可选："parallel"、"dependency_driving"、"hybrid"
```

## 大模型配置说明

---

openJiuwen-DeepSearch 当前可以为全部模块配置四个模型：
- **plan_understanding:** 该模型旨在能理解用户意图，生成任务规划步骤，减少幻觉，配置在IntentRecognition、Outliner、Planner模块。
- **info_collecting:** 该模型用于信息收集各个步骤，配置在InfoCollector
- **writing_checking:** 该模型用于准确生成报告及插入图文，配置在Sub_reporter
- **general:** 该模型为通用模型，综合能力较强，所有模块都可调用该模型 
- **vlm_chart_generating** 该模型为专门处理图表的多模态模型（参考下表），可以接收图表输入，配置在VLMChartGenerator

其中，**general模型必须配置**，其他模型配置可选，其他模型未配置时，默认使用general模型，因此，建议general配置综合能力较强的模型    

每个模型都支持接入两种类型模型：
 - 硅基流动厂商系列模型，且遵循OpenAI接口格式。`LLMConfig`的`model_type`参数必须赋值为siliconflow。
 - OpenAI格式模型，模型服务按照标准OpenAI格式封装实现。`LLMConfig`的`model_type`参数必须赋值为openai。

`DeepresearchAgent` 在 SDK 内部默认会关闭支持厂商的模型思考模式，对应配置为 `ServiceConfig.llm_thinking_enabled=False`。该配置仅作用于 `DeepresearchAgent` 初始化 LLM 的流程，`DeepSearchAgent` 和 `SimpleReactSearchAgent` 不受影响。若需要开启思考模式，可在 SDK 内部运行配置中设置 `service_config.llm_thinking_enabled=True`；不建议通过 `LLMConfig.extension` 手动维护统一思考开关，以免与内部厂商适配规则冲突。


> 说明：用户需要自行前往硅基流动或者OpenAI的官网注册账号，以便获取模型广场中可用模型的api_key、模型名称model_name和模型调用的URL请求地址base_url。

vlm_chart_generating 多模态模型参考表

| 模型 | 单张图 / 1次测评迭代 耗时 (s) | 模型优势 |
| :---: | :---: | :--- |
| qwen3.5-plus | 34.18 | 千问性能最强的视觉理解模型 |
| qwen3.5-flash | 20.28 | 速度更快，成本更低，适用于对响应速度敏感的场景 |
| qwen3-vl-plus | 4.68 | Qwen3-VL 系列中性能最强的模型 |
| qwen3-vl-flash | 3.7 | 速度更快，成本更低，适用于对响应速度敏感的场景 |
| qwen-vl-max | 4.88 | Qwen2.5-VL 系列中效果最佳的模型 |
| qwen-vl-plus | 2.7 | 速度更快，在效果与成本之间实现良好平衡 |
> 支持qwen系列的其他vlm模型，以及适配openai类型的模型。

## 联网增强引擎配置说明

---

openJiuwen-DeepSearch 当前支持以下内置联网增强引擎，均通过 `web_search_engine_config.search_engine_name` 指定：

- `google`：Google/Serper 搜索适配。
- `serper`：研究态复用 Google/Serper Wrapper 的别名。
- `tavily`：Tavily 搜索。
- `xunfei`：讯飞搜索。
- `petal`：小艺 AI 问答联网增强。
- `bocha`：通过 harness `web_tools` 适配的博查搜索。
- `jina`：直接接入 Jina Search API，默认地址为 `https://s.jina.ai`。
- `perplexity`：通过 harness `web_tools` 适配的 Perplexity 搜索。
- `custom`：加载外部自定义联网搜索工具。

不同引擎的接入方式与配置重点如下：

- `jina` 使用项目内置的直接 HTTP API Wrapper；当 `search_url` 为空时，会自动回退到 `https://s.jina.ai`。国内网络环境如无法访问该默认地址，可显式将 `search_url` 配置为 `https://s.jinaai.cn`。可通过 `extension` 传入 `gl`、`hl`、`location`、`page` 等查询参数。
- `bocha`、`perplexity` 使用 harness `web_tools` 适配层；支持通过 `extension.timeout_seconds` 控制调用超时，通过 `extension.fetch_webpage` 控制是否继续抓取网页正文。仅当底层 provider 支持 URL 覆盖时，`search_url` 才会生效。国内网络环境如无法访问 Perplexity 默认服务，需要配置可访问的代理或转发地址，并通过 `search_url` 显式覆盖。
- `serper` 在研究态 `web_search_tool` 中映射到 Google/Serper Wrapper，便于与服务端配置名称保持一致。
- `tavily`、`google/serper`、`xunfei`、`petal` 保持原有接入方式，其中公共引擎允许 `search_url=""`，此时使用内置默认地址或 provider 默认行为。

搜索结果进入 Collector 链路前，系统还会执行统一的内容裁剪与归一化：

- `bocha`、`perplexity` 在预抓取网页正文后，会先按 `MAX_COLLECTOR_DOC_CONTENT_LENGTH` 裁剪，避免超长正文直接进入后续提示词。
- Collector 在 `_structure_result` 阶段会再次按同一上限裁剪传给 `run_doc_evaluation` 的内容。
- `web_page_search_record` 会统一保留标准化字段 `title`、`url`、`content`、`type`，兼容不同引擎返回的 `link`、`source_url`、`snippet`、`summary`、`answer` 等别名字段。

> 说明：用户需要自行前往相应联网增强引擎的官网注册账号，以便获取 `search_api_key`。对于 Jina 等公共搜索接口，`search_url` 可以留空使用系统默认地址；国内网络环境建议为 Jina 显式配置 `search_url="https://s.jinaai.cn"`。如需私有化部署、代理转发或 vendor 提供自定义地址，也可显式传入 `search_url`。

## ssl证书配置说明

---
在访问LLM大模型服务、联网增强引擎服务、以及知识库 Embedding 服务时，openJiuwen-DeepSearch提供ssl证书配置能力：

- **LLM**：如需启用，在环境变量中设置`LLM_SSL_VERIFY`为`true`，并提供`LLM_SSL_CERT`。
- **Tool（联网增强引擎）**：打开`TOOL_SSL_VERIFY`设置为`true`时，需要提供`TOOL_SSL_CERT`。
- **Embedding（知识库索引构建）**：`EMBEDDING_SSL_VERIFY`为`true`时启用 HTTPS 证书校验；使用系统信任的 CA 时可不配`EMBEDDING_SSL_CERT`。访问自签名或企业自建 CA 的 Embedding 地址时，需设置`EMBEDDING_SSL_CERT`为 PEM 证书路径。通过本仓库`server/main.py`启动时，未设置或空白`EMBEDDING_SSL_VERIFY`会按`false`处理（关闭校验），与`.env.example`默认一致；若显式设为`true`而服务端证书不可信且未配置证书路径，可能导致构建索引失败。

如果不需要启用 SSL 校验，可将`LLM_SSL_VERIFY`、`TOOL_SSL_VERIFY`、`EMBEDDING_SSL_VERIFY`设为`false`（或不配置 Embedding 开关并依赖上述默认），此时不需要提供对应的证书。

```python
import os
os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["LLM_SSL_CERT"] = ""
os.environ["TOOL_SSL_VERIFY"] = "false"
os.environ["TOOL_SSL_CERT"] = ""
os.environ["EMBEDDING_SSL_VERIFY"] = "false"
os.environ["EMBEDDING_SSL_CERT"] = ""
```

# 实例化 Agent

---
系统基于 openJiuwen 开发框架预置了深度研究 Agent，能够根据用户查询完成分析规划、信息收集和研究报告生成。

## 通过AgentFactory方式创建

---

`AgentFactory` 会根据 `agent_config` 中的 `execution_method` 等配置，返回当前应使用的 Agent 实例。研究模式下，`parallel` 返回 `DeepresearchAgent`，`dependency_driving` 返回 `DeepresearchDependencyAgent`，`hybrid` 返回 `DeepresearchIntentHybridAgent`。

当 `execution_method="hybrid"` 时，系统会在 `IntentRecognitionNode` 中调用大纲模式 router LLM，根据用户 query 自动选择普通大纲或依赖驱动大纲，并将实际选择结果写入 `search_context.outline_execution_method`。

```python
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)
```

这是当前最推荐的创建方式，因为它会自动根据执行模式选择合适的 Agent 实现。

## 通过构造函数方式创建

---
如果你明确希望直接使用并行研究 Agent，也可以手动实例化 `DeepresearchAgent`。

```python
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent

agent = DeepresearchAgent()
```

# 生成研究报告

---

`DeepresearchAgent` 的 `run()` 和 `generate_template()` 可以覆盖当前文档中的主要使用场景。用户输入通常分为三种情况：
 - 用户查询，描述用户的需求或者问题。
 - 用户查询和用户已有模板，期望系统遵循已有模板进行研究报告生成。
 - 用户查询和用户已有报告，期望系统遵循已有报告的章节格式进行研究报告生成。

## 根据用户查询生成研究报告

---

`DeepresearchAgent` 的 `run` 函数可接收用户查询 `message`，数据类型是 `str`。`conversation_id` 是会话标识，整个深度研究过程遵循 `agent_config` 的参数配置。

`run`函数按照流式数据的模式，逐帧输出系统内部结果。每帧数据是`dict`类型，key值`agent`记录当前帧数据的生产者角色；key值`content`来记录当前帧数据的具体内容。默认情况下，最终结果由`NodeId.END.value`输出；当开启报告后局部优化能力时，`user_feedback_processor`节点会在结束前额外承担一轮交互。

用户查询 `message` 不必只包含研究主题，也可以直接携带报告生成约束。系统会先做意图识别，从查询中提取研究主题 `research_query` 以及结构化约束 `research_intent`，再将这些约束透传给后续的大纲、规划、信息收集与写作阶段。

当前查询中可直接表达的常见约束包括：

- **报告类型**：例如“精简版”“专业版”，内部会归一化为 `brief` 或 `professional`。
- **章节数量**：例如“生成5个章节”。
- **目标读者**：例如“面向投资人”“给研发负责人看”“供政策研究员参考”。
- **写作风格**：例如“正式”“分析”“客观”“解释型”。
- **信息源约束**：例如“参考这些链接”“不要使用某站点内容”。

示例查询：

```text
请写一份精简版报告，控制在 4 个章节以内，面向研发负责人，语气正式且偏分析：AI Agent 工程化落地趋势
```

```text
请基于以下链接写一份专业版报告，面向投资人，重点分析 2025 年中国低空经济商业化进展：
https://example.com/a
https://example.com/b
```

```python
import json
import uuid
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

message = "用户原始查询问题"
conversation_id = str(uuid.uuid4())

async for chunk in agent.run(message=message, conversation_id=str(uuid.uuid4()), agent_config=agent_config):
    logger.debug("[Stream message from node: %s]", chunk)
    chunk_content = json.loads(chunk)
    report_result = parse_endnode_content(chunk_content)
    if report_result:  # 获取最终研究报告内容
        logger.debug("[Final Report is: %s]", report_result)
```

## 根据用户查询和用户已有模板生成研究报告

---

当配置参数`agent_config`中，开启遵从模板模式时，用户同时输入已准备好的模板文件内容，系统生成的研究报告，可以遵从用户提供的模板文件的要求。

用户提供的模板文件，是期望生成研究报告的章节大纲以及各章节的核心内容规范。模板文件格式支持markdown类型，模板内容需遵循以下要求：
 - 各一级标题的内容是对目标研究报告的一级章节内容的简要描述。
 - 各二级标题的内容是对目标研究报告中二级章节的内容简要描述。
 - 功能概述是对目标研究报告的具体内容的进一步描述。
 - 是否核心章节是对目标研究报告的当前章节是否关键章节的标识，核心章节为系统重点撰写的章节。

模板文件通过 `generate_template` 上传时，base64 解码后的原始文件大小上限为 `50 MB`；规范化后的 Markdown 内容大小上限为 `5 MB`。

以下是模板文件实例：
```markdown
# 企业基本情况
> 功能概述：详细阐述目标企业的具体情况
> 是否核心章节：true

## 1.1 基础信息
> 功能概述：罗列该企业的各项基础信息。

## 1.2 经营范围和主营业务
> 功能概述：详细列示并解析企业经核准的法定经营范围，并在此基础上精准识别其实际从事的核心主营业务。

## 1.3 股权结构与关联企业
> 功能概述：详细列明各股东的持股比例、出资方式及股东性质，梳理并披露重要关联企业。

# 企业经营与行业分析
> 功能概述：详细阐述目标企业经营与所在的行业分析
> 是否核心章节：true

## 2.1 宏观环境与区域经济分析
> 功能概述：行业宏观环境（所在行业大类和中类行业宏观环境分析）、区域经济与产业集群。

## 2.2 行业发展现状与前景
> 功能概述：所在行业大类和中类的行业发展现状与前景分析。

## 2.3 企业竞争力分析
> 功能概述：包括生产能力与规模、技术研发实力、市场地位与品牌、核心客户结构

## 2.4 上下游产业链分析
> 功能概述：上游供应链分析和下游客户结构分析
```

`DeepresearchAgent` 的 `generate_template` 函数可以对用户提供的模板文件进行规范化校验和处理。其中，入参 `is_template` 标识用户提供的文件是否为模板文件，此处取值为 `True`。

```python
import base64
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

# 提供入参
file_path = "用户提供的模板文件名，以md后缀结尾"
file_stream = base64.b64encode(read_file_safely(file_path)).decode("utf-8")  # "用户提供的模板文件内容的base64编码"
is_template = True  # 标识模板文件

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

# 执行模板文件处理操作
result = await agent.generate_template(file_name=file_path, file_stream=file_stream, is_template=is_template,
                                       agent_config=agent_config)
user_template_content = result["template_content"]
```

`DeepresearchAgent` 的 `run` 函数支持通过参数 `report_template` 接收系统规范化后的模板内容 `user_template_content`，数据类型是 `str`，内容为一份 base64 编码字符串。

```python
import json
import uuid
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content

message = "用户原始查询问题"
conversation_id = str(uuid.uuid4())

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

async for chunk in agent.run(message=message, conversation_id=conversation_id, agent_config=agent_config,
                             report_template=user_template_content):
    logger.debug("[Stream message from node: %s]", chunk)
    chunk_content = json.loads(chunk)
    report_result = parse_endnode_content(chunk_content)
    if report_result:  # 获取最终研究报告内容
        logger.debug("[Final Report is: %s]", report_result)
```

## 根据用户查询和用户已有报告生成研究报告

---

当配置参数`agent_config`中，开启遵从模板模式时，用户同时输入已准备好的样例报告文件内容，系统可以先根据用户提供的样例报告文件，提取出模板内容，再遵从模板内容要求，进行研究报告生成。

用户提供的样例报告文件，与期望生成研究报告遵循相同模板。样例报告文件格式支持markdown、docx、pdf、html。

样例报告通过 `generate_template` 上传时，base64 解码后的原始文件大小上限为 `50 MB`；若为 PDF，最多支持 `512` 页；若为 DOCX，解压后的总大小上限为 `50 MB`，且 `word/document.xml` 大小上限为 `8 MB`；解析后的 Markdown 内容大小上限为 `5 MB`。

与上一小节“根据用户查询和用户已有模板生成研究报告”不同的是，`DeepresearchAgent` 的 `generate_template` 函数中，入参 `is_template` 应取值为 `False`，表示用户提供的是样例报告文件。

```python
import base64
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

# 提供入参
file_path = "用户提供的样例报告文件的文件名，以md/docx/pdf/html后缀结尾"
file_stream = base64.b64encode(read_file_safely(file_path)).decode("utf-8")  # "用户提供的样例报告文件内容的base64编码"
is_template = False  # 标识样例报告文件

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

# 执行模板文件处理操作
result = await agent.generate_template(file_name=file_path, file_stream=file_stream, is_template=is_template,
                                       agent_config=agent_config)
user_template_content = result["template_content"]
```

提取出规范化后的模板文件内容 `user_template_content` 之后，再通过 `DeepresearchAgent` 的 `run` 函数继续生成研究报告。

```python
import json
import uuid
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content

message = "用户原始查询问题"
conversation_id = str(uuid.uuid4())

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

async for chunk in agent.run(message=message, conversation_id=conversation_id, agent_config=agent_config,
                             report_template=user_template_content):
    logger.debug("[Stream message from node: %s]", chunk)
    chunk_content = json.loads(chunk)
    report_result = parse_endnode_content(chunk_content)
    if report_result:  # 获取最终研究报告内容
        logger.debug("[Final Report is: %s]", report_result)
```

# 人机交互

---

本功能支持在 DeepResearch 工作流执行过程中与用户进行自然语言式交互，从而更准确地理解用户需求，并允许用户参与研究规划过程。

当启用人机交互后，系统会在关键节点暂停执行流程，等待用户反馈，并在用户反馈后恢复执行。

当前支持两种人机交互阶段：

1. **用户查询意图交互（Clarification Interaction）**：在任务规划前，通过提问进一步理解用户需求。
2. **大纲交互（Outline Interaction）**：在报告大纲生成后，允许用户对大纲进行多轮修改。

在所有交互过程中，**会话标识 `conversation_id` 必须保持一致**，以便系统实现流程中断与恢复。

---

## 用户查询意图交互（Clarification Interaction）

在规划预备阶段，系统会根据用户的原始查询生成 `research_query`，再依据 `research_query` 自动生成若干延伸问题，引导用户提供更多背景信息，以便系统更准确地理解研究目标。

当配置参数：

```python
agent_config["workflow_human_in_the_loop"] = True
```

系统将执行用户查询意图交互流程，该功能 **默认开启**。

---

### 工作流程

1. 用户提交原始查询
2. 系统根据用户原始查询，意图识别后生成 `research_query` 与 `research_intent`
3. 系统基于 `research_query` 提出补充问题，并保留 `research_intent` 供后续节点消费
4. 系统中断流程等待用户回答
5. 用户反馈后系统恢复流程并继续执行 DeepResearch

---

### 交互模式

支持两种交互方式：

#### web 模式（推荐）

用户通过 Web 前端（如 Studio）输入反馈。

```python
service_config.workflow_feedback_mode = "web"
```

#### cmd 模式

用户通过命令行直接输入反馈。

```python
service_config.workflow_feedback_mode = "cmd"
```

---

### Web 模式示例

```python
# 第一轮请求：用户原始问题
{
    "message": "用户原始查询问题",
    "conversation_id": "会话标识id",
    "agent_config": {"workflow_human_in_the_loop": True, ...}
}

# 第二轮请求：用户回答系统问题
{
    "message": "用户的反馈回答",
    "conversation_id": "会话标识id，与第一轮保持一致",
    "agent_config": {"workflow_human_in_the_loop": True, ...}
}
```

---

## 大纲交互（Outline Interaction）

在报告大纲生成后，系统支持用户对大纲进行 **多轮交互式修改**，以确保最终研究结构符合用户预期。

当配置参数：

```python
agent_config["outline_interaction_enabled"] = True
```

系统在生成大纲后会暂停执行，并等待用户反馈，该功能 **默认开启**。

---

### 交互模式

用户可以通过以下三种方式反馈：

| 动作               | 说明       | 后续流程         |
| ---------------- | -------- | ------------ |
| `accepted`       | 用户接受当前大纲 | 进入报告生成阶段     |
| `revise_comment` | 用户提供修改意见 | 系统根据意见重新生成大纲 |
| `revise_outline` | 用户直接修改大纲 | 系统基于用户修改优化大纲 |

---

### 配置参数

大纲交互相关参数定义在 **Server 层 `DeepSearchRequest`** 中。

| 参数                               | 类型   | 默认值    | 说明                  |
| -------------------------------- | ---- | ------ | ------------------- |
| `outline_interaction_enabled`    | bool | `True` | 是否开启大纲交互            |
| `outline_interaction_max_rounds` | int  | `3`    | 最大交互轮次，范围 `[1,100]` |

SDK 层通过 `agent_config` 接收这些参数。

**运行时 API 工具（可选）**：Server 层 **`DeepSearchRequest.tools`** 用于传入 HTTP 接口型工具列表（元素类型见 `RuntimeApiToolRequest`）。服务端在构建 Agent 时会将其规范化为 **`api_tools_config`**。

**运行时 API URL 安全校验开关**：默认会对 Runtime API URL 进行安全校验（例如拒绝私网/本机地址）。仅本地调试场景可通过环境变量 `RUNTIME_API_ALLOW_UNSAFE_URL=true`（等价真值 `1/true/yes`）放宽校验；未设置时保持安全校验开启。生产环境不建议开启该开关，否则会削弱 SSRF 防护。

---

### Server 层请求示例

```python
{
    "message": "用户查询",
    "conversation_id": "会话ID",
    "outline_interaction_enabled": True,
    "outline_interaction_max_rounds": 3,
    ...
}
```

### 空间（space_id）与本地知识库

**Server 层 `DeepSearchRequest`** 中的 `space_id` 用于多租户隔离：创建知识库、上传文档等接口均与 `space_id` 关联。调用 `run` 并启用本地检索时，`local_search_config.local_search_config_ids` 中的知识库必须属于请求体中的 **`space_id`**；服务端会查询数据库校验，**跨空间传入 `kb_id` 会失败**。

**知识库与对象存储**：仅当服务端环境为 `CHECKPOINTER_TYPE=redis`（分布式）时，上传的知识库文件会写入配置的对象存储以便多实例一致访问；`in_memory` / `persistence` 时文件只落在服务端本地磁盘，不使用 OBS。多实例时应用数据库须为 MySQL 且各实例共用同一库，否则知识库元数据无法在实例间一致（服务端会拒绝 `redis` + `sqlite` 的配置组合）。

服务端对 Agent 的缓存键由 **`DeepSearchRequest` 中参与构建 Agent 的字段**（排除 `message`、`conversation_id`、`interrupt_feedback`）经稳定 JSON 序列化后取哈希得到，其中包含 **`space_id`**、**`local_search_config`（含知识库 ID 列表）**、联网检索与 `llm_config`、各类开关等；因此不仅不同空间不会共用同一 Agent，**同一空间下更换本地知识库或检索相关配置也不会误命中旧缓存**。

若部署在公网或多人共用同一后端，**不应**仅依赖请求体中的 `space_id` 作为唯一信任来源，应在网关或鉴权层校验调用方是否有权使用该空间。

---

### Web 模式交互示例

在 Web 模式下：

* `interrupt_feedback` 表示用户动作
* `message` 表示反馈内容

```python
# 第一轮：生成大纲（等待交互）
{
    "message": "分析中国新能源汽车市场发展趋势",
    "conversation_id": "会话标识id",
    "agent_config": {
        "outline_interaction_enabled": True,
        "outline_interaction_max_rounds": 3,
        ...
    }
}

# 第二轮：用户提出修改意见
{
    "message": "请增加充电基础设施建设的分析章节",
    "conversation_id": "会话标识id，与第一轮保持一致",
    "interrupt_feedback": "revise_comment",
    "agent_config": {...}
}

# 第三轮：用户接受大纲
{
    "message": "",
    "conversation_id": "会话标识id，与之前保持一致",
    "interrupt_feedback": "accepted",
    "agent_config": {...}
}
```

---

## 注意事项

* 所有交互轮次必须保持 **`conversation_id` 一致**，否则系统无法恢复工作流。
* 当系统进入交互阶段时，流程会 **触发中断（interrupt）并等待用户反馈**。
* 用户反馈后，系统会根据 `interrupt_feedback` 参数恢复流程执行。
* 大纲交互存在最大轮次限制 `outline_interaction_max_rounds`，超过后将自动进入报告生成阶段。

---
# 报告后局部优化

---

本功能支持在报告生成完成后，针对用户选中的局部文本继续进行扩写、润色、缩写、补充检索或内容真实性核验。开启方式是在`agent_config`中设置：

```python
agent_config["user_feedback_processor_enable"] = True
agent_config["user_feedback_processor_max_interactions"] = 100
```

该功能与前置 HITL 不同，它发生在报告和溯源结果已经生成之后。工作流会在内部进入`UserFeedbackProcessorNode`：
- 首次进入时，系统会先向前端发送完整的`final_result`快照。
- 后续前端继续使用同一个`conversation_id`，把用户动作作为 JSON 字符串传给`message`。
- 每次改写成功后，系统会返回局部替换信息和最新的`final_result`，前端可据此增量刷新内容。
- 当用户发送`finish`或达到最大交互次数时，流程结束。

当前支持的动作如下：
- `expand`：扩写选中文本。
- `polish`：润色选中文本。
- `shorten`：缩写选中文本。
- `supplementary_search`：结合补充检索对选中内容定向增强（见下文「改写范围」）。
- `new_task`：根据新的用户任务改写现有章节或追加新小节。
- `truth_verification`：对选中内容进行真实性核验，不修改报告正文。
- `sync`：将前端已编辑完成的整篇报告同步回后端状态。
- `finish`：结束当前局部优化会话。

**协议约定（与实现一致）：**
- **`action` 必填**：必须为已注册动作之一，且为非空字符串；不可省略或由后端推断。
- **`rewrite_scope`（除 `finish` 外建议始终携带）**：通用字段。若省略或传空字符串，后端在解析阶段会默认补为 `selected_only`。当前合法取值：
  - `selected_only`：仅替换用户选区对应片段（默认）。
  - `selected_and_related`：替换选区所在**整章**，并允许衔接性联动改写（仅 `supplementary_search` 使用；其它动作即使携带也会在行为上忽略）。
- 对 `supplementary_search`，`rewrite_scope` 必须为上述二者之一（否则在校验阶段报错）。

局部改写动作（`expand`、`polish`、`shorten`、`supplementary_search`、`new_task`）以及只读核验动作 `truth_verification` 的请求体需包含以下字段：
- `action`：动作类型（必填）。
- `selected_text`：用户当前选中的原始文本。
- `start_offset`：选中文本在当前报告中的起始偏移。
- `end_offset`：选中文本在当前报告中的结束偏移。
- `user_instruction`：附加改写或补充说明，可选；若出现则须为字符串。
- `rewrite_scope`：可选，默认 `selected_only`；仅 `supplementary_search` 强制消费；`truth_verification` 不使用该字段。

`sync` 请求体只需要：
- `action`：固定为 `sync`。
- `selected_text`：前端编辑后的完整报告内容。

`sync` 不需要传 `start_offset` / `end_offset`，也不会消耗 `feedback_interaction_count`。

```python
import json
import uuid
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

conversation_id = str(uuid.uuid4())
message = "请生成一份某行业研究报告"

# 第一轮：正常生成报告
async for chunk in agent.run(message=message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Stream message from node: %s]", chunk)

# 第二轮：对报告局部内容执行扩写
feedback_message = json.dumps({
    "action": "expand",
    "rewrite_scope": "selected_only",
    "selected_text": "需要扩写的原文片段",
    "start_offset": 120,
    "end_offset": 136,
    "user_instruction": "补充行业背景和数据解释"
}, ensure_ascii=False)

async for chunk in agent.run(message=feedback_message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Rewrite stream message: %s]", chunk)

# 按需选用补充检索（与 expand 类似，将 message 换为下列之一后同样 agent.run）：
# - 仅替换选区：rewrite_scope 为 selected_only，或省略 rewrite_scope（等价默认）
# - 整章联动：rewrite_scope 为 selected_and_related（走后端另一套 prompt 与替换范围）
# json.dumps({
#     "action": "supplementary_search",
#     "rewrite_scope": "selected_only",  # 或 "selected_and_related"
#     "selected_text": "...",
#     "start_offset": 0,
#     "end_offset": 0,
#     "user_instruction": "可选说明"
# }, ensure_ascii=False)

# 按需执行真实性核验（只读，不修改报告正文）：
# json.dumps({
#     "action": "truth_verification",
#     "selected_text": "需要核验的原文片段",
#     "start_offset": 120,
#     "end_offset": 136,
#     "user_instruction": "可选补充说明"
# }, ensure_ascii=False)

# 第三轮：结束局部优化
finish_message = json.dumps({"action": "finish"}, ensure_ascii=False)
async for chunk in agent.run(message=finish_message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Finish stream message: %s]", chunk)

# 前端也可以在整篇报告编辑完成后发送 sync，同步最新全文到后端状态：
# json.dumps({
#     "action": "sync",
#     "selected_text": "完整编辑后报告"
# }, ensure_ascii=False)
```

说明：
- 局部改写动作要求 `selected_text` 与当前报告中 `[start_offset, end_offset)` 范围内的文本完全一致，否则会返回偏移校验错误。
- 改写结果会更新 `final_result.response_content`；当 `source_tracer_research_trace_source_switch` 开启时，后端会对变化片段执行差异感知局部溯源，未变化片段保留原引用，新增引用会同步更新 `citation_messages` 并在文末追加参考文献。
- 后端不再额外维护 offset 映射。
- `sync` 仅更新 `final_result.response_content`，不消耗 `feedback_interaction_count`，且只有整篇报告内容实际变化时才会追加一条 `search_context.rewrite_history` 记录。
- 后端仅保留最近 10 条 `sync` 历史；内容未变化的 `sync` 不会新增历史记录。
- 每次成功的普通局部改写会在 `search_context.rewrite_history` 中追加一条记录，其中包含 `action`、`rewrite_scope`（若有）及偏移等信息，便于排查与审计。
- `truth_verification` 不更新 `final_result.response_content`，也不写入 `rewrite_history`；流式 `SUMMARY_RESPONSE` 的 `content` 为 JSON，包含 `display_text` 与 `feedback_interaction_count`，并消耗一次 `feedback_interaction_count`。
- **兼容性**：省略 `rewrite_scope` 时与显式传 `selected_only` 等价；**`action` 不可省略或为空字符串**，若旧版前端仍依赖后端推断动作，需改为显式传入合法 `action`。



# 更多参考

---

 - 开发指南的完整示例代码，详见：https://gitcode.com/openJiuwen/deepsearch/blob/dev/main.py
 - 更多关于openJiuwen-DeepSearch的API介绍，详见：https://gitcode.com/openJiuwen/deepsearch/tree/dev/docs/zh/4.%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97
