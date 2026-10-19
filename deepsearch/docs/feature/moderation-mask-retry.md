# 模型输入输出风控优化（moderation-mask-retry）

## 维护范围

涵盖模型风控识别、输入掩码、输出约束及其与调用方重试的衔接。
不修改厂商审核策略、SDK 网络重试、业务校验或各环节的失败降级。

## 功能目的

平台返回可识别的内容风控错误后，让调用方已有的下一次尝试使用补救策略，
避免在原有重试之外叠加一套模型请求。

## 可见行为

默认关闭，通过 `LLMConfig.moderation_mask_retry=True` 开启。
每次公共入口调用只执行一次原有调用实现；不增加独立的风控循环。
正常成功请求不加载词库。关闭时输入和输出补救均不执行。

原本总共三次尝试的操作仍最多三次，`run_llm` 默认四次仍最多四次。
首次输入被拦后，下一次尝试掩码 user 文本；首次输出被拦后，下一次尝试追加输出约束。
若剩余尝试仍失败，照常进入原有失败处理；不会因同侧再次被拦提前终止原有循环。
原来异常后不重试的调用仍不重试，不为补救增加次数。

上述计数是应用层调用次数，不是严格的 HTTP 请求数。
SDK／提供商内部重试及原有 thinking 开关兼容处理保持不变，另行计数。

## 关键代码路径

以下相对 `openjiuwen_deepsearch/`：

- `config/config.py`、`llm/llm_wrapper.py`：开关与外部词库目录配置。
- `utils/common_utils/llm_utils.py`：公共入口、异常识别与观测日志。
- `utils/common_utils/moderation_detection.py`：结构化错误、过滤结束原因及异常链的统一识别。
- `utils/common_utils/moderation_retry.py`：在既有重试操作内保存补救状态，不发请求。
- `utils/common_utils/moderation_mask.py`：风控判侧、掩码及输出约束提示。
- `algorithm/` 与 `framework/` 中已有重试操作：只增加状态作用域，保留循环与降级。
- `utils/common_utils/lexicon_resource.py`、`data/sensitive_lexicon/`：词库资源解码与校验。

## 核心流程

1. 在已有重试操作开始时建立局部状态；首次按原消息调用。
2. 非风控异常原样抛出，不启用新策略，由原有重试或失败分支处理。
3. 风控异常同样原样抛出，同时记录下一次已有尝试要应用的策略：
   输入侧掩码 user 文本，输出侧追加中性生成约束。
4. 若调用方仍有尝试次数，将策略应用到调用方本轮提供的消息副本再调用。
   即使业务校验重新生成了消息，也保留已有补救策略和本轮纠错反馈。
5. 输入／输出侧切换时保留已启用的策略。输出约束不重复追加；不默认扩大到 system。
   `mask_messages(level=2)` 仅保留供离线实验使用。
6. 正常响应交回原有解析和校验。操作退出时释放局部状态；次数耗尽后的返回值、
   异常包装和业务降级仍由原有代码决定，不再新增补救耗尽异常。

不同逻辑操作和并发批次分别保存状态。递归拆批复制父批次策略，子批次互不污染；
HTML 章节通过章节 ID 隔离，共用同一外层预算但不共享掩码或输出策略。
拆批本来就可能产生多个子请求，不把整棵拆批树解释为统一的三次请求上限。

## 数据契约与依赖

- `moderation_mask_retry: bool = False`，`moderation_mask_lexicon_dir: str = ""`。
- 消息支持字典和框架消息对象；只修改 user 的字符串 content，返回新列表和消息副本。
  system、assistant、tool 及结构化／多模态 content 不在生产掩码范围。
- 中文及中英混合词保持长词优先字面匹配；英文词条兼容 ASCII 大小写，
  检查英文字母、数字、下划线边界，不自动扩展词形或短语空白。
- URL 先识别并解析域名，按精确域名及真实子域名匹配；不启用带路径、查询或片段
  限定的词条，不将其扩大为整个域名。普通词不扫描地址区间。
- 中文、英文及中英混合词统一等长替换为 `*`，包括命中短语内部的空格。
  命中域名的整个地址替换为 `[URL_REDACTED]`，不保证等长。
- 原始消息、抓取地址和资料存储不改。正常 URL、邮箱、带用户信息、无法解析或
  不支持协议的地址候选保留原样；命中计数为原文替换字符数和合并区间数。
- 外部目录无效或为空时回退打包词库；词库和匹配器惰性缓存。

