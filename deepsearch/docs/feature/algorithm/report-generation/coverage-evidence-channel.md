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
- passage 条目（候选池侧）：被选中的 `passage_text`。

判定共享谓词 `collector_evidence.py::_supplied_by_basis`（规则块侧
`exclude_passages` 与候选池侧 `coverage_compressor.py::_paragraph_overlaps_basis`
同一口径，防漂移）：

- 文本层重叠 = 完全相同 / 段落整体落在基准内部（基准远大于段落的包含情形）/
  高相似或高占比子串；
- **锚点救援**：文本层重叠、但段落携带基准之外锚点的段落保留——"前 500 字符
  叙述 + 新事实句"的扩展块是覆盖通道要供给的形态，不因与前缀基准相似被整段
  判重；锚点是"有无新事实"的廉价判据。宁可多供（token 冗余）不可误删（事实丢失）。

边界：≤500 字符的 fulltext 文档整篇已进大纲摘要块，覆盖证据与候选池对该文档
为空（正确行为，非缺陷）。

## 候选池两级缺口驱动装配（LLM 压缩层）

`coverage_compressor.py::build_candidate_pool` 在准入过滤（噪声 + 摘要基准剔除）
之后，按两级预算装配候选池：

- **保底层**：`floor_ratio`（默认 0.4，环境变量 `DS_COVERAGE_POOL_FLOOR_RATIO`
  覆盖，0~1 钳制）份额的预算走原始跨文档轮询（无信息先验）。缺口检测是启发式，
  系统性漏判某类文档时，纯优先级排队会让它长期排不上队；保底份额是对"优先级
  排队≈软淘汰"灰色地带的低成本对冲。
- **缺口层**：剩余预算按文档级"锚点缺口率"反向分配字符配额——该文档候选段锚点
  中被（摘要基准 ∪ 规则块覆盖段）覆盖的比例越低，配额越大；无锚点的纯文本文档
  按"完全未覆盖"计（配额最大，正是 4.c 富集文档）。配额内部含缺口锚点的段落
  优先。无任何缺口信号时退化为均匀轮询。
- **回收**：两层都放不下、池预算仍有余量时按 (文档顺序, 原文顺序) 补齐——任何
  段落不因分组/配额而被净淘汰。
- **同 URL 合并**：同一 URL 的多个 passage 条目合并为一个文档组，父文档只进池
  一次（rationale 常从同一篇选中多个段落，逐条目进池会重复送父文档全文），组内
  去重基准取该 URL 全部选中段落并集。
- 池输入缺 `passage_text` 键的旧式 dict 按 fulltext 口径处理（兼容）。
- `compress_for_coverage` 自动把规则块段落（`rule_passage_texts`）传入池装配作为
  缺口层锚点基准之一。

## 增量并集差集基准扩容（5.5）

`coverage_compressor.py::filter_incremental_facts` 的差集基准从"规则块段落"
扩为"规则块段落 **+ 写作端已渲染文本**"（`_main_evidence_texts` 构造，渲染规则
与 `report_common.build_citation_infos` 一致：fulltext 条目整篇 / passage 条目
`passage_text`，同 URL 合并到组长 index，与候选池分组同一实现
`_group_classified_entries`）。

- **判定口径**（设计定稿）：文本层（归一化子串或近似重复）与锚点层（锚点键
  ⊆ 该文档基准锚点并集）**同时满足**才判已覆盖；纯文本事实文本层命中即判。
- **测量一体**（评审焦点 4 决策）：C 臂与 C' 臂都带扩容口径跑，C−B 与 C'−C
  同基线可比。
- **归因统计**：`extras_dropped_vs_main`（与主证据判重条数）、
  `dedup_vs_main_evidence`（vs_main ÷ extras_total，校验通过事实里与写作端
  已见内容重复的占比）落盘 `compress_stats.jsonl`，五臂实验据此校验压缩层
  净增益口径的重复水分。
- 规则块关闭（`DS_COVERAGE_RULE_BLOCK=0`）时主证据单基准仍生效——rationale
  选中事实的重复产出依然被拦。
- 已知代价（定稿口径接受）：与主证据"换措辞不逐字"的转述不判重（文本层不
  命中），`dedup_vs_main_evidence` 读数偏小；换来"数字相同但语义维度不同的
  事实不被误删"。

