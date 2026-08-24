# 搜索内容噪声清洗

## 维护范围

本文档覆盖 web 搜索内容链路的「噪声清洗」特性：在搜索结果归一化时，用纯规则删除混入 `content` 字段的页面样板与抽取残留（导航菜单、相关推荐、页脚备案、cookie/订阅横幅、HTML/markdown 残留标记等）。professional 与 brief 报告路径共用 collector，均受益。

不覆盖：正文抽取（输入已是搜索引擎/抓取服务抽取过的文本，只做残留噪声减法）、学术 `full_text` 路径、本地搜索路径、抓取架构本身。

## 功能目的

下游证据构造与摘段落对正文做硬截断（collector 10000 字符、摘段落 15000 字符），截断取前 N 字符：噪声混在前面就把正文挤出窗口。清洗在归一化时（第一个有效截断点之前）删除噪声，提高单位预算内的有效正文字符数。零新增 LLM 调用、零新增第三方依赖（仅标准库 `re` + `html.unescape`）。

## 可见行为

- 默认开启。对 `content` 达到门控长度（1500 字符）的 web 搜索结果，归一化时自动清洗；短摘要原样透传。
- 只清 `content` 字段（含 `raw_content` 兜底路径）；`full_text`、`title`、`url`、`date_metadata`、`score` 一律不动。
- 图片语法 `![alt](url)` 原样保留，不删不归档；裸 URL 行不动。
- 任何护栏触发即整体回退原文，最坏情况等于现状（宁漏勿杀）。
- 短于门控长度时原样透传，行为与现状完全一致。

## 关键代码路径

- 核心实现：`openjiuwen_deepsearch/algorithm/research_collector/content_cleaner.py`（`clean_web_content`、`ContentCleaningConfig`、`CleaningStats`）
- 归一化挂点：`openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`（`_normalize_web_search_item`，tavily/google/common 三路径共用）
- 配置注入：`openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/info_collector.py`（构造默认 `ContentCleaningConfig`，经 `agent_input["content_cleaning_config"]` 传入 collector）

主要测试：

- `tests/info_collector/algorithm/test_content_cleaner.py`

## 核心流程

1. **挂点**：`_normalize_web_search_item` 内 `content` 字段落定之后、写入 `normalized["content"]` 之前调用 `clean_web_content`。tavily/google/common 三路径共用，所有部署形态必经。
2. **L0 残留规范化**：HTML 实体反转义；孤立 HTML 标签残留删除（仅常见标签名且前后为空白/行边界，防误伤比较式/泛型写法）；连续 ≥3 空行压缩为单个空行。
3. **L1 行级样板删除**：按空行/标题行切块，计算语言中立形态特征（行数/平均行长/链接行占比/块位置），中英双语 chrome 特征词表仅作辅助条件。任一命中即删块：
   - R1 链接列表块：≥3 行 且 链接行占比 ≥60% 且 平均行长 ≤40 字符；
   - R2 chrome 块：命中特征词 ≥1 且（链接行占比 ≥40% 或 平均行长 ≤25 字符），双条件防误伤长句正文；
   - R3 尾部样板块：块起始位于文档末尾 20% 区域 且 命中尾部特征词（备案/版权/举报/关注我们/隐私政策类）≥2 个。
   - 块级门槛：块文本 <4 字符或 >5000 字符（多为正文容器）不参与评估。
4. **L0 链接还原**：L1 判定完成后，幸存内容的 markdown 链接 `[text](url)` 还原为 `text`（链接行占比特征依赖链接语法，故还原放在 L1 之后）。
5. **L2 护栏**：任一触发 → 整体回退原文。
   - G1 长度护栏：结果 < `min_keep_chars`（500）或 < `min_keep_ratio`（40%）× 原文；
   - G2 删除占比保险丝：删除字符占比 > `max_remove_ratio`（60%）；
   - G3 事实锚点护栏：原文 ≥4 位数字与百分数集合在清洗后保留率 < `anchor_keep_ratio`（85%）。锚点提取前先做 URL 掩蔽（链接还原为锚文本、剔除裸 URL 行），URL 路径内的文章 ID/日期数字不计入锚点。

## 数据契约与依赖

- `clean_web_content(content, config) -> tuple[str, CleaningStats]`：纯函数，返回清洗后文本与统计（`raw_chars/cleaned_chars/removed_ratio/applied_rules/fallback_reason`，`fallback_reason` ∈ {`min_keep`, `max_remove_ratio`, `anchor_keep`}，未回退为 `None`）。统计仅供模块自检与单测断言，不写入归一化文档、不参与去重/选材/写作。
- `ContentCleaningConfig`：framework 节点构造、collector 纯函数消费，algorithm 层不直接读全局 state。字段（默认值即生产值，无外部配置项，调参直接改 dataclass 默认值）：`enabled=True` / `min_chars=1500` / `max_remove_ratio=0.6` / `min_keep_chars=500` / `min_keep_ratio=0.4` / `anchor_keep_ratio=0.85`。
- 注入路径：professional 路径由 `InfoRetrievalNode._pre_handle` 构造默认配置传入 `agent_input["content_cleaning_config"]`；brief 路径及直接调用 collector 的调用方未注入时，同样按默认值构造，两者取值一致。

## 边界与错误处理

- 门控：开关关闭、原文 < `min_chars`、或内容为空白时直接返回原文。
- 清洗对同一输入确定性输出，单次运行内 `source_id`/去重键（含 content 全文）全链路一致。极端情形：两条仅样板不同的同 URL 记录清洗后变成同文 → 去重合并为一条，属期望内的去冗余。
- 同 URL 的 `source_id`（以 content 为哈希输入）清洗前后不同；单运行内自洽，跨运行不可比，无正确性影响。
- 链接行判定口径：行内 markdown 链接构造（`[text](url)` 整体，含 URL）占行长 ≥50% 判定为链接行；R1 的平均行长按原始行（含 URL）计算，故主要命中短/相对 URL 的紧凑链接块，长绝对 URL 的链接块依赖 R2/R3 兜住（宁漏勿杀方向）。
- 规则异常不阻断流程：`process_*_search_result` 外层已有 try/except 兜底，清洗失败回退为原始记录。
- 排障入口：某篇文档内容异常变少 → 单测构造同内容复现，查 `clean_web_content` 返回的 `fallback_reason` 与 `applied_rules`；疑似整体回归 → 回滚代码（无运维开关，护栏保证单文档最坏情况=原文）。

## 测试与验证

```bash
uv run pytest tests/info_collector/algorithm/test_content_cleaner.py -v
uv run pytest tests/info_collector tests/report -q
```

必须覆盖：L0 各规则（实体反转义/图片保留/链接还原/裸 URL 保留/空行压缩）、L1 的 R1/R2/R3 正反向用例、G1/G2/G3 护栏回退（含 `fallback_reason`）、门控（开关/短文本/`full_text` 不清）、中英文样本各 ≥3、确定性。

## 相关文档

- [资料采集](./algorithm/research-collector.md)
- [信息采集子图](./framework/info-collector-subgraph.md)
- [Agent 与服务运行配置](./config/agent-and-service-config.md)
- [时间约束软过滤](./temporal-soft-filter.md)（正文质量连带影响内容时间判出率）
- 设计文档：`docs/superpowers/specs/2026-08-23-html-content-cleaning-design.md`
