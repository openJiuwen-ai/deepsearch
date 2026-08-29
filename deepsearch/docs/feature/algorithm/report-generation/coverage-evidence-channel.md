# Coverage Evidence 覆盖通道

## 维护范围

本文档覆盖子报告大纲阶段的**覆盖证据（Coverage Evidence）通道**：从原文中按客观信息密度
规则抽取的、与关键词检索无关的事实段落，作为大纲生成的补充证据输入。与已有的
`key_passages`（相关性检索）并行，属于"方案 B"的工程实现。

路径：`openjiuwen_deepsearch/algorithm/report/report.py::_append_rule_coverage_to_core`、
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

与 `structured_evidence_guide`（维度级覆盖信号）并存，但定位为**段落级**补充。

- `_append_rule_coverage_to_core` 为每个全文证据额外抽取覆盖证据，去重后聚合成一个
  `===== COVERAGE PASSAGES =====` 块，追加到 `core_content_list` 末尾（key 块在前、
  coverage 块在后、不交错）。
- 纯叙述、无事实特征的文档不产生覆盖证据；所有文档都无有效事实时不追加该块。
- 覆盖证据与同文档 `key_passages` 重复（相同/高相似/高占比子串）时被剔除，避免 token 冗余。
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
- 组装：`report.py::_append_rule_coverage_to_core`、`compact_doc_info.py::build_coverage_passage_block`
- Prompt：`algorithm/prompts/sub_section_outline.md`
- 测试：`tests/info_collector/algorithm/test_coverage_evidence.py`、
  `tests/report/test_sub_report.py`

## 核心流程

1. 大纲输入装载时（`generate_sub_report` → `enrich_fulltext_for_section` + `_append_rule_coverage_to_core`），对每个全文证据：
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
  版本/年份/单位/量级不同的事实不会被误删；`exclude_passages` 与 key 去重沿用
  "归一化相同 / 字符二元组 Jaccard ≥0.6 / 一方是另一方 ≥60% 占比子串"。
- 邻域扩展：`expansion_density_threshold`（方案3 密度门控，默认 0 关闭）。预算兜底
  （K≈候选全集）下邻居扩展为空操作，故默认关闭；参数保留供有限 K 场景评估。
- 集成常量（`collector_evidence.py` 唯一真源，`report.py` 与评估工具共用）：
  `_COVERAGE_TOP_K_CAP = 128`（选段数量上界，实际由预算兜底）、
  `_COVERAGE_MAX_CHARS_PER_DOC = 1200`（单文档预算，`extract_coverage_passages`
  的 `max_chars` 默认值即引用此常量）、`_COVERAGE_MAX_TOTAL_CHARS = 6000`
  （章节级总预算，由 report.py `_fit_coverage_to_budget` 二次裁剪）、
  `_COVERAGE_SCORE_MODE`、`_COVERAGE_DENSITY_MIN_LEN`。
- coverage 块格式：`===== COVERAGE PASSAGES =====` / `Document N coverage passages:`
  + `- passage`，`N` 与 key 块编号对齐。
- 运行开关：环境变量 `DS_COVERAGE_RULE_BLOCK`（默认开）可单独关闭覆盖通道做回滚。
  按标准布尔口径解析：`1`/`true`/`yes`/`on`（大小写与首尾空白不敏感）开启，
  其余值（`0`/`false`/`off`/空串）关闭。
- 性能：正则抽取流水线为纯 CPU（生产口径 10 篇 × 10000 字符、高事实密度最坏
  用例冷调用约 150 ms；锚点去重键集合按块缓存复用，lru_cache 命中时 <10 ms）。
  `generate_sub_report` 经 `asyncio.to_thread` 调用组装函数，不阻塞事件循环；
  章节共享预算耗尽后跳过剩余文档的抽取。
- 不修改 `doc_info` / `classified_doc_infos`，写作阶段证据输入不受影响。

## 边界与错误处理

- 空内容、纯噪声（过短、标题、导航、无数字分隔行、**纯引用/链接列表**）、无任何事实
  特征 → 不产覆盖证据。
- 每个文档的覆盖证据经 `_fit_coverage_to_budget` 裁入剩余章节预算；放不下的块
  整块跳过、继续尝试后面更小的块（与 `extract_coverage_passages` 内部预算循环
  同语义），仅当第一个块即超出预算时截断该块，保证非空。
- `max_chars <= 0` 时 `extract_coverage_passages` 返回空列表。
- 规则版不引入 LLM/网络调用，无外部依赖。

## 测试与验证

- 定向：`uv run pytest tests/info_collector/algorithm/test_coverage_evidence.py`
- 组装与 prompt：`uv run pytest tests/report/test_sub_report.py tests/report/test_tools_in_report.py`
- 必须覆盖：事实密度优先于位置、噪声过滤、特征计数与封顶、Neighbor Expansion 与相邻合并、
  两级去重、字符预算、`exclude_passages` 去重、`_append_rule_coverage_to_core` 追加 coverage 块、
  outline prompt 渲染两路证据语义、缓存键隔离与返回隔离。

## 相关文档

- 子报告生成：`docs/feature/algorithm/report-generation/sub-report-generation.md`
- 文档选源（分类/矩阵链路）：`docs/feature/algorithm/report-generation/coverage-matrix-doc-selection.md`