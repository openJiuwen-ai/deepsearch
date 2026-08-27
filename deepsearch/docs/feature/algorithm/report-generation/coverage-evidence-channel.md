# Coverage Evidence 覆盖通道

## 维护范围

本文档覆盖子报告大纲阶段的**覆盖证据（Coverage Evidence）通道**：从原文中按客观信息密度
规则抽取的、与关键词检索无关的事实段落，作为大纲生成的补充证据输入。与已有的
`key_passages`（相关性检索）并行，属于"方案 B"的工程实现。

路径：`openjiuwen_deepsearch/algorithm/report/report.py::_get_classified_infos`、
`openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`、
`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py::build_coverage_passage_block`、
Prompt `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline.md`。

本文件不覆盖：写作阶段 prompt（方案 A，见 `sub_report_markdown.md`）、按 `section_iscore` 分级（未实现）。

## 功能目的

`extract_key_passages` 是基于 query/title 关键词命中式的规则抽取，原文中不包含提问关键词但
确有数字/日期/实体/引用等高信息密度的事实，会在采集阶段被丢弃，大纲因此"看不见"它们、
不为它们安排结构，造成结构性事实遗漏。Coverage 通道在抽取逻辑上与关键词无关，按客观
信息密度打分，兜住关键词检索漏掉的事实，降低大纲阶段的结构性事实遗漏。

## 可见行为

布局与设计文档见 `docs/feature/algorithm/report-generation/sub-report-fact-loss-analysis.md`。
与 `structured_evidence_guide`（维度级覆盖信号）并存，但定位为**段落级**补充。

- `_get_classified_infos` 为每个选中文档额外抽取覆盖证据，去重后聚合成一个
  `===== COVERAGE PASSAGES =====` 块，追加到 `core_content_list` 末尾（key 块在前、
  coverage 块在后、不交错）。
- 纯叙述、无事实特征的文档不产生覆盖证据；所有文档都无有效事实时不追加该块。
- 覆盖证据与同文档 `key_passages` 重复（相同/高相似/高占比子串）时被剔除，避免 token 冗余。
- 大纲 prompt（`sub_section_outline.md`）新增 `## Evidence Channels` 说明两路证据语义；
  证据边界从"仅 key passages"放宽为"key passages + coverage passages"；明确覆盖证据
  不强制开新标题，遵守"证据不改结构"规则。

## 关键代码路径

- 抽取函数：`collector_evidence.py::extract_coverage_passages`（进程内有界缓存接口）、
  `collector_evidence.py::exclude_passages`、`CoveragePassage`
- 组装：`report.py::_get_classified_infos`、`compact_doc_info.py::build_coverage_passage_block`
- Prompt：`algorithm/prompts/sub_section_outline.md`
- 离线评估：`tools/evaluate_coverage.py`
- 测试：`tests/info_collector/algorithm/test_coverage_evidence.py`、
  `tests/report/test_sub_report.py`、`tests/tools/test_evaluate_coverage.py`

## 核心流程

1. 大纲输入装载时（`generate_sub_report` → `_get_classified_infos`），对每个矩阵选中文档：
   - `extract_coverage_passages(item.original_content, max_passages=_COVERAGE_TOP_K_CAP, max_chars=1200)`
     抽取覆盖证据块（结果按 `(content, 全部参数)` 做进程内有界缓存，
     `functools.lru_cache(maxsize=512)`，同章节重试/重生成不重复计算）；
     选段不预设硬 Top-K，由单文档字符预算兜底（方案2：预算即终止条件）；
   - `exclude_passages(coverage, item.key_passages)` 与同文档关键片段去重；
   - 裁入章节级总预算 `_COVERAGE_MAX_TOTAL_CHARS = 6000`（`_fit_coverage_to_budget`）。
2. 所有 key 块先进入 `core_content_list`；最终把一个聚合 coverage 块追加到末尾。
3. `_generate_sub_section_outline` 把 `sub_section_core_content`（含两路证据）与
   `structured_evidence_guide` 一起拼入 outline prompt。
4. 大纲模型依据两路证据设计子章节标题结构；覆盖证据中的事实归入最相关的既有标题，
   不要求为它单独开标题。

## 数据契约与依赖