## 截断排序信号（5.6）

`compress_for_coverage` 在 extras 字符预算截断前按"锚点密度 × 稀缺来源加成"
降序排序（稳定排序，同分保持压缩输出顺序）：

- 锚点密度 = `extract_fact_anchors(fact.text)` 记号数；
- 稀缺来源加成 = 来源文档在池中仅一个段落支撑时 +1（跨文档独有信息）；
- **排序不是过滤**：弱相关事实排后、预算耗尽时自然淘汰；明确不做相关性预筛
  （rationale 的相关性信号绝不进淘汰——兜底机制的存在意义就是挑战其判断）；
- `extras_truncated`（被截条数）落盘 compress_stats.jsonl；
- 排序+截断一次完成，extras 块（大纲间接通道）与直达注入消费**同一份列表**，
  两通道体量一致。

## 直达注入与挽回收率（第 7 章主线，P4）

`evidence.py::_maybe_inject_virtual_entries`（开关 `DS_COVERAGE_EXTRAS_INJECT`，
默认关，独立回滚）：把 extras 中"写作端不可见"的事实以虚拟条目直接送进写作
提示词——全系统唯一承诺"挽回"的机制。

- **注入范围**：逐条判定"该事实所在文本是否已在写作提示词里"——来源 fulltext
  文档的事实整篇已渲染（`writer_visible=True`，留大纲层）；passage 来源默认
  不可见（注入对象）。
- **虚拟条目**：content = 池段落**原文**（证据=原文、浓缩=发现机制；转述语义
  保真检查仍在监控期不直接进写作端），同一池段落多事实合并一个条目；
  `scores = {"tier": "supplementary"}`（渲染零改动，配合写作 prompt 补充证据
  措辞：只许为已展开论点补细节，不得衍生新论点/新章节）；编号在
  classified_content 末尾新编（N+1…），url/标题/时间复用来源文档；
  classified_content 与 `sub_section_references` 镜像同步追加。
- **注入点**：`_maybe_compress_coverage` 成功分支内、extras 追加大纲之后；
  任何异常 → 不追加，退回"extras 只进大纲"的间接形态（降级矩阵），主链路
  无感。
- **挽回收率**（第一指标）= 成稿锚点命中数 ÷ 注入数。注入遥测双写：
  `compress_stats.jsonl` typed 行（`type=injection`，含每章注入清单：事实文本/
  池段落/引文编号，`label=section_N`）+ `doc_selection_debug.coverage_injection`
  （ResultExporter 链路）。离线脚本读任一通道与成稿做锚点交集即可算回收率。

## 大纲处置问责（8.2 仪表）

`sub_section_outline.py::_record_coverage_disposal`（观测增强，非流程依赖）：
注入开启时，大纲 prompt 附带编号覆盖事实索引（`coverage_fact_index`，仅含
writer_visible=False 的事实——fulltext 来源写作端已可见，属边界性丢失不救），
请求大纲 LLM 对每条事实输出处置标注：

- `assigned | <子标题编号>`：事实安置到某子标题；
- `discarded | <理由>`：显式丢弃并论证（等于认为上游判断有误，需要给理由）；
- `inherited_discard`：复用 rationale 的低分判断，免论证。

- **硬性顺序约束①（机制化）**：标注请求由 `DS_COVERAGE_EXTRAS_INJECT` 门控
  ——仅注入开启时才请求标注，保证 assigned 事实写作端必有对应证据，杜绝
  "有标题没材料"的编造风险。
- **解析失败忽略**：无标注/格式非法时忽略标注、大纲照常（损耗率缺数，报告
  正常）；防御性剥离（`strip_disposal_annotations`）保证标注行不混进提纲。
- **遥测**：`type=disposal` 行落盘 compress_stats.jsonl（总数/各态计数/传导
  损耗率/discarded 理由/已显式拒绝清单）；拒绝清单同时留存
  `current_inputs.coverage_disposal` 供 8.3 补丁消费。

## 草稿锚点补丁（8.3 短路收口）

`evidence.py::compute_draft_anchor_gap` + `Reporter._maybe_patch_draft`
（开关 `DS_COVERAGE_DRAFT_PATCH`，默认关，独立回滚）：

