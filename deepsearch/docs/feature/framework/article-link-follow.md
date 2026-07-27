# 信息收集文章内链接跟进

## 目的与范围

文章内链接跟进是 DeepResearch 信息收集子图中的可选能力。它从本轮搜索结果网页 A 的
`original_content` 中发现链接，经安全过滤和确定性规则选择后抓取网页 B，并在 Supervisor
运行前把 B 作为独立证据加入当前 step。

该能力只支持一跳。由链接跟进生成的 B 带有 `discovery.depth=1`，不会继续产生网页 C。

## 配置

- `AgentConfig.info_collector_article_link_follow_enable`：默认 `False`，控制功能是否启用。
- `ServiceConfig.info_collector_article_link_follow_max_urls`：默认 `3`，限制每个 step 每轮的
  B 抓取总数。
- B 抓取复用 `info_collector_webpage_enrich_fetch_timeout_seconds`。

该开关与 `info_collector_webpage_enrich_enable` 独立。A 增强关闭时，节点分析搜索结果已有的
`original_content`；A 增强开启时，节点分析增强后的正文。

## 核心流程

工作流顺序为：

```text
InfoRetrievalNode
  -> WebPageEnrichmentNode
  -> SupervisorNode
```

文章内链接跟进不是独立工作流节点，而是 `WebPageEnrichmentNode` 中可独立启用的第二个内部
阶段。两个阶段使用独立开关和独立 URL 预算：网页增强开启时，链接阶段分析增强后的 A；网页
增强关闭时，链接阶段直接从搜索结果已有的 `original_content` 中发现 B，不依赖增强阶段执行。

节点只扫描 `new_doc_infos_current_loop` 的输入快照，并排除深度大于等于 1 的文档。算法层
支持 Markdown、HTML、纯文本 URL 和相对 URL，复用 collector canonical URL 规则进行
去重，过滤非 HTTP(S) 和明显的非网页资源。

候选在规则选择前使用严格公网 URL 校验。校验阻止嵌入凭据、localhost、环回、私网、
链路本地、元数据地址和解析到非公网 IP 的域名；抓取返回的最终 URL 会再次校验。

规则选择采用三层漏斗：先硬过滤登录、注册、分享、首页、分类页等无效链接；再保留锚文本
与任务相关、链接上下文与任务相关，或者锚文本/URL 命中报告、论文、数据集、法规等证据
关键词的候选；最后按锚文本相关、上下文相关、证据关键词、首次出现位置和 canonical URL
稳定排序并截取 Top N。三个条件均未命中的链接不会为了填满预算而被抓取。

B 复用 `WebPageEnrichmentNode` 的统一抓取流程，包括 direct fetch、PDF 识别、Jina Reader
fallback、整体 deadline 和抓取日志。候选 URL 与抓取返回的最终 URL 均执行公网校验。压缩
LLM 提取与 step 相关的 bounded evidence，随后使用现有文档评价流程补充分数。成功的 B 写入：

- `collector_context.doc_infos`；
- `collector_context.new_doc_infos_current_loop`；
- `collector_context.source_store`；
- 父搜索 query 的 `history_queries[*].doc_infos`。

B 保留自己的 URL、标题、`doc_id`、`source_id` 和 `content_ref`，并通过 `discovery` 记录父
页面、锚文本、选择理由及一跳深度。B 不覆盖或合并网页 A。

## 状态与失败行为

`EvidenceLedger` 通过 `attempted_links`、`successful_links` 和 `failed_links` 保存 canonical
URL，防止后续研究轮重复抓取。

选择为空、单链接超时、抓取失败、最终 URL 不安全、正文无效、压缩失败或评价失败均降级为
不新增对应 B。并发任务使用异常隔离，一个链接失败不会移除 A、阻断其他 B 或终止 collector。

## 代码路径

- `openjiuwen_deepsearch/algorithm/research_collector/article_link_follow.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/webpage_enrichment.py`
- `openjiuwen_deepsearch/algorithm/prompts/collector_article_link_follow_compress.md`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/collector_graph/graph_builder.py`

## 测试

```powershell
python -m pytest tests/info_collector/algorithm/test_article_link_follow.py
python -m pytest tests/info_collector/test_webpage_enrichment.py
python -m pytest tests/info_collector/test_webpage_enrichment_article_link_follow.py
python -m pytest tests/info_collector/test_evidence_ledger.py
python -m pytest tests/info_collector/test_graph_builder.py
python -m pytest tests/utils/test_url_utils.py
```
