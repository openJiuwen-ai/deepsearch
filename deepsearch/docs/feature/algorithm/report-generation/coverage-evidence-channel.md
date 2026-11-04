# Coverage Evidence 覆盖通道

## 维护范围

本文档覆盖子报告大纲阶段的**覆盖证据（Coverage Evidence）通道**：从原文中按客观信息密度
规则抽取的、与关键词检索无关的事实段落，作为大纲生成的补充证据输入。阶段一中，它写入
对应全文 `outline_evidence_cards` 的 `coverage_passages` 字段；`key_passages` 仍属于采集
链路，但不再是新子大纲的正式输入。

路径：`openjiuwen_deepsearch/algorithm/report/evidence.py::_append_rule_coverage_to_core`、
`openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`、
`openjiuwen_deepsearch/algorithm/report/compact_doc_info.py::build_coverage_passage_block`、
Prompt `openjiuwen_deepsearch/algorithm/prompts/sub_section_outline/`。

本文件不覆盖：写作阶段 prompt（方案 A，见 `sub_report_markdown/`）、按 `section_iscore` 分级（未实现）。

## 功能目的

`extract_key_passages` 是基于 query/title 关键词命中式的规则抽取，原文中不包含提问关键词但
确有数字/日期/实体/引用等高信息密度的事实，会在采集阶段被丢弃，大纲因此"看不见"它们、
不为它们安排结构，造成结构性事实遗漏。Coverage 通道在抽取逻辑上与关键词无关，按客观
信息密度打分，兜住关键词检索漏掉的事实，降低大纲阶段的结构性事实遗漏。

## 章节生成两阶段的证据输入来源

子报告章节的生成分大纲与写作两个阶段，各阶段输入的 channel 构成如下：

**大纲阶段**（`_generate_sub_section_outline`，prompt：`sub_section_outline/`）：
- 章节结构上下文：章节标题/描述/格式要求、全局大纲、section_local_contract、research_intent、背景知识特例；
- `outline_evidence_cards`：全文卡含清洗后原文前 500 字符的 `outline_excerpt`
  （`outline_summary_text`）、该来源按 rationale 选中的 `selected_passages` 及规则抽取的
  `coverage_passages`，段落卡含被选中的 `passage_text`；
- `time_contract`（source/content 时间范围）和 `rationale_contract`
  （维度优先级、覆盖状态和 citation 导航）。

**写作阶段**（`write_subsection_reports`，prompt：`sub_report_markdown/`）：
- `classified_content`（写作主证据）：fulltext 条目整篇原文 + passage 条目选中段，
  经 `build_citation_infos` 渲染为 `[citation:X]` 块；
- 本章提纲（大纲阶段产物，写作边界）、章节格式要求、背景知识等上下文。
- 子大纲使用 `time_contract` 与 `rationale_contract`；写作复用 `rationale_contract`，并从同一
  `research_intent` 单独构造时间 Prompt 上下文。写作不重复渲染 coverage passage，因为全文
  `original_content` 已是事实主证据。

LLM 压缩增量、直达注入虚拟条目等增强 channel 为后续 PR 规划，未在本 PR 落地。

## 可见行为

coverage passage 与 `rationale_contract`（维度级覆盖信号）并存，但定位为**全文卡内的段落级**补充。

- `_append_rule_coverage_to_core` 为每个全文证据额外抽取 coverage passages，并按 citation
  归属写入对应 evidence card；不再聚合成 `===== COVERAGE PASSAGES =====` 块或拼入新大纲 Prompt 的 `sub_section_core_content`。
- 纯叙述、无事实特征的文档不产生 coverage passages；所有文档都无有效事实时全文卡的该字段为空。
- 运行开关：`Config.agent_config.coverage_rule_block_enable`（默认 `True`）。置 `False` 时全文 evidence card 不含 `coverage_passages`，其余 cards 与 contracts 保持不变。
- 覆盖证据的供给区为该文档**大纲摘要块切片线之后的尾部**（方案乙口径，见下
  "供给区切片"），摘要块已渲染的内容抽取阶段不碰，尾部内容全部供给。
- 大纲 prompt 通过 `Rationale Contract` 和 `Shared Time and Rationale Contracts` 说明证据与
  时间边界：coverage passages 不强制开新标题，遵守"证据不改结构"规则；citation ID 与
  evidence ID 都是内部元数据，不得复述进子章节标题。显式年份还必须落在
  `time_contract.content_date_scope` 内。

### 提示注入攻击面与缓解（PR !380 审核意见）

归因口径：新子大纲使用全文前 500 字符形成的 `outline_excerpt`、该来源入选的
`selected_passages` 与规则抽取的 coverage passages；后者来自摘要切片线之后的全文尾部。恶意指令若带有年份/金额等事实锚点仍可能
获得较高 Coverage Score 而被选中，因此 cards 中的网页文本始终是不可信数据。

