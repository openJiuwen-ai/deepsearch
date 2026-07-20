# 依赖驱动大纲格式要求契约补齐设计

## 1. 背景

普通大纲工具 `create_outline_tool()` 已经把章节输出约束拆分为结构化字段
`format_requirements`，并由编辑团队管理节点传递到写作链路。该字段用于保存用户明确要求的
Markdown 表格、精确列名和顺序、指定行对象、逐项枚举、篇幅与样式约束、来源限制以及交付格式。

依赖驱动大纲工具 `creat_dep_driving_outline_tool()` 当前没有定义该字段。其工具说明反而要求模型把
表格、列、行、枚举等约束放进 `description`。因此依赖模式不能稳定、结构化地保存格式约束；约束可能
在描述压缩、重写或后续提示词处理时被弱化或丢失，生成的 `Section.format_requirements` 默认是空数组。

依赖写作链路还存在第二个断点：`editor_team_manager_node.py` 创建的 `section_state` 已包含
`section_format_requirements` 和 `section_local_contract`，但依赖写作工作流的输入映射与
`SectionWritingStartNode` 没有接收这两个字段。即使外部 Outline 提供了结构化格式要求，进入依赖写作
子图后仍会退化为默认空值。

## 2. 目标

本次改动应实现以下目标：

1. 依赖驱动大纲结构化生成 `format_requirements`。
2. 依赖工具要求非空的 `section_focus` 和 `focus_dimensions`，与普通大纲现有章节契约保持一致。
3. 依赖模式将研究内容与输出格式约束分别保存到 `description` 和 `format_requirements`。
4. 依赖写作子图完整接收 `section_format_requirements` 和 `section_local_contract`。
5. 格式要求从依赖 Outline 一直保留到子大纲及子报告提示词。
6. 不改变普通大纲的生产代码、提示词、公共校验逻辑和合法输入行为。
7. 保持历史 Outline 和持久化任务的反序列化兼容性。

## 3. 非目标

本次不处理以下事项：

- 不重构普通与依赖工具为共享 schema helper。
- 不修改 `create_outline_tool()`。
- 不修改普通大纲提示词 `outliner.md` 或 `outliner_template.md`。
- 不增强公共 `_has_required_section_value()` 的类型校验。
- 不修改普通编辑团队工作流。
- 不迁移或重写历史 Outline 数据。
- 不开展与章节契约无关的提示词重构。

这些限制用于减少普通大纲回归风险。两个工具继续分别维护 schema 的长期漂移风险由一致性测试控制。

## 4. 当前数据流与缺陷

### 4.1 普通模式

```text
create_outline_tool
  -> Section.format_requirements
  -> EditorTeamManager section_state
  -> editor writing workflow
  -> SectionContext
  -> SubReporter prompt
```

该链路已经接通，本次不修改。

### 4.2 依赖驱动模式

```text
DependencyOutlineNode(with_dep_driving=True)
  -> creat_dep_driving_outline_tool
  -> 无 format_requirements schema
  -> Section.format_requirements 默认为 []

外部或历史 Outline 即使带有 format_requirements
  -> EditorTeamManager section_state 中字段存在
  -> dependency writing workflow 未映射字段
  -> SectionContext 中重新退化为 [] / {}
```

这两个断点必须同时修复。只修改依赖 Outliner 仍会在写作子图入口丢失字段。

## 5. 设计方案

### 5.1 依赖工具章节契约

仅修改 `creat_dep_driving_outline_tool()`。在依赖 section properties 中加入：

```python
"format_requirements": {
    "type": "array",
    "description": _format_requirements_description(),
    "items": {"type": "string"},
}
```

依赖工具的章节必填字段为：

```python
[
    "title",
    "description",
    "format_requirements",
    "id",
    "parent_ids",
    "relationships",
    "section_focus",
    "focus_dimensions",
]
```

依赖工具现有的 `section_focus` 增加 `minLength: 1`，`focus_dimensions` 增加
`minItems: 1`。这只约束新生成的依赖工具调用，不改变 Pydantic 模型的历史兼容默认值。

依赖工具的章节说明改为启用结构化格式字段：

```python
_section_list_description(section_num)
```

依赖工具的 description 说明改为：

```python
_section_description_description("and its relationships")
```

不再使用 `include_format_requirements=False`。因此工具说明将要求模型把格式约束写入
`format_requirements`，而不是混入 `description`。

`generate_outline()` 已经通过 `_normalize_format_requirements()` 读取列表或兼容单个字符串，无需修改。

