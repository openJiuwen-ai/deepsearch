# 段落选择调试信息导出

## 维护范围

本文档覆盖信息维度矩阵段落选择流程的中间结果（rationales / 覆盖矩阵 / 维度评分 / 选中段落）写入 `Section.doc_selection_debug` 字段，并通过 `ResultExporter` 导出为 JSON 和 Excel 的能力。

本文档不覆盖段落选择算法本身（见 [coverage-matrix-doc-selection.md](../algorithm/report-generation/coverage-matrix-doc-selection.md)）和通用大纲导出（见 [debug-and-export.md](./debug-and-export.md)）。

## 功能目的

将段落选择流程的中间结果写入 `Section.doc_selection_debug` 字段，使其随 `Outline.model_dump()` 自动进入 `ResultExporter` 的 JSON 和 Excel 输出，便于结构化排查"哪些 section 的哪些维度系统性 uncovered"等问题，为调优选择参数提供数据支撑。

## 可见行为

- `Section` 模型新增可选字段 `doc_selection_debug: Optional[Dict]`，默认 `None`，不影响已有序列化。
- 仅在走段落选择流程（`doc_infos` 非空且进入 else 分支）时写入；背景知识回退路径不写入。
- `export_outline_without_plans` 在排除 `plans` 的同时排除 `doc_selection_debug`，确保调试数据不会泄漏到 LLM 提示词（报告生成、章节摘要、sidecar 等）。
- `ResultExporter.export_outline` 的 JSON 输出自动包含 `doc_selection_debug`（因为 `model_dump()` 序列化全部字段）。
- `OutlineToExcelExporter` 的 Excel 输出新增 2 个 sheet：信息维度、维度Top段落。
- 开关仍由 `export_intermediate_results` 控制，与现有大纲导出一致。

## 关键代码路径

- 数据模型：`openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py`（`Section.doc_selection_debug`）
- 采集写入：`openjiuwen_deepsearch/algorithm/report/report.py`（`_write_doc_selection_debug`）
- LLM 输入排除：`openjiuwen_deepsearch/algorithm/report/report.py`（`export_outline_without_plans`，排除 `plans` + `doc_selection_debug`）
- Excel 导出：`openjiuwen_deepsearch/utils/debug_utils/outline_visualization.py`（`_extract_doc_selection_debug`、`create_dataframes`、`export_to_excel`）
- 触发入口：`openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`（`ResultExporter.export_outline`）

## 核心流程

1. `generate_sub_report` 执行段落选择流程（rationale 生成 → 段落抽取+3 维度评分 → 按 rationale top-k 选择 → 覆盖校验），中间结果为局部变量。
2. `_write_doc_selection_debug` 将 6 类中间结果打包为 dict 并写入 `current_inputs["doc_selection_debug"]`。打包时通过 `PassageSelectionContext` dataclass 封装 4 个相关参数，函数签名从多参数简化为 2 参数。同时构建 `passage_info_map`（passage_key → {doc_title, doc_url, passage_text}）供 Excel 表展示文档标题和 URL。
3. `SubReporterNode._do_invoke` 将 `doc_selection_debug` 纳入 `algorithm_output`；`_post_handle` 将其写入 `session.update_global_state({"section_context.doc_selection_debug": ...})`。
4. `SectionEndNode.invoke` 从 session 读取 `section_context.doc_selection_debug` 并纳入子图最终 payload（`section_state` dict）。
5. `editor_team_manager_node._parse_section_state` 从子图 payload 提取 `doc_selection_debug`；`_update_state` 将其写入 `section.doc_selection_debug`。
6. `editor_team_manager_node` 在 sub_report 全部完成后调用 `ResultExporter.export_outline(state.get("outline"), ...)`，Outline 的 `doc_selection_debug` 被 `model_dump()` 序列化进 JSON。
7. `OutlineToExcelExporter.extract_all_data` 遍历 sections 时调用 `_extract_doc_selection_debug`，将 `doc_selection_debug` 展平为 2 张表的长格式行。
8. `create_dataframes` 构建 2 个 DataFrame 并设置中文列名。
9. `export_to_excel` 写入 2 个新 sheet，复用 `sanitize_dataframe` 防公式注入。

