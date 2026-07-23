# 调试与中间结果导出

## 维护范围

本文档覆盖节点格式化 debug 日志、大纲中间结果导出和大纲 Excel 可视化。

不覆盖通用日志和接口日志初始化；该部分见 [日志与接口记录](./logging.md)。

## 功能目的

调试导出工具为本地排查复杂 workflow 提供结构化节点输入/输出记录和中间大纲文件，便于定位大纲、章节、plan、step 和检索 query 的问题。

## 可见行为

- `node_debug_enable=True` 时，节点 debug logger 会写入 `node_debug_log/`。
- 每条节点 debug 记录是 JSON，包含前一节点、当前节点、消息 id、输入/输出类型、节点层级和内容。
- 敏感模式下，debug `content` 会写成 `***`。
- `export_intermediate_results=True` 时，`ResultExporter.export_outline` 会导出大纲 JSON 和 Excel。
- 大纲导出文件名会清理不安全字符，并包含 title、session_id 和 UTC 时间戳。
- Excel 导出会对公式前导字符做转义，降低公式注入风险。

## 关键代码路径

- `openjiuwen_deepsearch/utils/debug_utils/node_debug.py`
- `openjiuwen_deepsearch/utils/debug_utils/result_exporter.py`
- `openjiuwen_deepsearch/utils/debug_utils/outline_visualization.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/editor_team_manager_node.py`
- `openjiuwen_deepsearch/utils/log_utils/log_manager.py`
- `tests/workflow/test_workflow_run.py`
- `tests/framework/test_background_knowledge.py`

## 核心流程

1. `LogManager.init` 根据 `Config().service_config.node_debug_enable` 决定是否初始化 debug logger。
2. 节点调用 `add_debug_log_wrapper`，传入 `NodeDebugData`。
3. wrapper 读取 `search_context.debug_pre_node`，为当前节点生成唯一 id。
4. 输入和输出内容分别写为 debug JSON，并更新 `debug_pre_node`。
5. `EditorTeamNode` 等节点在适当阶段调用 `ResultExporter.export_outline`。
6. exporter 校验输出目录，写 JSON，并通过 `OutlineToExcelExporter` 生成多 sheet Excel。

## 数据契约与依赖

- `NodeType` 包含 `main` 和 `sub`。
- `LogType` 包含 `input` 和 `output`。
- `ResultExporter` 默认安全基目录为 `./output/results`。
- 导出目录必须通过 `ensure_safe_directory` 校验。
- Excel sheet 覆盖 outline、sections、plans、steps、retrieval query docs、doc infos、toc 以及文档选择调试（信息维度、覆盖矩阵、文档选择、覆盖校验）等结构。

## 边界与错误处理

- `NODE_DEBUG_ENABLE` 在模块加载时从配置读取，运行时改配置不会自动改变已导入模块的开关。
- 导出目录不安全或创建失败时，会禁用中间结果导出并记录 warning。
- `export_outline` 收到非 `Outline` 或 dict 时直接返回。
- 导出失败只记录 error，不中断 workflow。

## 测试与验证

- `uv run pytest tests/workflow/test_workflow_run.py`
- `uv run pytest tests/framework/test_background_knowledge.py`
- 修改安全目录逻辑时，补充运行 `uv run pytest tests/utils/test_log_manager.py`。

## 相关文档

- [日志与接口记录](./logging.md)
- [参数校验、安全目录与 URL 处理](./validation-security-url.md)
- [章节推理与写作子工作流](../framework/section-reasoning-writing-sub-workflows.md)
- [文档选择调试信息导出](./doc-selection-debug-export.md)