第三方词库源自 [Sensitive-lexicon](https://github.com/Konsheng/Sensitive-lexicon)，
Copyright (c) 2024~2099 Konsheng，MIT License；分发时保留随附版权与许可声明。
安装包只带 `lexicon.bin`、`manifest.json`、许可证及包初始化文件，不附带原始 `.txt`。
格式为版本头加确定性 gzip 压缩的 UTF-8 排序去重词条，不加密、不使用 pickle。
首次读取校验 SHA-256、格式版本、词条数及 8 MiB 解压上限。

构建者独立获取授权词表，在 SDK 根目录执行：

```bash
python tools/build_moderation_lexicon.py --source-dir /path/to/Sensitive-lexicon/Vocabulary
```

生成器不联网、不复制原始词表，同一组词条生成相同字节。
源码包保留生成工具，wheel 保留运行时资源；外部文本目录仍可加载。

## 边界与错误处理

开关开启时，先读取异常的完整响应 JSON、body 及异常链，文本短语只作兜底。
兼容 `data_inspection_failed` 的 Input data / Input text data / Output data 变体、
`SensitiveContentDetected`、GLM `1301` 错误结构及 MiniMax 的已验证 422 错误结构。
GLM 顶层 `contentFilter.role=user` 提供输入证据；没有实测过的 role 不推导为输出侧。
不将裸数字 `1301`、`1026` 识别为通用异常文本信号。

非流式响应在 JSON / schema 解析前检查 `finish_reason=content_filter`；流式在聚合和
推送片段前检查该字段。流中 error 沿用 SDK 异常链，不新增 SSE 客户端。
200/filter 转为 `CustomValueException(LLM_CALL_FAILED)`，附带固定识别结果，
回到调用方原有失败路径；已有 SDK 异常仍保留原有异常类型及包装。
已经发送到前端的部分正文不回滚，失败结果不能作为完整答案继续解析。

识别不依赖模型名，也不扫描生成正文猜测风控。`content_filter` 本身侧别未知，
响应消息角色为 assistant 不代表输出审核；明确侧别冲突时同样保留 unknown。
unknown 沿用输入策略，但日志区分 `side=unknown` 与 `strategy=input_mask`。
开关关闭时不执行新增检查或补救；没有重试作用域时也不增加调用。
这些规则只覆盖已采集格式，不证明所有厂商都采用同一种错误响应。
JSON / schema 校验异常可能回显生成正文，不用其中的文本做短语兜底，
避免正文提到风控码时被误识别为平台拦截。结构化上游风控证据仍正常识别。

不再使用 `ModerationRetryExhausted` 改变异常类型。异常识别后使用原异常进入既有捕获路径，
例如检索词兜底、sidecar 使用正文、章节失败及报告摘要的 tenacity 耗尽行为均保留。
词库损坏或不可用时告警并返回空词库，不使用部分词条、不伪造成功，也不跳过已有尝试。

输出约束不会修改采样参数或输出上限，也不额外调用改写模型。
正常返回及 schema 合法不代表内容完整、事实准确或没有拒答。
输入掩码可能损失实体区分、语义和嵌入代码；连续星号可能影响 Markdown 排版。
网址占位符没有恢复映射，模型无法引用被替换地址，引用损失需另行验证。
流式失败前可能已有部分输出，本修改不增加流缓冲或 UI 回滚。

日志包含操作 `call_id`、尝试次数和 agent：
`moderation-blocked` 表示已记录下一次尝试的补救策略，不保证调用方还会重试；
`moderation-recovered` 仅表示后续模型调用返回，不表示业务校验或整份报告成功。
同一作用域保留策略，不为每轮重新分配独立的补救预算；取消或异常退出也清理状态。

资源哈希不是防篡改签名，二进制可还原、不保证内容保密或托管平台审核通过。
平台审核需独立确认，不能以资源格式代替合规审核。

## 测试与验证

```bash
pytest tests/utils/test_lexicon_resource.py tests/utils/test_moderation_mask.py tests/utils/test_moderation_retry.py tests/utils/test_moderation_detection.py tests/utils/test_moderation_integration.py
```

重点覆盖原有三次／四次预算、非风控错误、两侧切换、开关关闭、零命中继续原有尝试、
参数及原消息保留、格式纠错后重新掩码、并发与递归隔离、取消清理，
以及规划失败、检索词兜底、tenacity、sidecar、HTML 局部重试和一次调用后降级。
使用内存 HTTP transport 驱动真实 OpenAI SDK 和 OpenJiuwen，验证 200/filter、
部分正文后的流内 error，以及 GLM 顶层判侧字段穿过异常包装后仍可读取。
这些集成测试不访问真实平台，不使用真实 key。
报告、检索、简报、溯源和工作流的原有非在线测试应同步回归。
这些测试不证明真实平台救回率、端到端成本或报告质量；须另做在线实验。
