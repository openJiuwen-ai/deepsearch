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
- **use_material_ids**(List[str])：规划阶段确认引用的用户素材 ID；报告撰写时会并入 `classified_content`。默认空列表。

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
- **format_requirements**(List[str])：章节级输出格式要求，例如表格、列名、逐项枚举、字数或样式约束。默认空列表。
- **is_core_section**(bool)：是否核心章节。默认值：`False`。
- **parent_ids**(List[str])：依赖章节。
- **relationships**(List[str])：依赖关系说明。
- **plans**(List[Plan])：章节计划列表。
- **section_focus**(str)：章节的分析职责标签，例如 `market_size_and_growth`、`vendors_and_supply`。默认值：`""`。
- **focus_dimensions**(List[str])：本章节应重点展开的分析维度。默认空列表。
- **material_bindings**(List[Dict[str, str]])：章节使用的用户素材，包含 `material_id`、`role` 与 `claims_to_use`。默认空列表。
- **doc_selection_debug**(Optional[Dict])：可选的文档选择调试信息。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.ChapterSidecar
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.ChapterSidecar(...)
```
**ChapterSidecar** 是供总报告生成复用的章节结构化汇总信息。

**字段**：
- **chapter_summary**(str)：章节摘要。默认值：`""`。
- **key_findings**(List[str])：章节关键发现。默认空列表。
- **risk_points**(List[str])：风险、限制或不确定性。默认空列表。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SectionLocalContract
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.SectionLocalContract(...)
```
**SectionLocalContract** 用于限制章节仅覆盖其负责的分析职责。

**字段**：
- **section_focus**(str)：当前章节的核心职责标签。默认值：`""`。
- **allowed_dimensions**(List[str])：允许重点展开的维度。默认空列表。
- **is_final_decision_section**(bool)：是否承担最终判断或排序职责。默认值：`False`。

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
- **section_id**(str | int)：默认值：`"1"`。
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

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Report
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.Report(...)
```
**Report** 是总报告模型。

---

## class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.FinalResult
```python
class openjiuwen_deepsearch.framework.openjiuwen.agent.search_context.FinalResult(...)
```
**FinalResult** 是工作流最终输出模型。

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
- **brief_state**(Dict[str, Any] | None)：Brief 工作流的序列化状态。默认值：`None`。
- **user_materials**(List[Dict[str, Any]])：StartNode 写入的原始用户素材。默认空列表。
- **material_analysis**(Dict[str, Any] | None)：素材预处理产物，包含 manifest 与摘要，可跨轮复用。默认值：`None`。
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
