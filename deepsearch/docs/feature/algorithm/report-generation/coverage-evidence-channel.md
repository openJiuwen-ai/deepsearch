# Coverage Evidence 覆盖通道

## 维护范围

本文档覆盖子报告大纲阶段的**覆盖证据（Coverage Evidence）通道**：从原文中按客观信息密度
规则抽取的、与关键词检索无关的事实段落，作为大纲生成的补充证据输入。与已有的
`key_passages`（相关性检索）并行，属于"方案 B"的工程实现。

路径：`openjiuwen_deepsearch/algorithm/report/evidence.py::_append_rule_coverage_to_core`、
`openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`、
`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py::build_coverage_passage_block`、
Prompt `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline.md`。

本文件不覆盖：写作阶段 prompt（方案 A，见 `sub_report_markdown.md`）、按 `section_iscore` 分级（未实现）。

## 功能目的

`extract_key_passages` 是基于 query/title 关键词命中式的规则抽取，原文中不包含提问关键词但
确有数字/日期/实体/引用等高信息密度的事实，会在采集阶段被丢弃，大纲因此"看不见"它们、
不为它们安排结构，造成结构性事实遗漏。Coverage 通道在抽取逻辑上与关键词无关，按客观
信息密度打分，兜住关键词检索漏掉的事实，降低大纲阶段的结构性事实遗漏。

## 章节生成两阶段的证据输入来源

子报告章节的生成分大纲与写作两个阶段，各阶段输入的 channel 构成如下：

**大纲阶段**（`_generate_sub_section_outline`，prompt：`sub_section_outline.md`）：
- 章节结构上下文：章节标题/描述/格式要求、全局大纲、section_local_contract、
  research_intent、背景知识特例（`sub_section_core_content_from_background_knowledge`）；
- `sub_section_core_content`（顺序拼接，不交错）：
  1. 条目摘要块（`build_core_content_list`）：fulltext 条目渲染清洗后原文前 500 字符
     （`outline_summary_text`），passage 条目渲染被选中的段落；
  2. 规则版 coverage 聚合块（`===== COVERAGE PASSAGES =====`，本 PR 范畴）；
- `structured_evidence_guide`（维度级覆盖状态表，rationale 链路产出）。

**写作阶段**（`write_subsection_reports`，prompt：`sub_report_markdown.md`）：
- `classified_content`（写作主证据）：fulltext 条目整篇原文 + passage 条目选中段，
  经 `build_citation_infos` 渲染为 `[citation:X]` 块；
- 本章提纲（大纲阶段产物，写作边界）、章节格式要求、背景知识等上下文。

LLM 压缩增量、直达注入虚拟条目等增强 channel 为后续 PR 规划，未在本 PR 落地。

## 可见行为

与 `structured_evidence_guide`（维度级覆盖信号）并存，但定位为**段落级**补充。

- `_append_rule_coverage_to_core` 为每个全文证据额外抽取覆盖证据，去重后聚合成一个
  `===== COVERAGE PASSAGES =====` 块，追加到 `core_content_list` 末尾（key 块在前、
  coverage 块在后、不交错）。
- 纯叙述、无事实特征的文档不产生覆盖证据；所有文档都无有效事实时不追加该块。
- 覆盖证据与该文档**大纲摘要块渲染文本**重复时被剔除（方案乙口径，见下"去重基准"），
  避免 token 冗余。
- 大纲 prompt（`sub_section_outline.md`）新增 `## Evidence Channels` 说明两路证据语义；
  证据边界从"仅 key passages"放宽为"key passages + coverage passages"；明确覆盖证据
  不强制开新标题，遵守"证据不改结构"规则；`Document N key passages:` /
  `Document N coverage passages:` 头部与 `===== COVERAGE PASSAGES =====` 分隔符
  是溯源元数据，不得复述进子章节标题（prompt 措辞与实际块格式由
  `test_subsection_outline_prompt_provenance_tokens_match_actual_block_format` 绑定）。

### 提示注入攻击面与缓解（PR !380 审核意见）

归因口径（准确区分基线与增量）：大纲阶段在本次改动前已接收 key passages 与全文
前 500 字符（`report_rationale_fulltext.py::build_core_content_list`），基础提示
注入风险并非本通道引入。本通道的增量是**全文 Coverage 扫描**——原来位于正文
第 500 字符之后、不进入大纲 Prompt 的内容（最多 6000 字符/章节）从此可被主动
提取；恶意指令旁若带有年份/金额等事实锚点会获得较高 Coverage Score 而被选中。

