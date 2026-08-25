# 时间约束软过滤

## 维护范围

本文档覆盖 professional 报告路径下的「时间约束软过滤」特性：从用户查询提取时间约束、在选材与查询生成中尊重约束、并在低信号场景自动退出。brief 路径本期不含。

跨子系统，主 owner 为报告生成（选材加权）；相关模块：查询理解（约束分类）、资料采集（日期归一化）、搜索上下文（约束模型）。

## 功能目的

用户常以"截至 2022 年底""2020-2023 年间发表的论文"等限定研究的时间范围。本特性把这类约束结构化（`TemporalScope`），在不硬性砸召回的前提下，让选材偏好时间合规的段落、让查询生成把边界自然带进搜索词；并在候选池普遍缺日期信号时自动退出，避免在噪声上排序、挤压覆盖度。

## 可见行为

- 意图识别从 `original_query` 提取 `temporal_scope`（`constraint_type` ∈ {`source_date`, `content_date`} + 可选 `start_date`/`end_date`），无需用户显式开关。
- `content_date`（事实/事件/研究/数据时段有界，新发综述可接受）：选材排序键加入时效分，时间合规段落可反超覆盖度略低者；硬过滤不生效。
- `source_date`（来源发表/可得时间有界，含 as-of 快照）：查询生成把边界带进搜索词；选材纯按覆盖度（时效加权不生效）。
- 低信号自动退出：`content_date` 场景下，候选池有日期段落占比（`known_ratio`）越低，实际权重越趋零，满信号时回到固定权重上限。
- 摘段落的相对时间词换算、写作引用块的 `time` 字段、观测日志均消费归一化发表日期。

## 关键代码路径

- 意图识别：`openjiuwen_deepsearch/algorithm/query_understanding/intent_recognition.py`；Prompt `algorithm/prompts/intent_recognition.md`、`intent_recognition_entry.md`
- 日期归一化：`openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`（`_normalize_web_search_item`）；证据挂接 `algorithm/research_collector/collector_evidence.py`
- 日期基础设施：`openjiuwen_deepsearch/algorithm/report/date_utils.py`（`parse_date_window`/`parse_content_window`/`classify_temporal`/`timeliness_score`/`parse_published_date`）
- 选材加权：`openjiuwen_deepsearch/algorithm/report/report.py`（`_select_by_rationale_coverage`）
- 约束模型：`framework/openjiuwen/agent/search_context.py`（`TemporalScope`）

主要测试：

- `tests/report/test_date_utils.py`
- `tests/info_collector/algorithm/test_collector_function.py`
- `tests/report/test_select_by_rationale_coverage_temporal.py`
- `tests/algorithm/query_understanding/test_intent_recognition.py`
- `tests/algorithm/query_understanding/test_intent_temporal_llm.py`（llm 标记，opt-in）

## 核心流程

1. **约束提取**：意图识别 LLM 按 `intent_recognition*.md` 规则提取 `temporal_scope`。分类判定原则——时间词修饰载体（来源发表/可得性）→ `source_date`；修饰主题（事实/研究/数据时段）→ `content_date`，即使句中含"研究/成果/论文/文献"字样（旧研究的最佳综述可能是新发表的，按发表时间过滤会误杀）。as-of 快照语义仅在用户明确要求语料按可得性截断时归 `source_date`。
2. **日期归一化**：`_normalize_web_search_item` 在 `include_date_metadata=True` 时，Tavily 走已归一化的 `source_date`+`source_date_type=="published"`（严格 ISO 解析）；无则按序取原生 `published`/`published_at`/`published_date`（容错解析：严格 `YYYY-MM-DD`、ISO 8601 前缀如 `2023-01-15T12:00:00Z`、PubMed `YYYY Mon DD`）。不读语义含糊的裸 `date` 键；解析不出不附加（没日期不罚）。Google 与通用路径（pubmed/arxiv 走通用路径）均已开启该开关；Tavily 路径另在 source_date 场景做硬过滤。
3. **选材加权**（`content_date` 场景）：`_select_by_rationale_coverage` 对每个 rationale 按 `覆盖分 + effective_weight × 时效分` 降序取 top-k。`effective_weight = CONTENT_DATE_TIMELINESS_WEIGHT × known_ratio`，`known_ratio` = 候选池四档中非 unknown 占比。四档 compliant/partial/violation/unknown 对应 +1.0/-0.3/-1.0/0.0。保留门要求段落最大覆盖度 ≥ 0.15（与下游 L1 过滤对齐，避免加权提升结果被 L1 撤销；门槛只看原始覆盖分，时效惩罚不硬删合规段落；全池低于门槛时退回 `score>0` 老门兜底）；**并集补回（union-restore）**：每个 rationale 再回放一遍纯覆盖度 top-k 基线（含同一地板门），被时间加权挤掉的成员里**仅 unknown 档（判不出日期）补回池子**——"没日期不罚"护无辜；violation/partial 档扣分是其实际证据挣来的，留在池底不补回（A/B 实测：纯加权零和，普通子集掉 5~15pp；unknown-only 补回后普通子集回稳且时间增益保留）；补回按"每 rationale 交付 ≤ 15 条"封顶（对齐下游 L2 的 top-15 截断，默认 top_k=15 时饱和 rationale 不触发补回）；`source_date`/无约束退化为纯覆盖度。

## 数据契约与依赖

- `TemporalScope`：`constraint_type`（必填）、`start_date`/`end_date`（可选 `date`，ISO 闭区间）。
- `date_metadata`（归一化记录）：`field`（`source_date` 或命中的 `published*` 键）、`type="published"`、`value`（原始串）、`parsed_date`（`YYYY-MM-DD`，仅解析成功时附加）。
- 证据层 `doc_time`/`publish_time`：取自 `date_metadata.parsed_date`（`type=="published"` 时）。
- 常量：`CONTENT_DATE_TIMELINESS_WEIGHT`（`report.py`，0.2，content_date 排序权重上限；实际 = × known_ratio）。

## 边界与错误处理

- 硬过滤 `_apply_temporal_filter` 仅 Tavily 路径、仅 source_date 场景调用；非 Tavily 文档即使有日期也不被硬删。
- `known_ratio=0`（全 unknown）→ `effective_weight=0` → 纯覆盖度排序；`known_ratio=1` → 等价固定权重。低信号自动退出，无需新阈值。
- 摘段落 `content_time` 起止倒置视为无效、判 unknown（不奖不罚）。
- 意图识别 LLM 无 tool_call 或异常时回退为空 `temporal_scope`（不阻断流程）。

## 测试与验证

```bash
uv run pytest tests/report/test_date_utils.py tests/info_collector/algorithm/test_collector_function.py tests/report/test_select_by_rationale_coverage_temporal.py tests/algorithm/query_understanding/test_intent_recognition.py
# LLM 分类回归（opt-in，需 general LLM 凭证）：
RUN_LLM_TESTS=1 uv run pytest tests/algorithm/query_understanding/test_intent_temporal_llm.py
```

## 相关文档

- [信息维度矩阵段落选择](./algorithm/report-generation/coverage-matrix-doc-selection.md)
- [资料采集](./algorithm/research-collector.md)
- [查询理解](./algorithm/query-understanding.md)
- [搜索上下文与数据契约](./framework/search-context.md)
- 设计文档：`docs/superpowers/specs/2026-08-21-temporal-soft-filter-design.md`、`docs/superpowers/specs/2026-08-22-temporal-iteration2-design.md`