## doc_selection_debug 数据结构

`doc_selection_debug` 顶级键名保持不变（跨文件数据契约），但内部字段名已更新为段落级。

```json
{
    "rationales": [
        {"id": "r1", "description": "...", "type": "quantitative", "priority": "primary"}
    ],
    "doc_filter": {"before": 30, "after": 18},
    "coverage_matrix": {
        "passage_0": {"r1": 0.8, "r2": 0.3},
        "passage_1": {"r1": 0.1, "r2": 0.7}
    },
    "dimension_scores": {
        "passage_0": {
            "r1": {"coverage": 0.9, "reliability": 0.8, "data_density": 0.7, "total_score": 0.8},
            "r2": {"coverage": 0.3, "reliability": 0.5, "data_density": 0.4, "total_score": 0.3}
        },
        "passage_1": {
            "r1": {"coverage": 0.1, "reliability": 0.2, "data_density": 0.1, "total_score": 0.1},
            "r2": {"coverage": 0.7, "reliability": 0.8, "data_density": 0.6, "total_score": 0.7}
        }
    },
    "passage_info_map": {
        "passage_0": {"doc_title": "...", "doc_url": "...", "passage_text": "..."},
        "passage_1": {"doc_title": "...", "doc_url": "...", "passage_text": "..."}
    },
    "selected_passages": [
        {"passage_key": "passage_0", "doc_title": "...", "doc_url": "...", "passage_text": "..."}
    ]
}
```

各字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `rationales` | list | 该 section 的信息维度列表，每项含 `{id, description, type, priority}` |
| `doc_filter` | dict | 段落过滤前后的数量，`{before: int, after: int}` |
| `coverage_matrix` | dict | 每个段落对各 rationale 的总分，`{passage_N: {rationale_id: float}}`，值为 `dimension_scores` 中对应条目的 `total_score` |
| `dimension_scores` | dict | 每个段落对各 rationale 的 3 维度评分，`{passage_N: {rationale_id: {coverage, reliability, data_density, total_score}}}` |
| `passage_info_map` | dict | 段落元信息映射，`{passage_N: {doc_title, doc_url, passage_text}}`，供 Excel 表展示文档标题、URL 和段落内容 |
| `selected_passages` | list | 最终选中的段落列表，每项含 `{passage_key, doc_title, doc_url, passage_text}` |

注意：`selected_passages` 只保留 passage_key/doc_title/doc_url/passage_text 摘要，不保留完整文档内容，避免 JSON/Excel 体积膨胀。

## 新增 Excel sheet

### 信息维度（rationales）

| 章节ID | 章节标题 | 维度ID | 维度描述 | 维度类型 | 优先级 |
|--------|----------|--------|----------|----------|--------|

### 维度Top段落（rationale_top_passages）

长表格式，一行 = 一个 passage × rationale 组合，按 coverage_matrix 中的总分降序排列，每个 rationale 最多保留 top 15 条。

| 章节ID | 章节标题 | 维度ID | 维度描述 | 排名 | 段落键 | 文档标题 | 文档URL | 段落内容 | 覆盖分 | 可信分 | 数据密度 | 总分 |
|--------|----------|--------|----------|--------|--------|----------|---------|----------|--------|--------|----------|------|

各列来源：
- 排名：按 `coverage_matrix[passage_key][rationale_id]` 的值（即 `total_score`）降序排列，1-based
- 覆盖分：来自 `dimension_scores[passage_key][rationale_id].coverage`
- 可信分：来自 `dimension_scores[passage_key][rationale_id].reliability`
- 数据密度：来自 `dimension_scores[passage_key][rationale_id].data_density`
- 总分：来自 `coverage_matrix[passage_key][rationale_id]`（即 `dimension_scores` 中的 `total_score`）

