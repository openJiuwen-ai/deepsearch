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

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.OutlineInteraction
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.OutlineInteraction(...)
```
**OutlineInteraction** 是大纲交互模型，记录用户与系统进行大纲交互时的输入输出。

**字段**：
- **feedback**(str)：用户反馈内容。默认值：`""`。
- **interaction_mode**(str)：大纲交互模式。可选值：
  - `"revise_comment"`：用户提供修改意见
  - `"revise_outline"`：用户直接修改大纲
- **outline_before**(Union[Outline, Dict, str, None])：用户反馈前的大纲状态。默认值：`None`。

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
- **background_knowledge**(List[Dict], 可选)：写作背景知识。依赖驱动模式下会记录父章节的摘要信息，
  典型结构为 `{"section_id": str, "content_summary": str}`，可在当前章节没有检索文档时作为写作兜底上下文。
- **content**(SubReportContent)：子报告内容。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SubReportContent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SubReportContent(...)
```
**SubReportContent** 是子报告内容模型。

**字段**：
- **classified_content**(List[Dict])：子章节筛选的文档信息。若当前章节直接使用 `background_knowledge` 生成内容，该字段可能为空列表。
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
- **infer_messages**(list): 溯源推理信息。报告导出时会读取其中的 `html_base64`，并写出为独立 HTML 资源。
- **chart_messages**(list): vlm迭代生成图信息。报告导出时会读取其中的 `base64`，并写出为图片资源。
- **exception_info**(str)：异常信息。
- **warning_info**(str)：告警信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.ReportTypePolicy
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.ReportTypePolicy(...)
```
**ReportTypePolicy** 是由 `research_intent.report_type` 解析出的报告类型策略，用于稳定控制各阶段的报告生成策略。

**字段**：
- **report_type**(str)：报告类型，当前为 `professional` 或 `brief`。
- **paragraph_style**(str)：段落风格，当前为 `detailed` 或 `concise`。
- **require_summary_first**(bool)：是否强调摘要/总览前置。
- **require_methodology_and_risk**(bool)：是否强调方法论与风险相关内容。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.ResearchIntent
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.ResearchIntent(...)
```
**ResearchIntent** 是从用户查询中解析出的结构化报告生成约束。

**字段**：
- **section_count**(Optional[int])：用户希望的章节数量。仅正整数会被保留。
- **audience_role**(Optional[str])：目标读者角色。
- **tone**(Optional[str])：写作风格，建议使用稳定英文枚举值，例如 `formal`、`analytical`。
- **report_type**(Optional[str])：报告类型，建议使用稳定英文枚举值 `professional`、`brief`。其中 `professional` 表示专业版报告，`brief` 表示精简版报告。
- **include_url**(List[str])：用户指定包含的链接。默认值：`[]`。
- **exclude_url**(List[str])：用户指定排除的链接。默认值：`[]`。
- **include_domains**(List[str])：用户指定的站点域名。默认值：`[]`。
- **exclude_domains**(List[str])：用户排除的站点域名。默认值：`[]`。

**运行期生效说明**：
- 大纲阶段 `section_num`：用户指定 `section_count` 时为 `min(section_count, OUTLINER_SECTION_NUM_MAX)`，否则为 `config.outliner_max_section_num`。
- `audience_role` 与 `tone` 会透传到大纲、章节规划（Plan）、子报告写作与总报告汇总阶段。
- `report_type` 会透传到大纲、章节规划（Plan）、信息收集、子报告写作与总报告汇总阶段。
- `include_domains`与`exclude_domains` 会透传到信息收集阶段。
---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SearchContext
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SearchContext(...)
```
**SearchContext** 是工作流运行时状态模型。

**字段**：
- **session_id**(str)：会话ID。默认值：`""`。
- **original_query**(str)：用户输入的原始问题。默认值：`""`。
- **research_intent**(ResearchIntent)：结构化报告约束。默认值：空 `ResearchIntent`。
- **report_type_policy**(ReportTypePolicy)：由 `research_intent.report_type` 解析出的运行期策略，供大纲、规划、信息收集、子报告写作和总报告汇总阶段消费。默认值：空 `ReportTypePolicy`。
- **messages**(List[Message])：对话消息列表。
- **language**(str)：默认值：`"zh-CN"`。
- **report_template**(str)：模板内容。默认值：`""`。
- **search_mode**(str)：默认值：`"research"`。
- **entry_search_results**(List[Dict])：Entry节点预搜索结果。默认值：`[]`。
- **questions**(str)：系统提问。默认值：`""`。
- **user_feedback**(str)：用户反馈。默认值：`""`。
- **outline_interactions**(List[OutlineInteraction])：大纲多轮交互历史记录。默认值：`[]`。
- **outline_execution_method**(Literal["", "parallel", "dependency_driving"])：hybrid 模式下本次大纲实际执行方式，空值表示尚未选择，`"parallel"` 表示普通大纲，`"dependency_driving"` 表示依赖驱动大纲。默认值：`""`。
- **outline_executed_num**(int)：默认值：`0`。
- **current_outline**(Union[Outline, Dict, str, None])：当前大纲。
- **history_outlines**(List[Outline])：历史大纲。
- **report_generated_num**(int)：默认值：`0`。
- **current_report**(Union[Report, Dict, str, None])：当前报告。
- **history_reports**(List[Report])：历史报告。
- **final_result**(FinalResult)：最终结果。
- **feedback_interaction_count**(int)：报告生成后局部优化的交互次数。默认值：`0`。
- **feedback_snapshot_sent**(bool)：是否已经向前端推送过初始反馈快照。默认值：`False`。
- **rewrite_history**(List[Dict])：局部改写历史记录。
- **debug_pre_node**(str)：上一个调试节点。默认值：`""`。