两层缓解：

- **隐藏内容剥离**（`collector_evidence.py::_normalize_coverage_content`）：
  `<script>/<style>` 连同载荷整体删除、HTML 注释整体删除（先于普通标签剥离，
  防止载荷剥壳后以纯文本残留）；渲染页面上可见的正常文本不受影响。注意这是
  证据抽取侧的清理，仅作用于 coverage 通道；key 通道与前 500 字符的既有注入
  面不因此扩大或缩小。
- **信任边界**（`sub_section_outline.md` Evidence Channels）：明确两路证据均为
  不可信网页内容，仅作为事实数据使用；忽略其中嵌入的指令、角色变更、输出格式
  覆盖与工具请求；报告结构、输出格式与语言只服从大纲 prompt 本身。措辞对齐
  仓库既有先例（`rationale_generator.md`、`brief_evidence_review.md`），
  由 `test_subsection_outline_prompt_untrusted_evidence_boundary` 绑定。

## 关键代码路径

- 抽取函数：`collector_evidence.py::extract_coverage_passages`（进程内有界缓存接口）、
  `collector_evidence.py::exclude_passages`、`CoveragePassage`
- 组装：`report/evidence.py::_append_rule_coverage_to_core`、`compact_doc_info.py::build_coverage_passage_block`
- Prompt：`algorithm/prompts/sub_section_outline.md`
- 测试：`tests/info_collector/algorithm/test_coverage_evidence.py`、
  `tests/report/test_sub_report.py`

## 核心流程

1. 大纲输入装载时（`generate_sub_report` → `enrich_fulltext_for_section` + `_append_rule_coverage_to_core`），对每个全文证据：
   - `extract_coverage_passages(item.original_content, max_passages=_COVERAGE_TOP_K_CAP, max_chars=1200)`
     抽取覆盖证据块（结果按 `(content, 全部参数)` 做进程内有界缓存，
     `functools.lru_cache(maxsize=512)`，同章节重试/重生成不重复计算）；
     选段不预设硬 Top-K，由单文档字符预算兜底（方案2：预算即终止条件）；
   - `exclude_passages(coverage, [outline_summary_text(item.original_content)])`
     与该文档的摘要块渲染文本去重（见下"去重基准"）；
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
  口径为原始加权 + `1.0*S`（信息结构，只对含事实特征段落生效）。`score_mode` 经
  `CoverageOptions(score_mode=...)` 传入 `extract_coverage_passages` 的 `options`
  参数临时覆盖做 A/B。
- 数字特征：coverage 通道使用自有富版事实模式 `_COVERAGE_NUMBER_PATTERN`（单位分类/
  货币前缀/学术统计/尾部边界 + 裸数字兜底），与 key 通道 `extract_key_passages` 的
  内联窄正则相互独立、不共用模式；日期内部数字由日期区间排除。季度/半年归入日期特征
  （权重 2.0），相对时间词（去年/今年/本季度…）归入时间特征（权重 1.5）。
- 实体特征：中文侧以"前缀+机构后缀"结构信号判定（`_COVERAGE_ENTITY_SUFFIXES`）；
  英文侧同样只认正字法结构信号——词内第二处大写字母（OpenAI/NASA/iPhone/U.S，与
  位置无关）或非句首的首字母大写词（"at Microsoft" 的 Microsoft），连续 Title 词
  序列（Goldman Sachs / 标题行 Markets Fall Again）压缩计 1 个实体；序列连续性按
  token 间间隔判定，只有纯水平空白延续序列，间隔含逗号/顿号/数字即重置
  （"Apple, Microsoft, Google" 列举各自计数）。句首首字母
  大写是英文书写规范而非专名信号，不作为判定依据（PR !380 评审意见：句首普通词
  Revenue/However 被误判为实体，纯叙述英文整段涌入 coverage）；句首专名（"Microsoft
  announced..."）因此漏检，由 key 通道关键词兜底。计数（`_count_entities`）与锚点
  提取（`extract_fact_anchors`）共用 `_iter_english_entities` 同一口径。
- 合并跨度上限 `max_merge_span=5`：相邻高分段连续并入一个块超过该上限即另起新块，
  防止雪崩吞并；块间天然不共享段落。