## 数据契约与依赖

输入依赖（来自段落选择流程）：

- `rationales`：`_generate_section_rationales` 返回的 list[dict]
- `coverage_result`：`_extract_and_score_documents` 返回的 dict，含 `filtered_passages` / `coverage_matrix` / `dimension_scores`
- `selected_passages`：`_select_by_rationale_coverage` 后的最终选择

输出挂载点：

- `Section.doc_selection_debug: Optional[Dict]`，按 `section_idx`（1-based）定位 Section

回传通道：

- `generate_sub_report` 将 debug 数据写入 `current_inputs["doc_selection_debug"]`
- `SubReporterNode._do_invoke` 纳入 `algorithm_output`；`_post_handle` 写入 `session.update_global_state({"section_context.doc_selection_debug": ...})`
- `SectionEndNode.invoke` 从 session 读取 `section_context.doc_selection_debug` 并纳入子图最终 payload（`section_state` dict）
- `editor_team_manager_node._parse_section_state` 从子图 payload 提取 `doc_selection_debug`（与 `sub_report_content`/`plans` 同通道）
- `_update_state` 将 `result.get("doc_selection_debug")` 写入 `section.doc_selection_debug`（与 `section.plans = result.get("plans")` 同模式）
- `ResultExporter.export_outline(state.get("outline"), ...)` 导出含 `doc_selection_debug` 的 Outline

## 边界与错误处理

- 走背景知识回退路径（`doc_infos` 为空）时不写入 `doc_selection_debug`，字段保持空 dict。
- `doc_selection_debug` 为空 dict 的 section 不产生行，对应 sheet 为空时自动跳过。
- 所有字符串单元格经 `sanitize_dataframe` 转义公式前导字符。

## 修改文件清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `openjiuwen_deepsearch/framework/openjiuwen/agent/search_context.py` | 新增字段 | `Section` 新增 `doc_selection_debug: Optional[Dict]` |
| `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/section_context.py` | 新增字段 | `SectionContext` 新增 `doc_selection_debug: Dict`（保证 session dotted key 保留） |
| `openjiuwen_deepsearch/algorithm/report/report.py` | 修改 + 新增方法 + 新增 dataclass | 新增 `PassageSelectionContext` dataclass 封装 4 个参数；新增 `_write_doc_selection_debug` 写入 `current_inputs["doc_selection_debug"]`；在 `generate_sub_report` 末尾调用 |
| `openjiuwen_deepsearch/framework/openjiuwen/agent/reasoning_writing_graph/editor_team_nodes.py` | 修改 | `_do_invoke` 将 `doc_selection_debug` 纳入 `algorithm_output`；`_post_handle` 写入 session；`SectionEndNode.invoke` 从 session 读取并纳入 payload |
| `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py` | 修改 | `_parse_section_state` 提取 `doc_selection_debug`；`_update_state` 写入 `section.doc_selection_debug` |
| `openjiuwen_deepsearch/utils/debug_utils/outline_visualization.py` | 新增方法 + sheet | 新增 `_extract_doc_selection_debug`；`create_dataframes` 新增 2 个 DataFrame；`export_to_excel` 新增 2 个 sheet 写入 |
| `tests/report/test_doc_selection_debug_export.py` | 新增测试 | 覆盖打包、提取、端到端一致性、模型字段、LLM 输入排除、DataFrame 集成 |

## 测试与验证

```bash
cd d:\Jiuwen\deepsearch-dev\deepsearch

# 段落选择算法测试
python -m pytest tests/report/test_doc_selection.py -x -q

# 段落选择调试导出测试
python -m pytest tests/report/test_doc_selection_debug_export.py -x -q
```

## 相关文档

- [信息维度矩阵段落选择](../algorithm/report-generation/coverage-matrix-doc-selection.md)
- [调试与中间结果导出](./debug-and-export.md)