- `CoveragePassage`：`text`、`score`、`source_indices`、`features`（number/date/time/
  entity/citation/structure），见 `collector_evidence.py`。
- Coverage Score：基础加权 `1.0*N + 2.0*D + 1.5*T + 1.5*E + 1.0*C`（`N` 封顶 5、计数
  特征封顶 3）。默认按段落长度归一化（`_COVERAGE_SCORE_MODE="density"`，方案1，经真实
  数据 A/B 后选定：覆盖率 47%→58%、Newly Covered 92→119、噪声 80%→77%）；`absolute`
  口径为原始加权 + `1.0*S`（信息结构，只对含事实特征段落生效）。`score_mode` 可经
  `extract_coverage_passages`/评估工具临时覆盖做 A/B。
- 数字特征复用共享的富版事实模式（`extract_key_passages` 与 coverage 共用同一
  `_NUMERIC_FACT_PATTERN`：单位分类/货币前缀/财年季度/学术统计/尾部边界）+ 裸数字
  兜底；日期内部数字由日期区间排除。季度/半年归入日期特征（权重 2.0），相对时间词
  （去年/今年/本季度…）归入时间特征（权重 1.5）。
- 合并跨度上限 `max_merge_span=5`：相邻高分段连续并入一个块超过该上限即另起新块，
  防止雪崩吞并；块间天然不共享段落。
- 近似去重（方案4 锚点级）：证据块内部的近似去重按"数值锚点键"重合率 ≥ `_COVERAGE_
  ANCHOR_DEDUP_RATIO=0.7` 判定——数值锚点按数字核心归一（"20%" 与 "20 个百分点" 同键），
  结构相似但版本/年份不同的事实不会被误删；`exclude_passages` 与 key 去重沿用
  "归一化相同 / 字符二元组 Jaccard ≥0.6 / 一方是另一方 ≥60% 占比子串"。
- 邻域扩展：`expansion_density_threshold`（方案3 密度门控，默认 0 关闭）。预算兜底
  （K≈候选全集）下邻居扩展为空操作，故默认关闭；参数保留供有限 K 场景评估。
- 集成常量（`collector_evidence.py` 唯一真源，`report.py` 与评估工具共用）：
  `_COVERAGE_TOP_K_CAP = 128`（选段数量上界，实际由预算兜底）、
  `_COVERAGE_MAX_CHARS_PER_DOC = 1200`、`_COVERAGE_MAX_TOTAL_CHARS = 6000`、
  `_COVERAGE_SCORE_MODE`、`_COVERAGE_DENSITY_MIN_LEN`。
- coverage 块格式：`===== COVERAGE PASSAGES =====` / `Document N coverage passages:`
  + `- passage`，`N` 与 key 块编号对齐。
- 不修改 `doc_info` / `classified_doc_infos`，写作阶段证据输入不受影响。

## 边界与错误处理

- 空内容、纯噪声（过短、标题、导航、无数字分隔行、**纯引用/链接列表**）、无任何事实
  特征 → 不产覆盖证据。
- 每个文档的覆盖证据经 `_fit_coverage_to_budget` 裁入剩余章节预算；预算放不下更高分
  证据块时整块丢弃，仅当第一个块即超出预算时截断该块，保证非空。
- `max_chars <= 0` 时 `extract_coverage_passages` 返回空列表。
- 规则版不引入 LLM/网络调用，无外部依赖。

## 测试与验证

- 定向：`uv run pytest tests/info_collector/algorithm/test_coverage_evidence.py`
- 组装与 prompt：`uv run pytest tests/report/test_sub_report.py tests/report/test_tools_in_report.py`
- 必须覆盖：事实密度优先于位置、噪声过滤、特征计数与封顶、Neighbor Expansion 与相邻合并、
  两级去重、字符预算、`exclude_passages` 去重、`_get_classified_infos` 追加 coverage 块、
  outline prompt 渲染两路证据语义、缓存键隔离与返回隔离。

## 相关文档

- 问题分析与方案设计：`docs/feature/algorithm/report-generation/sub-report-fact-loss-analysis.md`
- 子报告生成：`docs/feature/algorithm/report-generation/sub-report-generation.md`
- 技术评审：仓库根 `subreport_fact_loss_review.md`