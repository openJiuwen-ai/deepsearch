# openjiuwen_deepsearch.framework.openjiuwen.agent.workflow

## class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.BaseAgent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.BaseAgent()
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
- `file_stream` base64 解码后的原始文件大小上限为 `50 MB`。
- `is_template=False` 且上传 PDF 时，最多支持 `512` 页。
- `is_template=False` 且上传 DOCX 时，解压后的总大小上限为 `50 MB`，且 `word/document.xml` 大小上限为 `8 MB`。
- 模板规范化/文档解析后的 Markdown 内容大小上限为 `5 MB`。
- 异常场景会记录接口日志并返回失败信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchAgent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchAgent()
```
并行执行模式的研究工作流 Agent。

### run
```python
async run(message: Optional[str] = None, conversation_id: Optional[str] = None, agent_config: Optional[dict] = None, report_template: str = "", interrupt_feedback: str = "")
```
运行工作流并返回流式输出。

**参数要点**：
- `agent_config` 会被 `AgentConfig.model_validate` 校验。
- `interrupt_feedback` 用于控制中断恢复和用户反馈，可选值：`""`、`"accepted"`、`"cancel"`、`"revise_outline"`、`"revise_comment"`。默认值 `""`
  - `""`：正常执行，返回 SSE 流式响应
  - `"accepted"`：用于 HITL（人机交互）场景，表示用户接受中断并继续流程
  - `"cancel"`：取消正在运行的任务，返回 JSON 响应（非 SSE 流）
  - `"revise_comment"`：大纲交互场景，用户对大纲提供修改意见，系统将重新生成大纲
  - `"revise_outline"`：大纲交互场景，用户直接修改大纲内容，系统将基于用户修改重新生成
- `report_template` 若为 base64 字符串会自动解码，解码失败则回退原文。


**返回**：
- **AsyncGenerator[str]**：流式 JSON 字符串（默认执行、HITL 恢复、大纲交互、报告后局部优化场景）。
- **dict**：JSON 响应（当 `interrupt_feedback="cancel"` 时）。

**行为说明**：
- 初始化 LLM 与搜索工具上下文。
- `native` 本地搜索要求 `knowledge_base_configs` 非空。
- 将互动事件包装为中断消息输出。
- 收到 `ALL END` 视为流程完成并清理上下文。
- 当 `interrupt_feedback="cancel"` 时，接口返回 JSON 响应而非流式输出，用于取消正在运行的任务。取消功能支持单进程和跨进程（Redis 模式）两种场景。
- 当 `interrupt_feedback` 为 `"revise_comment"` 或 `"revise_outline"` 时，系统会在大纲交互节点处理用户反馈，并重新生成大纲。
- 当 `agent_config.user_feedback_processor_enable=True` 时，工作流在 `SourceTracerInferNode` 后不会立即结束，而是进入 `UserFeedbackProcessorNode` 进行报告后局部优化交互。

---

### register_web_search_tool
```python
@staticmethod
register_web_search_tool(custom_web: CustomWebSearchConfig, search_config: WebSearchEngineConfig)
```
注册网络搜索工具并返回引擎名称与映射。研究工作流还会注册内置 PubMed 和 arXiv 作为 secondary 搜索引擎；DeepSearch 只注册当前配置的活动 web 引擎。

---

### _register_local_search_tool
```python
@staticmethod
_register_local_search_tool(custom_local: CustomLocalSearchConfig, search_config: LocalSearchEngineConfig)
```
注册本地搜索工具并返回引擎名称与映射。若 `search_engine_name == "native"`，要求 `knowledge_base_configs` 非空。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchDependencyAgent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchDependencyAgent(DeepresearchAgent)
```
依赖驱动执行思维链模式的研究工作流 Agent。

**行为说明**：
- 与 `DeepresearchAgent` 共用 `run` 接口、参数校验和中断恢复机制。
- 当 `agent_config.execution_method="dependency_driving"` 时启用。
- 主链路为 `DependencyOutlineNode -> DependencyOutlineInteractionNode -> DependencyEditorTeamNode -> ReporterNode`。
- `DependencyEditorTeamNode` 内部统一编排依赖驱动的推理子图和写作子图，章节执行遵循依赖层级：上一层章节写作可以与下一层章节推理并行执行。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchIntentHybridAgent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepresearchIntentHybridAgent(DeepresearchAgent)
```
混合大纲路由模式的研究工作流 Agent。

**行为说明**：
- 与 `DeepresearchAgent` 共用 `run` 接口、参数校验和中断恢复机制。
- 当 `agent_config.execution_method="hybrid"` 时启用。
- 主链路复用 `OutlineNode` 和 `OutlineInteractionNode`，不会注册旧方案中的 hybrid 大纲节点。
- `IntentRecognitionNode` 会调用大纲模式 router LLM，并把结果写入 `search_context.outline_execution_method`。
- 当路由结果为 `parallel` 时进入 `EditorTeamNode`；当路由结果为 `dependency_driving` 时进入 `DependencyEditorTeamNode`。


## class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.workflow.DeepSearchAgent()
```
当 **`search_mode="search"`** 时使用的 DeepSearch 图 Agent：动作空间检索、并行执行 **`state_creation`** 子图、工具映射 **`search_fetch`** 或 **`retrieve`**，**`run`** 通常 **`yield`** 一条 JSON 结果。完整说明见 [`deepsearch_agent`](./deepsearch_agent.md)。

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
