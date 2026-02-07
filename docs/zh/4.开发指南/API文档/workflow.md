# jiuwen_deepsearch.framework.jiuwen.agent.workflow

## class jiuwen_deepsearch.framework.jiuwen.agent.workflow.BaseAgent
```python
class jiuwen_deepsearch.framework.jiuwen.agent.workflow.BaseAgent()
```
**BaseAgent** 是所有 Agent 的基类。

### run
```python
async run(message: str, conversation_id: str, agent_config: dict, report_template: str = "", interrupt_feedback: str = "")
```
抽象方法，子类必须实现。默认抛出 `CustomValueException`。

---

### generate_template
```python
async generate_template(file_name: str, file_stream: str, is_template: bool, agent_config: dict)
```
生成报告模板。

**参数**：
- **file_name**(str)：文件名（含后缀）。
- **file_stream**(str)：base64 编码的文件内容。
- **is_template**(bool)：是否为模板文件（True：模板 / False：从报告生成）。
- **agent_config**(dict)：Agent配置。

**返回**：
- **dict**：`{"status": "success"|"fail", "template_content": str, "error_message": str}`

**说明**：
- 会校验入参并调用 `TemplateGenerator.generate_template`。
- 异常场景会记录接口日志并返回失败信息。

---

## class jiuwen_deepsearch.framework.jiuwen.agent.workflow.DeepresearchAgent
```python
class jiuwen_deepsearch.framework.jiuwen.agent.workflow.DeepresearchAgent()
```
并行执行模式的研究工作流 Agent。

### run
```python
async run(message: Optional[str] = None, conversation_id: Optional[str] = None, agent_config: Optional[dict] = None, report_template: str = "", interrupt_feedback: str = "")
```
运行工作流并返回流式输出。

**参数要点**：
- `agent_config` 会被 `AgentConfig.model_validate` 校验。
- `interrupt_feedback` 仅允许 `""` 或 `"accepted"`。
- `report_template` 若为 base64 字符串会自动解码，解码失败则回退原文。

**返回**：
- **AsyncGenerator[str]**：流式 JSON 字符串。

**行为说明**：
- 初始化 LLM 与搜索工具上下文。
- `native` 本地搜索要求 `knowledge_base_configs` 非空。
- 将互动事件包装为中断消息输出。
- 收到 `ALL END` 视为流程完成并清理上下文。

---

### _register_web_search_tool
```python
@staticmethod
_register_web_search_tool(custom_web: CustomWebSearchConfig, search_config: WebSearchEngineConfig)
```
注册网络搜索工具并返回引擎名称与映射。

---

### _register_local_search_tool
```python
@staticmethod
_register_local_search_tool(custom_local: CustomLocalSearchConfig, search_config: LocalSearchEngineConfig)
```
注册本地搜索工具并返回引擎名称与映射。若 `search_engine_name == "native"`，要求 `knowledge_base_configs` 非空。

---

## function validate_generate_template_params
```python
validate_generate_template_params(file_name: str, file_stream: str, is_template: bool)
```
校验 `generate_template` 入参合法性。

---

## function validate_run_params
```python
validate_run_params(message: str, conversation_id: str, report_template: str = "", interrupt_feedback: str = "")
```
校验 `run` 入参合法性。

---

## function parse_endnode_content
```python
parse_endnode_content(chunk: CustomSchema) -> dict | None
```
解析 EndNode 输出内容，若内容为 JSON 且包含 `exception_info` 则返回该字典，否则返回空字典。