### 5.2 依赖提示词职责边界

修改 `dep_driving_outliner.md` 和 `dep_driving_outliner_interaction.md`，加入以下契约：

- `description` 保存研究对象、范围、时间范围、问题、分析维度和章节依赖关系。
- `format_requirements` 保存表格、精确列名及顺序、指定行对象、逐项枚举、篇幅与样式、来源限制和交付格式。
- 用户提供的名称、列顺序和对象顺序必须原样保留。
- 没有章节级格式要求时必须输出 `[]`，不能省略字段。
- 同一格式约束不应同时复制到 `description` 和 `format_requirements`。
- 每个章节必须提供非空 `section_focus` 和至少一个 `focus_dimensions` 元素。
- 交互模式收到用户新增、删除或修改格式要求的反馈时，必须更新对应章节的
  `format_requirements`，不能只改写 `description`。

### 5.3 依赖写作工作流传递

修改 `dependency_writing_team_nodes.py`。

`SectionWritingStartNode` 创建 `SectionContext` 时增加：

```python
section_format_requirements=inputs.get("section_format_requirements", []),
section_local_contract=inputs.get("section_local_contract") or {},
```

`build_dependency_writing_workflow()` 的开始节点输入 schema 增加：

```python
"section_format_requirements": "${section_format_requirements}",
"section_local_contract": "${section_local_contract}",
```

字段的上游生产者和下游消费者均已存在：

- `editor_team_manager_node.py` 已把两个字段放入 `section_state`。
- `SectionContext` 已定义两个字段及安全默认值。
- `SubReporterNode` 已从 `SectionContext` 读取两个字段并构造报告输入。
- 子大纲和子报告提示词已经使用 `section_format_requirements`。

因此本次只补齐依赖工作流边界，不修改上述模块。

### 5.4 兼容性与错误处理

Pydantic `Section` 模型继续保留：

```python
format_requirements: List[str] = Field(default_factory=list)
section_focus: str = Field(default="")
focus_dimensions: List[str] = Field(default_factory=list)
```

`SectionContext` 继续为格式要求和局部契约提供 `[]`、`{}` 默认值。历史 Outline、历史任务和外部旧数据
缺少字段时仍能加载。

新生成的依赖工具调用必须提供完整章节契约。缺失字段沿用现有 `check_tool_call()` 行为并抛出
`CustomValueException`。本次不扩展公共类型校验，以免改变普通工具非法输入的既有失败路径。

## 6. 修改文件

### 6.1 生产代码

1. `openjiuwen_deepsearch/algorithm/query_understanding/outliner.py`
   - 只修改 `creat_dep_driving_outline_tool()`。
   - 增加并要求 `format_requirements`。
   - 要求 `section_focus` 和 `focus_dimensions`。
   - 启用 description 与格式约束分离说明。

2. `openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner.md`
   - 增加结构化格式要求抽取和字段职责规则。

3. `openjiuwen_deepsearch/algorithm/prompts/dep_driving_outliner_interaction.md`
   - 增加交互反馈更新格式要求的规则。

4. `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/dependency_writing_team_nodes.py`
   - 将 `section_format_requirements` 和 `section_local_contract` 映射并保存到 `SectionContext`。

### 6.2 测试

5. `tests/algorithm/query_understanding/test_dependency_outliner.py`
   - 增加依赖工具 schema、必填字段、提示词和 Outline 数据保留测试。

6. `tests/workflow/test_dependency_writing_nodes.py`
   - 增加开始节点字段传递及默认值测试。

7. `tests/workflow/test_dependency_workflow.py`
   - 增加 workflow 输入映射测试。

8. `tests/report/test_sub_report.py`
   - 增加依赖模式格式要求到最终提示词的集成测试。

### 6.3 功能文档

9. `docs/feature/algorithm/query-understanding.md`
   - 记录依赖 Outliner 的结构化章节格式契约和历史兼容边界。

10. `docs/feature/algorithm/report-generation/sub-report-generation.md`
    - 记录依赖写作路径对格式要求和章节局部契约的传递规则。

## 7. 明确不修改的文件与行为

以下生产文件不做修改：