两层缓解：

- **隐藏内容剥离**（`collector_evidence.py::_normalize_coverage_content`）：
  `<script>/<style>` 连同载荷整体删除、HTML 注释整体删除（先于普通标签剥离，
  防止载荷剥壳后以纯文本残留）；渲染页面上可见的正常文本不受影响。注意这是
  证据抽取侧的清理，仅作用于 coverage 通道；key 通道与前 500 字符的既有注入
  面不因此扩大或缩小。
- **信任边界**（`sub_section_outline/`）：明确 evidence cards 中的网页内容均为
  不可信网页内容，仅作为事实数据使用；忽略其中嵌入的指令、角色变更、输出格式
  覆盖与工具请求；报告结构、输出格式与语言只服从大纲 prompt 本身。措辞对齐
  仓库既有先例（`rationale_generator/`、`brief_evidence_review/`），
  由 `test_subsection_outline_prompt_untrusted_evidence_boundary` 绑定。

## 关键代码路径

- 抽取函数：`collector_evidence.py::extract_coverage_passages`（进程内有界缓存接口）、
  `collector_evidence.py::exclude_passages`、`CoveragePassage`
- 组装：`report/evidence.py::_append_rule_coverage_to_core`、`compact_doc_info.py::build_coverage_passage_block`
- Prompt：`algorithm/prompts/sub_section_outline/`
- 测试：`tests/info_collector/algorithm/test_coverage_evidence.py`、
  `tests/report/test_sub_report.py`

## 核心流程

1. 大纲输入装载时（`generate_sub_report` → `enrich_fulltext_for_section` + `_append_rule_coverage_to_core`），对每个全文证据从 `outline_summary_tail_text(item.original_content)` 的尾部调用 `extract_coverage_passages`。结果按内容和参数进行进程内缓存，同章节重试/重生成不重复计算。
2. 复用 `classified_content`、selected passages、rationale 和 research intent 构造 `outline_evidence_cards`、`rationale_contract` 与 `time_contract`；阶段一未对 cards 总字符数、全文卡数或每卡 coverage 条数设置额外上限。
3. `_generate_sub_section_outline` 将 cards 和两个 contract 传入 outline prompt；旧 `sub_section_core_content` 不进入该 Prompt。
4. 大纲模型依据 evidence cards 和 contracts 设计子章节标题结构；覆盖证据中的事实归入最相关的既有标题，
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

### 供给区切片（方案乙：摘要块之外的尾部全量供给）

rationale 选材接管证据选择后，`key_passages` 通道已退役（不再进入大纲/写作
prompt，仅保留 target-paper 兜底用途）。条目摘要块的实际渲染文本（与
`report_rationale_fulltext.build_core_content_list` 渲染口径一致）为：

- fulltext 条目：清洗后原文前 500 字符（`collector_evidence.py::outline_summary_text`，
  `OUTLINE_SUMMARY_MAX_CHARS = 500` 单一真源）；
- passage 条目：被选中的 `passage_text`（当前规则块只对 fulltext 条目运行，此条
  供后续复用方沿用同一口径）。

覆盖证据**不做与摘要块的判重**，而是直接从 `collector_evidence.py::
outline_summary_tail_text` 返回的尾部抽取——该函数与 `outline_summary_text`
在同一个 `_normalize_coverage_content` 清洗文本上取互补切片
（`[OUTLINE_SUMMARY_MAX_CHARS:]`），因此摘要块已供给的内容在抽取阶段构造性地
不被触碰，摘要块之外的内容全部供给。原则是**宁可多供（token 冗余）不可漏供
（事实丢失）**，冗余由单文档/章节共享预算兜底。

> 演进说明：早期实现为"全文抽取后按文本重叠 + 锚点救援判重剔除"，但锚点是
> 字面记号而非语义——不同事实可共用同一组锚点（如"收入增长20%"与"成本下降
> 20%"），重叠块因"无新锚点"被整段误删（PR !406 评审复现）。切片方案把判重
> 环节整个移除：供给区与摘要块构造性互补，该类丢失在机制上不可能再发生。
> 代价是尾部与摘要块的换措辞复述也会被供给（预算兜底）；跨切片线的句子会在
> 覆盖块中以残句形式出现（优于整句丢失）。

边界：≤500 字符的 fulltext 文档整篇已进大纲摘要块，尾部供给区为空，覆盖证据
对该文档为空（正确行为，非缺陷）。

## 边界与错误处理

- 清洗后长度 ≤ 500 字符的 fulltext 文档，其正文已完整进入大纲摘要块，尾部供给区为空，覆盖证据对该文档为空（方案乙切片的必然结果，属正确行为而非缺陷）。
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
