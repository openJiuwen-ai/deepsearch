# openjiuwen_deepsearch.framework.openjiuwen.agent.search_context

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Message
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Message(name: str = "", role: str, content: str)
```
**Message** 是对话消息模型。

**字段**：
- **name**(str, 可选)：消息名称。默认值：`None`。
- **role**(str, 必需)：角色（`user` / `system` / `assistant`）。
- **content**(str, 必需)：消息内容。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.StepType
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.StepType(str, Enum)
```
**StepType** 是步骤类型枚举。

**枚举值**：
- **INFO_COLLECTING**：`"info_collecting"`。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.RetrievalQuery
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.RetrievalQuery(...)
```
**RetrievalQuery** 是具体步骤Step中每个query的检索信息

**字段**：
- **query**(str)：直接用于检索的query。
- **description**(str)：简要说明query为何与搜索任务相关，为何要生成当前query。
- **doc_infos**(Optional[List[Dict]])：query检索的文档信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Step
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Step(...)
```
**Step** 是章节计划中的具体步骤。

**字段**：
- **id**(str, 可选)：步骤唯一标识符。默认值：`""`。
- **type**(StepType)：步骤类型。
- **title**(str)：步骤标题。
- **description**(str)：步骤说明。
- **parent_ids**(List[str], 可选)：依赖步骤。
- **relationships**(List[str], 可选)：依赖关系说明。
- **background_knowledge**(List[str], 可选)：背景知识。
- **retrieval_queries**(List[Dict], 可选)：每个query的检索信息。
- **step_result**(str, 可选)：步骤总结结果。默认值：`None`。
- **evaluation**(str, 可选)：步骤评估。默认值：`""`。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Plan
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Plan(...)
```
**Plan** 是章节计划模型。

**字段**：
- **id**(str)：默认值：`""`。
- **language**(str)：默认值：`"zh-CN"`。
- **title**(str)：计划标题。
- **thought**(str)：计划思考。
- **is_research_completed**(bool)：是否完成信息收集。
- **steps**(List[Step])：默认空列表。
- **background_knowledge**(Dict[str, str], 可选)：默认空字典。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Section
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Section(...)
```
**Section** 是章节模型。

**字段**：
- **id**(str)：默认值：`""`。
- **title**(str)：章节标题。
- **description**(str)：章节说明。
- **is_core_section**(bool)：是否核心章节。默认值：`False`。
- **parent_ids**(List[str])：依赖章节。
- **relationships**(List[str])：依赖关系说明。
- **plans**(List[Plan])：章节计划列表。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Outline
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Outline(...)
```
**Outline** 是大纲模型。

**字段**：
- **id**(str)：默认值：`""`。
- **language**(str)：默认值：`"zh-CN"`。
- **thought**(str)：大纲思考过程。
- **title**(str)：报告标题。
- **sections**(List[Section])：章节列表。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SubReport
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SubReport(...)
```
**SubReport** 是子报告模型。

**字段**：
- **id**(str)：默认值：`""`。
- **section_id**(int)：默认值：`0`。
- **section_task**(str)：子章节任务标题。
- **background_knowledge**(List[Dict], 可选)：背景知识。
- **content**(SubReportContent)：子报告内容。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SubReportContent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SubReportContent(...)
```
**SubReportContent** 是子报告内容模型。

**字段**：
- **classified_content**(List[Dict])：子章节筛选的文档信息。
- **sub_report_content**(str)：子报告内容。
- **sub_report_content_summary**(str)：子报告摘要。
- **sub_report_trace_source_datas**(List[Dict])：子报告溯源信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Report
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Report(...)
```
**Report** 是总报告模型。

**字段**：
- **id**(str)：默认值：`""`。
- **report_task**(str)：总报告任务。
- **report_template**：总报告模板。
- **sub_reports**(List[SubReport])：子报告列表。
- **report_content**(str)：溯源前报告内容。
- **all_classified_contents**(List[Dict])：所有子章节筛选的文档信息。
- **merged_trace_source_datas**(List[Dict])：溯源校验前的引用信息。
- **checked_trace_source_report_content**(str)：溯源后报告内容。
- **checked_trace_source_datas**(List[Dict])：最终溯源信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.FinalResult
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.FinalResult(...)
```
**FinalResult** 是工作流最终输出模型。

**字段**：
- **response_content**(str)：响应内容。
- **citation_messages**(dict)：引用信息。
- **exception_info**(str)：异常信息。
- **warning_info**(str)：告警信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SearchContext
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SearchContext(...)
```
**SearchContext** 是工作流运行时状态模型。

**字段**：
- **session_id**(str)：会话ID。默认值：`""`。
- **query**(str)：用户输入问题。默认值：`None`。
- **messages**(List[Message])：对话消息列表。
- **language**(str)：默认值：`"zh-CN"`。
- **report_template**(str)：模板内容。默认值：`""`。
- **search_mode**(str)：默认值：`"research"`。
- **questions**(str)：系统提问。默认值：`""`。
- **user_feedback**(str)：用户反馈。默认值：`""`。
- **outline_executed_num**(int)：默认值：`0`。
- **current_outline**(Union[Outline, Dict, str, None])：当前大纲。
- **history_outlines**(List[Outline])：历史大纲。
- **report_generated_num**(int)：默认值：`0`。
- **current_report**(Union[Report, Dict, str, None])：当前报告。
- **history_reports**(List[Report])：历史报告。
- **final_result**(FinalResult)：最终结果。
- **debug_pre_step**(str)：上一步调试日志。