- **差集计算（零 LLM）**：注入事实全集 − 大纲已显式丢弃（8.2 拒绝清单）−
  成稿已写（事实锚点出现在归一化草稿文本）。已弃事实不再进入补丁判断
  ——rationale 判过、仪表复用过、补丁不再重判。
- **已知盲区（结构性）**：纯文本事实（无锚点）差集恒不含它，单独计数；纯
  文本的传导靠第 7 章直达注入兜底。
- **窄补丁调用**：差集非空才触发一次调用（只含差集事实）；prompt 显式允许
  skip（硬塞不合上下文的事实比漏写更伤报告）；输出未新增任何锚点视为全
  skip，保留原草稿。
- **失败降级**：调用失败/空输出/全 skip 一律保留原草稿——损失是无增益，
  不是负增益。
- **遥测**：`type=draft_patch` 行落盘（差集事实、skip 清单）。
- 邻域扩展：`expansion_density_threshold`（方案3 密度门控，默认 0 关闭）。预算兜底
  （K≈候选全集）下邻居扩展为空操作，故默认关闭；参数保留供有限 K 场景评估。
- 集成常量（`collector_evidence.py` 唯一真源，`report/evidence.py` 集成层引用）：
  `_COVERAGE_TOP_K_CAP = 128`（选段数量上界，实际由预算兜底）、
  `_COVERAGE_MAX_CHARS_PER_DOC = 1200`（单文档预算，`extract_coverage_passages`
  的 `max_chars` 默认值即引用此常量）、`_COVERAGE_MAX_TOTAL_CHARS = 6000`
  （章节级总预算，由 `report/evidence.py` `_fit_coverage_to_budget` 二次裁剪）、
  `_COVERAGE_SCORE_MODE`、`_COVERAGE_DENSITY_MIN_LEN`。
- coverage 块格式：`===== COVERAGE PASSAGES =====` / `Document N coverage passages:`
  + `- passage`，`N` 与 key 块编号对齐。
- 运行开关：环境变量 `DS_COVERAGE_RULE_BLOCK`（默认开）可单独关闭覆盖通道做回滚。
  按标准布尔口径解析：`1`/`true`/`yes`/`on`（大小写与首尾空白不敏感）开启，
  其余值（`0`/`false`/`off`/空串）关闭。
- 直达注入开关：环境变量 `DS_COVERAGE_EXTRAS_INJECT`（默认关）。同标准布尔
  口径；独立回滚，五臂 C' 臂使用。
- 候选池保底比例：环境变量 `DS_COVERAGE_POOL_FLOOR_RATIO`（默认 0.4，0~1 钳制，
  非法回退默认）。设 1 退化为纯均匀轮询（关闭缺口驱动），设 0 为纯缺口驱动。
- 性能：正则抽取流水线为纯 CPU（生产口径 10 篇 × 10000 字符、高事实密度最坏
  用例冷调用约 150 ms；锚点去重键集合按块缓存复用，lru_cache 命中时 <10 ms）。
  `generate_sub_report` 经 `asyncio.to_thread` 调用组装函数，不阻塞事件循环；
  章节共享预算耗尽后跳过剩余文档的抽取。
- 不修改 `doc_info` / `classified_doc_infos`，写作阶段证据输入不受影响。

## 边界与错误处理

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
- 候选池/压缩：`uv run pytest tests/report/test_coverage_compressor.py`
- 必须覆盖：事实密度优先于位置、噪声过滤、特征计数与封顶、Neighbor Expansion 与相邻合并、
  两级去重、字符预算、摘要基准去重（含锚点救援与基准内包含判定）、候选池两级装配
  （保底份额/缺口配额/回收不净淘汰/同 URL 合并）、`_append_rule_coverage_to_core`
  追加 coverage 块、outline prompt 渲染两路证据语义、缓存键隔离与返回隔离、
  markdown 表格原子性（表头与数据同块、无数字表头不丢、表格与段落边界）。

## 相关文档

- 子报告生成：`docs/feature/algorithm/report-generation/sub-report-generation.md`
- 文档选源（分类/矩阵链路）：`docs/feature/algorithm/report-generation/coverage-matrix-doc-selection.md`