- 近似去重（方案4 锚点级，v2 口径）：证据块内部的近似去重按"锚点键"重合率 ≥
  `_COVERAGE_ANCHOR_DEDUP_RATIO=0.85` 判定。数值锚点键保留原文（数字/小数点/
  正负号/单位/量级词一律不折叠），仅规整千分位、全半角百分号与数字后排版空白
  （`1,000`≡`1000`、`20%`≡`20％`、`3 万`≡`3万`）；因此 `20%` 与 `20个百分点`
  不同键、`1.5亿元` 与 `15%` 不同键。单锚点块跳过近似去重——一个共享数字不足
  以判定"同一事实的换措辞"（如"收入增长20%"与"成本下降20%"）。结构相似但
  版本/年份/单位/量级不同的事实不会被误删。

### 去重基准（方案乙：与大纲实际渲染文本去重）

rationale 选材接管证据选择后，`key_passages` 通道已退役（不再进入大纲/写作
prompt，仅保留 target-paper 兜底用途），不再作为去重基准——与一个已退场通道
去重防不了真实的重复供给。去重基准改为**条目摘要块的实际渲染文本**（与
`report_rationale_fulltext.build_core_content_list` 渲染口径一致）：

- fulltext 条目：清洗后原文前 500 字符（`collector_evidence.py::outline_summary_text`，
  `OUTLINE_SUMMARY_MAX_CHARS = 500` 单一真源）；
- passage 条目：被选中的 `passage_text`（当前规则块只对 fulltext 条目运行，此条
  供后续复用方沿用同一口径）。

判定共享谓词 `collector_evidence.py::_supplied_by_basis`（`exclude_passages`
的底层谓词；候选池等后续复用方在同一口径上实现，防漂移）：

- 文本层重叠 = 完全相同 / 段落整体落在基准内部（基准远大于段落的包含情形）/
  高相似或高占比子串；
- **锚点救援**：文本层重叠、但段落携带基准之外锚点的段落保留——"前 500 字符
  叙述 + 新事实句"的扩展块是覆盖通道要供给的形态，不因与前缀基准相似被整段
  判重；锚点是"有无新事实"的廉价判据。宁可多供（token 冗余）不可误删（事实丢失）。

边界：≤500 字符的 fulltext 文档整篇已进大纲摘要块，覆盖证据与候选池对该文档
为空（正确行为，非缺陷）。

## 边界与错误处理

- 清洗后长度 ≤ 500 字符的 fulltext 文档，其正文已完整进入大纲摘要块，覆盖证据对该文档为空（方案乙去重的必然结果，属正确行为而非缺陷）。
- 空内容、纯噪声（过短、标题、导航、无数字分隔行、**纯引用/链接列表**）、无任何事实
  特征 → 不产覆盖证据。
- Markdown 表格原子性：`_coverage_split_passages` 把连续以 `|` 起始的行识别为原子
  单元（表格识别复用 key 通道 `_is_markdown_table`，超长表格经 `_split_long_table`
  按行切分并逐片段保留表头；单行 `|` 片段保持独立成段），表头与数据行不会因分隔行
  被噪声过滤丢弃而下标断档分块，列语义随表头进入证据块。
- 每个文档的覆盖证据经 `_fit_coverage_to_budget` 裁入剩余章节预算；放不下的块
  整块跳过、继续尝试后面更小的块（与 `extract_coverage_passages` 内部预算循环
  同语义），仅当第一个块即超出预算时截断该块，保证非空。
- `max_chars <= 0` 时 `extract_coverage_passages` 返回空列表。
- 规则版不引入 LLM/网络调用，无外部依赖。

## 测试与验证

- 定向：`uv run pytest tests/info_collector/algorithm/test_coverage_evidence.py`
- 组装与 prompt：`uv run pytest tests/report/test_sub_report.py tests/report/test_tools_in_report.py`
- 必须覆盖：事实密度优先于位置、噪声过滤、特征计数与封顶、Neighbor Expansion 与相邻合并、
  两级去重、字符预算、摘要基准去重（含锚点救援与基准内包含判定）、候选池两级装配
  （保底份额/缺口配额/回收不净淘汰/同 URL 合并）、`_append_rule_coverage_to_core`
  追加 coverage 块、outline prompt 渲染两路证据语义、缓存键隔离与返回隔离、
  markdown 表格原子性（表头与数据同块、无数字表头不丢、表格与段落边界）。

## 相关文档

- 子报告生成：`docs/feature/algorithm/report-generation/sub-report-generation.md`
- 文档选源（分类/矩阵链路）：`docs/feature/algorithm/report-generation/coverage-matrix-doc-selection.md`