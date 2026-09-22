# 用户素材（User Materials）

## 接口

在 run 请求的 `metadata` 中传入 `user_materials_enabled: true` 和 `user_materials`。每条素材必须包含非空 `content`，可选 `material_id`、`title`、`url`、`publish_time`、`content_time`。启用时所有素材 `content` 的总长度不得超过 500 万字符；关闭开关时素材会被忽略；系统不依据 URL 或标题抓取正文。

```json
{"metadata":{"user_materials_enabled":true,"user_materials":[{"content":"..."}]}}
```

## 流程

1. StartNode 只保存原始素材，不做校验、去重、摘要或筛选。
2. IntentRecognitionNode 集中完成校验、去重和数量限制；去重后按输入顺序最多保留 50 条。
3. 节点根据原始 query 保守筛除无关素材：仅在存在可比较的同语种词面信号且零重叠时过滤；跨语言素材保留给意图识别的语义判断。对于用户明确要求“基于/总结提供素材”的请求，不做该词面硬过滤，避免误删表述不同但语义相关的素材。被筛除的资料不会进入摘要、prompt、规划或报告证据。
4. 保留的素材按长度直通、单篇摘要或单篇 map-reduce；摘要按 `content_hash` 跨轮复用，并围绕原始 query 组织事实和结论。
5. 意图识别的既有 LLM 调用同时输出 `material_relevance_map`：素材与 query 的相关性、可支持的论点、范围/局限和待补研究缺口，不增加额外调用。
6. 预处理结果写入 `search_context.material_analysis`，直接透传到专业版和 Brief 的提纲、章节规划及报告写作。提纲将每章的素材用途写入 `material_bindings`。
7. Planner 仅可声明已知的 `use_material_ids`，并优先继承章节绑定；写作阶段将声明素材合并为可溯源证据。
8. 大纲生成后的素材 ID 会按预处理清单规范化（例如将 `[M1]` 还原为 `M1`）；无效绑定不会静默导致空素材章节，素材覆盖章节会回退注入可用素材。
9. 对“基于/总结所提供素材”的 Brief 请求启用 `material_first`：用户素材优先进入章节写作证据，网页检索仅补足素材分析未覆盖的主张或研究缺口，不增加 LLM 调用次数；若没有缺口，允许空检索并直接进入素材合并和写作。专业版仅在章节绑定素材被判为 `direct` 或 `partial`、具备可用论点且没有研究缺口时，才允许跳过联网检索。
10. 日志输出 `[MATERIAL_ROUTE]`，包含每章节声明、解析到的素材数、素材/网页证据数和素材 ID，便于验证素材是否实际进入写作。

没有信息盘点节点或盘点状态；提纲和规划直接使用已筛选的素材清单，并按需要补充检索。

## 主要实现

当意图工具未返回 `material_relevance_map` 时，系统按保留的素材生成保守回退映射。对于明确要求基于素材写作的请求，意图阶段会固化 `required` 使用策略；该策略、章节绑定的素材 ID 和素材摘要会传入 Planner、Collector Supervisor 与写作阶段。Supervisor 将绑定素材作为既有证据，只对素材未覆盖的缺口补充网页检索，因此不会增加新的 LLM 调用。

- `algorithm/query_understanding/material_processing.py`
- `framework/openjiuwen/agent/main_graph_nodes.py`
- `algorithm/query_understanding/planner.py`
- `algorithm/report/report.py`、`algorithm/brief_report/collector.py`

## 验证

```bash
uv run pytest tests/algorithm/query_understanding/test_material_processing.py
uv run pytest tests/algorithm/query_understanding/test_planner_materials.py
uv run pytest tests/brief_report/test_material_merge.py
```