- `openjiuwen_deepsearch/algorithm/prompts/outliner.md`
- `openjiuwen_deepsearch/algorithm/prompts/outliner_template.md`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/editor_team_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/section_context.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`

普通 `create_outline_tool()` 和公共 `_has_required_section_value()` 也不做修改。

## 8. 测试设计

### 8.1 依赖工具 schema

在 `test_dependency_outliner.py` 中验证：

- `format_requirements` 存在，类型为 array，元素类型为 string。
- `section_focus` 存在，类型为 string，`minLength == 1`。
- `focus_dimensions` 存在，类型为 array，`minItems == 1`。
- 三个字段均在依赖 section 的 required 列表中。
- `id`、`parent_ids`、`relationships` 仍为依赖工具额外必填字段。
- 依赖工具三个共享字段的 schema 与普通工具逐字段相等；测试只读取普通工具，不修改其实现。

### 8.2 工具调用契约

在 `test_dependency_outliner.py` 中参数化验证：

- 分别缺少 `format_requirements`、`section_focus`、`focus_dimensions` 时抛出
  `CustomValueException`，错误包含字段名。
- `format_requirements=[]` 合法。
- 空 `section_focus` 非法。
- 空 `focus_dimensions` 非法。
- 合法依赖 tool call 生成的 `Section` 完整保留格式要求、字段顺序、章节职责、分析维度及依赖关系。

### 8.3 依赖提示词

渲染两个依赖提示词并验证包含：

- `format_requirements`
- Markdown table
- exact column names/order
- required rows
- item-by-item enumeration
- source restrictions
- 无要求时使用 `[]`

同时验证提示词不再指示模型把表格、列、行、篇幅、样式或来源限制放进 `description`。

### 8.4 依赖写作节点

在 `test_dependency_writing_nodes.py` 中构造带有精确表格列名和章节局部契约的输入，调用
`SectionWritingStartNode.invoke()`，验证 session 内两个字段与输入完全一致。

补充默认值用例：字段缺失时分别得到 `[]` 和 `{}`。补充隔离用例：两个章节的不同契约不会互相污染。

### 8.5 工作流映射

在 `test_dependency_workflow.py` 中验证开始节点 schema 包含：

```text
section_format_requirements
section_local_contract
```

### 8.6 Reporter 集成

在 `test_sub_report.py` 中捕获依赖路径渲染后的子报告提示词，验证：

- `format_requirements` 不是空数组。
- 用户指定的列名、顺序和来源限制原样出现。
- `section_focus` 和允许的分析维度进入章节写作指令。
- 非最终决策章节保留禁止输出最终推荐的约束。

### 8.7 普通模式回归

运行普通 Outliner 与报告现有测试，证明合法普通输入行为没有变化。不为普通生产路径增加新逻辑。

## 9. 验证命令

定向测试：

```powershell
uv run pytest tests/algorithm/query_understanding/test_dependency_outliner.py -q
uv run pytest tests/workflow/test_dependency_writing_nodes.py -q
uv run pytest tests/workflow/test_dependency_workflow.py -q
uv run pytest tests/report/test_sub_report.py -q
```

普通模式回归：

```powershell
uv run pytest tests/algorithm/query_understanding/test_outliner.py -q
```

相关模块回归：

```powershell
uv run pytest tests/algorithm/query_understanding tests/workflow tests/report -m "not llm" -q
```

完整非实时测试与静态验证：

```powershell
uv run pytest -m "not llm"
uv run python -m compileall -q openjiuwen_deepsearch server
git diff --check
```

## 10. 验收标准

1. 普通大纲生产代码和提示词无改动。
2. 依赖工具为每个新章节生成 `format_requirements`、`section_focus` 和 `focus_dimensions`。
3. 依赖模式不再依赖 `description` 保存输出格式约束。
4. 用户给定的表格列名、顺序、逐项要求和来源限制原样保留。
5. 依赖写作子图收到非空 `section_format_requirements` 和正确的 `section_local_contract`。
6. 最终子报告提示词包含真实格式要求和章节局部契约。
7. 历史 Outline 缺少新字段时仍能加载。
8. 普通 Outliner 现有测试全部通过。
9. 依赖模式新增定向测试和非 LLM 回归测试全部通过。

## 11. 风险与控制

- **schema 重复风险**：依赖与普通工具继续分别维护字段定义。通过测试比较三个共享字段 schema 控制漂移。
- **提示词遵循风险**：模型仍可能输出不理想的字段内容。通过 required、非空约束和提示词渲染测试降低风险。
- **历史兼容风险**：不修改 Pydantic 默认值，不要求历史数据补字段。
- **普通模式回归风险**：不修改普通生产代码和提示词，仅运行现有回归测试。
- **字段传递回归风险**：对 workflow 输入映射、开始节点 session 状态和最终 Reporter prompt 分层测试。
