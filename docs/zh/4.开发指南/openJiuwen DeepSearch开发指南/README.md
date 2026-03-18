# 初始化DeepResearchAgent配置

---
配置参数类`Config`包括两种类型的参数变量，一是`AgentConfig`类，这些参数是通过对外接口，用户可修改的配置参数；二是`ServiceConfig`类，这些参数涵盖系统各模块的主要核心配置参数，且已配置默认值。

初始化配置参数时，可根据功能需求，对`AgentConfig`的参数，进行赋值。

```python
from openjiuwen_deepsearch.config.config import Config

# 实例化AgentConfig
agent_config = Config().agent_config.model_dump()
# 对必填项进行赋值
# 1. 配置LLM
agent_config["llm_config"]["general"]["model_name"] = ""
agent_config["llm_config"]["general"]["model_type"] = ""
agent_config["llm_config"]["general"]["base_url"] = ""
agent_config["llm_config"]["general"]["api_key"] = ""
# 2. 配置联网增强引擎
agent_config["web_search_engine_config"]["search_engine_name"] = ""
agent_config["web_search_engine_config"]["search_url"] = ""
agent_config["web_search_engine_config"]["search_api_key"] = ""
```

## 大模型配置说明

---

openJiuwen-DeepSearch 当前可以为全部模块配置四个模型：
- **plan_understanding:** 该模型旨在能理解用户意图，生成任务规划步骤，减少幻觉，配置在Outliner、Planner模块
- **info_collecting:** 该模型用于信息收集各个步骤，配置在InfoCollector
- **writing_checking:** 该模型用于准确生成报告及插入图文，配置在Sub_reporter
- **general:** 该模型为通用模型，综合能力较强，所有模块都可调用该模型 

其中，**general模型必须配置**，其他模型配置可选，其他模型未配置时，默认使用general模型，因此，建议general配置综合能力较强的模型    

每个模型都支持接入两种类型模型：
 - 硅基流动厂商系列模型，且遵循OpenAI接口格式。`LLMConfig`的`model_type`参数必须赋值为siliconflow。
 - OpenAI格式模型，模型服务按照标准OpenAI格式封装实现。`LLMConfig`的`model_type`参数必须赋值为openai。


> 说明：用户需要自行前往硅基流动或者OpenAI的官网注册账号，以便获取模型广场中可用模型的api_key、模型名称model_name和模型调用的URL请求地址base_url。

## 联网增强引擎配置说明

---

openJiuwen-DeepSearch 支持接入四种类型联网增强引擎：

 - Google `web_search_engine_config`的`search_engine_name`参数必须赋值为google。
 - Tavily `web_search_engine_config`的`search_engine_name`参数必须赋值为tavily。
 - 讯飞搜索 `web_search_engine_config`的`search_engine_name`参数必须赋值为xunfei。
 - 小艺AI问答联网增强 `web_search_engine_config`的`search_engine_name`参数必须赋值为petal。


> 说明：用户需要自行前往相应的联网增强引擎的官网注册账号，以便获取可用的search_api_key和联网增强引擎调用的URL请求地址search_url。

## ssl证书配置说明

---
在访问LLM大模型服务、以及联网增强引擎服务时，openJiuwen-DeepSearch提供ssl证书配置能力，如需启用，需要在环境变量中，打开`LLM_SSL_VERIFY`设置为`true`，并提供大模型服务访问的证书`LLM_SSL_CERT`。同理，打开`TOOL_SSL_VERIFY`设置为`true`时，需要提供网络搜索服务访问的证书`TOOL_SSL_CERT`。

如果不需要启用ssl功能，需显式关闭环境变量中`LLM_SSL_VERIFY`和`TOOL_SSL_VERIFY`，设置为`false`，此时不需要提供对应的证书。

```python
import os
os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["LLM_SSL_CERT"] = ""
os.environ["TOOL_SSL_VERIFY"] = "false"
os.environ["TOOL_SSL_CERT"] = ""
```

# 实例化DeepResearchAgent类

---
`DeepResearchAgent`是系统基于openJiuwen开发框架，开发并预置的深度研究智能体。能够根据用户查询进行研究报告生成。

## 通过AgentFactory方式创建

---

`AgentFactory`类支持根据配置`agent_config`，实例化`DeepResearchAgent`类，获取`DeepResearchAgent`对象。

```python
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)
```

获取的agent是一个`DeepResearchAgent`实例。

## 通过构造函数方式创建

---
直接通过`DeepResearchAgent`的构造函数，获取实例化对象。

```python
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepResearchAgent

agent = DeepResearchAgent(agent_config)
```

# 生成研究报告

---

`DeepResearchAgent`可以根据用户查询，进行深度研究和分析规划，通过网络搜索等任务完成信息收集，并生成研究报告。用户的输入，可以分为三种情况：
 - 用户查询，描述用户的需求或者问题。
 - 用户查询和用户已有模板，期望系统遵循已有模板进行研究报告生成。
 - 用户查询和用户已有报告，期望系统遵循已有报告的章节格式进行研究报告生成。

## 根据用户查询生成研究报告

---

`DeepResearchAgent`的`run`函数，可接收用户查询`message`，数据类型是`str`。`conversation_id`参数是会话标识id。深度研究过程，遵循`agent_config`的参数配置。

`run`函数按照流式数据的模式，逐帧输出系统内部结果。每帧数据是`dict`类型，key值`agent`记录当前帧数据的生产者角色；key值`content`来记录当前帧数据的具体内容。最终的研究报告生产者角色为`NodeId.END.value`。

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

`DeepResearchAgent`的`generate_template`函数，可以对用户提供的模板文件进行规范化校验和处理。其中，入参`is_template`标识用户提供的文件是否为模板文件，此处取值为`True`。

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

`DeepResearchAgent`的`run`函数，参数`report_template`可接收系统规范化后的模板文件内容`user_template_content`，数据类型是`str`，是一份base64编码。

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

与上一小节“根据用户查询和用户已有模板生成研究报告”不同的是，`DeepResearchAgent`的`generate_template`函数，入参`is_template`标识应取值为`False`，标识用户提供的文件为样例报告文件。

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

提取出规范化后的模板文件内容`user_template_content`之后，继续通过`DeepResearchAgent`的`run`函数，进行研究报告生成。

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

在规划预备阶段，系统会根据用户的原始查询自动生成若干延伸问题，引导用户提供更多背景信息，以便系统更准确地理解研究目标。

当配置参数：

```python
agent_config.workflow_human_in_the_loop = True
````

系统将执行用户查询意图交互流程，该功能 **默认开启**。

---

### 工作流程

1. 用户提交原始查询
2. 系统提出补充问题
3. 系统中断流程等待用户回答
4. 用户反馈后系统恢复流程并继续执行 DeepResearch

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
outline_interaction_enabled = True
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


# 更多参考

---

 - 开发指南的完整示例代码，详见：https://gitcode.com/openJiuwen/deepsearch/blob/dev/main.py
 - 更多关于openJiuwen-DeepSearch的API介绍，详见：https://gitcode.com/openJiuwen/deepsearch/tree/dev/docs/zh/4.%E5%BC%80%E5%8F%91%E6%8C%87%E5%8D%97