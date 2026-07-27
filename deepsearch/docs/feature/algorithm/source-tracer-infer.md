# 推理链溯源

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/source_tracer_infer/` 下的推理链溯源能力，包括结论提取、证据筛选、推理生成、推理结构化、节点编号、图修补、HTML 生成和报告标注。

本文档不覆盖全局 citation 生成和报告正文写作。

## 功能目的

推理链溯源用于在报告已有来源引用的基础上，针对报告结论生成“结论如何由证据推出”的结构化推理图，并把图以 base64 HTML 形式返回给前端展示。

## 可见行为

- 输入报告和溯源结果后，系统提取结论及其对应搜索记录。
- 每个结论独立生成推理、结构化关系和图信息。
- 无法生成有效 HTML 的结论会被跳过，不影响其他结论。
- 输出包含修改后的报告、infer messages、校验所需 graph infos 和错误信息。

## 关键代码路径

- 推理链入口：`openjiuwen_deepsearch/algorithm/source_tracer_infer/infer.py`
- 结论资料提取：`openjiuwen_deepsearch/algorithm/source_tracer_infer/infer_extract_info.py`
- LLM 调用与校验：`openjiuwen_deepsearch/algorithm/source_tracer_infer/infer_call_model.py`
- 节点编号：`openjiuwen_deepsearch/algorithm/source_tracer_infer/number_node.py`
- 图修补：`openjiuwen_deepsearch/algorithm/source_tracer_infer/supplement_graph.py`
- HTML 生成：`openjiuwen_deepsearch/algorithm/source_tracer_infer/generate_html.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/infer_validate_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/infer_conclusion_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/infer_filter_inference_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/infer_structured_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/infer_supplement_prompt.md`
- `openjiuwen_deepsearch/algorithm/prompts/infer_extract_conclusion_prompt.md`

主要测试：

- `tests/source_tracer_infer/test_infer.py`
- `tests/source_tracer_infer/test_number_node.py`
- `tests/source_tracer_infer/test_supplement_graph.py`

## 核心流程

1. `SourceTracerInfer.run` 获取报告、语言、模型名和 source tracer response。
2. 如未提供 `conclusion_with_records`，先从报告和溯源信息中提取结论及搜索记录。
3. 每个结论并发执行 `async_run`。
4. 筛选与结论相关的引用资料。
5. LLM 生成推理过程，并过滤低质量推理。
6. 推理被结构化为节点关系。
7. `NumberNode` 给 citation、结论和中间推理节点编号。
8. `SupplementGraph` 删除自环、修补非连通图并剪枝。
9. HTML 生成后编码为 base64，并把推理标记写回报告。

## 数据契约与依赖

输入 context：

- `language`
- `llm_model_name`
- `source_tracer_response`
- `conclusion_with_records`

输出：

- `response`：标注推理内容后的报告。
- `infer_messages`：包含结论、推理和 `html_base64`。
- `checker_infos.graph_infos`
- `checker_infos.search_records`
- `error`

## 边界与错误处理

- 单个结论处理失败时返回空 infer message，不应阻塞其他结论。
- 结论或搜索记录为空时跳过当前推理。
- LLM 输出必须经过类型和长度检测。
- 图修补会删除自环、捏造节点和不连通的非关键子图。
- HTML base64 会做反解校验，失败时抛出异常并跳过当前结论。
- 敏感日志模式下不输出结论正文、搜索记录或推理文本。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer_infer
```

## 相关文档

- [全局溯源](./source-trace.md)
- [报告生成](./report-generation.md)
- [Prompt 模板系统](./prompt-template-system.md)
