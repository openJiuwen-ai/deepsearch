# jiuwen_deepsearch.framework.jiuwen.agent.agent_factory

## class jiuwen_deepsearch.framework.jiuwen.agent.agent_factory.AgentFactory
```python
class jiuwen_deepsearch.framework.jiuwen.agent.agent_factory.AgentFactory()
```
**AgentFactory** 是创建 Agent 实例的工厂类，会依据配置中的 `execution_method` 返回不同类型的 Agent。

> 模块级行为：导入该模块时会根据 `Config().service_config.workflow_execution_timeout` 写入环境变量 `WORKFLOW_EXECUTE_TIMEOUT`。

### __init__
```python
__init__()
```
初始化工厂并构建执行方式到 Agent 类的映射：

- `"parallel"` → `DeepresearchAgent`

**样例**：
```python
>>> from jiuwen_deepsearch.framework.jiuwen.agent.agent_factory import AgentFactory
>>> factory = AgentFactory()
>>> print(factory.agent_map)
{...}
```

### create_agent
```python
create_agent(agent_config: dict)
```
根据配置创建并返回对应的 Agent 实例。

**参数**：
- **agent_config**(dict)：Agent配置字典。会经过 `validate_agent_required_field` 与 `AgentConfig.model_validate` 校验。

**返回**：
- `DeepresearchAgent`：并行执行（默认）

**异常**：
- `CustomValueException`：入参校验失败或 `execution_method` 不支持时抛出。

**样例**：
```python
>>> from jiuwen_deepsearch.framework.jiuwen.agent.agent_factory import AgentFactory
>>> factory = AgentFactory()

>>> # 样例1：并行执行
>>> agent_config = {
...     "llm_config": {"model_name": "gpt-4", "model_type": "openai"},
...     "execution_method": "parallel"
... }
>>> agent = factory.create_agent(agent_config)
>>> print(type(agent).__name__)
DeepresearchAgent
