# AGC AI Networking 联网搜索引擎

## 维护范围

本文档覆盖 DeepSearch 接入华为 AGC「AI问答联网增强服务」的 `agc_ainetworking`
联网搜索引擎：枚举注册、包装器字段、参数映射、会话级站点约束行为、响应归一化、
错误码、明确不支持项以及上线前需人工验证的风险项。

不覆盖通用联网搜索引擎管理 CRUD、SSRF 校验、密钥加解密等
所有引擎共享行为，详见
[模板与联网搜索引擎管理](./server/template-and-web-search-engine-management.md)。
不覆盖 `webBoxSearch` 与 `webSearch/multiLang` 端点（见「明确不支持项」）。

权威语言：中文。本文档与代码行为不一致时以代码为准，并请同步更新本文档。

## 1. 服务简介

`agc_ainetworking` 接入的是华为 AppGallery Connect 提供的
「AI问答联网增强服务」`webSearch` 接口。该服务基于用户输入 `query`，实时
返回满足查询意图的网页结果，包括 URL、标题、正文片段、站点名、发布时间等
字段，并保证结果准确、内容合规。

- 官方文档（中文，权威）：
  https://developer.huawei.com/consumer/cn/doc/AppGallery-connect-References/agc-ainetworking-interfaceinvoking_webpage-0000002312954296
- 服务总览：
  https://developer.huawei.com/consumer/cn/agconnect/ainetworking/
- 接口地址（默认端点）：
  `https://connect-api.cloud.huawei.com/api/aiNetworking/v1/webSearch`
- 鉴权方式：HTTP 请求头 `X-Api-Key: <KEY>`
- 承载协议：HTTPS POST，请求与响应均为 `application/json`

DeepSearch 仅接入 `webSearch` 端点，不复用同服务下的 `webBoxSearch`
或 `webSearch/multiLang`。

## 2. 接入方式

### 2.1 引擎枚举与注册

- 枚举值：`SearchEngine.AGC_AINETWORKING = "agc_ainetworking"`
- 包装器类：`AgcAiNetworkingSearchAPIWrapper`
- 在 `framework/openjiuwen/tools/search_api/__init__.py` 导出并在
  `framework/openjiuwen/tools/web_search.py` 的 `search_engine_mapping`
  中以 `SearchEngine.AGC_AINETWORKING.value: AgcAiNetworkingSearchAPIWrapper`
  注册。服务端 `server/` 与数据库 schema 零改动，引擎名经既有透传链
  `_load_web_search_config → register_web_search_tool → web_search_context`
  生效。

### 2.2 引擎管理页配置

在 DeepSearch 引擎管理页（详见
[模板与联网搜索引擎管理](./server/template-and-web-search-engine-management.md)）
新增一条记录，按以下字段填入：

| 字段 | 取值 | 说明 |
|---|---|---|
| `search_engine_name` | `agc_ainetworking` | 与枚举值完全一致，区分大小写 |
| `search_api_key` | 华为控制台获取的 `X-Api-Key` | 入库前加密，运行时解密为 `bytearray` 后由包装器解码 |
| `search_url` | 留空或填官方端点 | 留空回落到默认端点 `v1/webSearch`；填入自定义端点时须经 SSRF 校验 |
| `extension` | JSON 对象 | 静态配置 `sites`/`category`/`freshness`，见第 3 节 |
| `is_active` | `true` | 控制是否在运行时被加载 |

`search_url` 留空时包装器自动回落到默认常量
`DEFAULT_AGC_AINETWORKING_SEARCH_URL`。若填入与默认值不同的 URL，
包装器先经 `validate_search_service_url` 做 SSRF 校验（仅允许 http/https
scheme，拒绝 localhost、私网、回环、链路本地、CGNAT 段等非公网地址），
再 strip 后返回；内网自托管搜索服务须在部署前设置
`SEARCH_SERVICE_ALLOW_UNSAFE_URL=1`，否则 SSRF 校验会返回 HTTP 400。

## 3. 参数映射表

包装器在 `model_post_init` 中从 `extension` 静态读取以下字段。运行时
请求体由 `_build_request_body(query)` 构造，参数与华为官方接口字段一一
对应：

| 华为请求体字段 | 来源 | 规则 |
|---|---|---|
| `query` | `results(query)` / `aresults(query)` 入参 | 空字符串直接返回 `[]`，不发请求 |
| `count` | `max_web_search_results` | `min(max(max_web_search_results, 1), 50)`，夹取到 1 至 50；默认 5 |
| `freshness` | `extension["freshness"]` | 字符串，默认 `"noLimit"`；空值回落 `"noLimit"` |
| `sites` | `extension["sites"]` 与会话级 includes 合并（见第 4 节） | `normalize_domains` 归一化后取前 20 条；空数组时请求体**不含 `sites` 键** |
| `category` | `extension["category"]` | 非空列表才写入请求体；逐项 `str().strip()` 后过滤空串 |

`extension` 键名与华为官方接口字段名一致，便于运维直接对照官方文档配置：

```json
{
  "sites": ["www.pku.edu.cn", "www.sz.gov.cn"],
  "category": ["0"],
  "freshness": "noLimit"
}
```

请求头固定为
`{"X-Api-Key": <key>, "Content-Type": "application/json"}`。
若 `search_api_key` 为空则省略 `X-Api-Key`，交由服务端 401 兜底。
超时配置为连接 10 秒、读取 30 秒。

## 4. 会话级站点行为

`agc_ainetworking` 在 `SITE_DOMAIN_CONSTRAINT_SEARCH_ENGINES` 集合中注册，
因此会话级意图识别输出的 `include_domains` 与 `exclude_domains` 会进入
`apply_web_search_domain_constraints` 的 `agc_ainetworking` 分支。

### 4.1 includes（包含站点）

意图识别输出的 `include_domains` 经 `normalize_domains` 归一化后，
与包装器静态 `sites` 字段合并、去重，再取前 20 条（华为 API 上限），
写回 `api_wrapper.sites`：

```text
intent_sites = normalize_domains(include_domains, keep_www=True)
configured_sites = normalize_domains(api_wrapper.sites, keep_www=True)
merged = intent_sites + [s for s in configured_sites if s not in intent_sites]
api_wrapper.sites = merged[:20]
```

华为 AGC 端要求 `sites` 为带 `www.` 的完整 host（如 `www.huawei.com`），
apex 域名（如 `huawei.com`）不匹配。`agc_ainetworking` 分支调用
`normalize_domains` 时显式传入 `keep_www=True`，保留 `www.` 前缀；其余
归一化规则（小写化、剥 scheme、去重）与 tavily 共享。`model_post_init`
中读取 `extension["sites"]` 时同样使用 `keep_www=True`。本引擎不重写该函数。

### 4.2 excludes（排除站点）

华为 `webSearch` 接口**未提供原生 `exclude` 参数**，因此 `agc_ainetworking`
分支在 `apply_web_search_domain_constraints` 中**忽略 `exclude_domains`
参数**，仅处理 includes，日志中会注明该分工。

excludes 的实际过滤由收集器 `process_common_search_result` 调用
`filter_search_results_by_exclude_domains` 完成：所有 `source ==
"agc_ainetworking"` 的归一化行与 `agent_input.research_intent.exclude_domains`
按归一化域名匹配，命中即被丢弃。该路径零新增代码，复用引擎无关的通用后置过滤。

详见
[信息采集](./algorithm/research-collector.md)
与 T4 测试 `tests/tools/test_web_search.py` 中 excludes 闭环验收用例。

## 5. 响应归一化

包装器 `_parse_results(payload)` 把华为 `webResult[]` 数组归一化为
DeepSearch 标准行。字段映射如下：

| 标准行字段 | 来源 | 规则 |
|---|---|---|
| `title` | `webResult[].title` | `str()[:MAX_SEARCH_CONTENT_LENGTH]`；为空时用 `url` 兜底 |
| `url` | `webResult[].url` | `str().strip()[:MAX_URL_LENGTH]`；为空跳过该条 |
| `content` | `chunk` 优先，空则回退 `content`，再空为 `""` | 截断 `[:MAX_SEARCH_CONTENT_LENGTH]` |
| `source` | 字面量 | 固定 `"agc_ainetworking"` |
| `published` | `webResult[].publishTime` | Unix 秒字符串；`int()` 成功且 >0 时转 `datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()`；`"0"`/非法/缺失时不输出该键 |
| `site_name` | `webResult[].siteName` | 非空才输出；归一化为 snake_case 键，丢弃原键 |

注意：华为返回的 `chunk` 字段优先级高于 `content`，因为 `chunk` 通常已经是
按 query 抽取的精炼片段，无需在客户端再次切片。若 `chunk` 为空再回退到
`content` 字段，最后兜底为空串。

异步路径 `aresults(query)` 镜像 petal 软失败约定：HTTP 非 200/201、
`aiohttp.ClientError`、业务 `code != 0` 均记录日志并 `return []`；
同步路径 `results(query)` 在上述错误上 `raise_for_status()` 或抛
`RuntimeError`，由上层调用方决定降级策略。

## 6. 错误码表

华为 `webSearch` 接口在响应体 `code` 字段返回业务码，HTTP 层另有标准
状态码。包装器对两类错误的处理如下：

| 错误码 | 含义 | 触发位置 | 包装器行为 |
|---|---|---|---|
| `0` | 成功 | 响应体 `code` | 调 `_parse_results` 返回归一化行列表 |
| `102010` | 未开通接口权限 | 响应体 `code` | 同步抛 `RuntimeError`；异步返回 `[]` |
| `10100` | 参数错误 | 响应体 `code` | 同步抛 `RuntimeError`；异步返回 `[]` |
| `10300` | 内部错误 | 响应体 `code` | 同步抛 `RuntimeError`；异步返回 `[]` |
| `401` | 鉴权失败 | HTTP 状态码 | `raise_for_status()` 抛 HTTPError；异步 `[]` |
| `404` | 端点不存在 | HTTP 状态码 | 同上；通常因 `search_url` 配置错误 |

错误日志沿用 `LogManager.is_sensitive()` 脱敏模式，不输出原始 API key
或完整响应体。其他未列出的非零业务码一律走 `code != 0` 通用分支。

## 7. 明确不支持项

为避免范围蔓延，本期 `agc_ainetworking` 明确**不接入**以下能力，
后续如需扩展须另立计划：

- **`webBoxSearch` 的 `boxResult` 解析**。华为同服务下提供 `webBoxSearch`
  端点返回聚合卡片结果，本包装器不解析、不调用。如需接入应新建独立
  端点与解析器。
- **`webSearch/multiLang` 的 `locale` 参数**。多语言检索由 `search_url`
  切换到对应端点实现，请求体**不新增 `locale` 键**。
- **`freshness` 时间范围接线**。`freshness` 仅作为静态请求字段透传给
  华为，默认 `noLimit`。**不接 `TEMPORAL_SCOPE_SEARCH_ENGINES` / 
  `apply_web_search_temporal_scope` 机制**，意图识别输出的时间约束
  不会自动转为 `freshness` 取值。需要时间范围时由运维在 `extension`
  中显式配置 `freshness`。
- **包装器内 exclude 站点过滤**。如第 4.2 节所述，excludes 由收集器
  通用后置过滤兜底，包装器不重复实现。
- **`server/` 改动与 DB schema 变更**。服务端透传链完全复用，零改动。
- **新增第三方依赖**。仅复用 `requests`/`aiohttp`/`pydantic`/`httpx`
  等既有依赖。

## 8. 风险与人工验证项

以下风险项**不阻塞交付**，但上线前需用真实 API key 人工验收，验收结果
回填到本节或 `issues.md`：

### 8.1 www. 前缀保留（已解决）

华为官方文档示例使用 `www.pku.edu.cn` 等带 `www` 前缀的域名作为 `sites`
取值。`agc_ainetworking` 分支在 `apply_web_search_domain_constraints`
（`web_search.py`）与 `model_post_init`（`api_wrapper.py`）中调用
`normalize_domains` 时均显式传入 `keep_www=True`，保留完整 host（如
`www.huawei.com`），不再剥离 `www.` 前缀。

测试 `tests/tools/test_web_search.py` 与
`tests/tools/search_api/test_agc_ainetworking.py` 均断言
`wrapper.sites == ["www.huawei.com"]` 等 keep_www 行为。此项已通过代码实现
闭环，不再为待验证风险。

### 8.2 sites 数组格式

华为官方文档类型标注 `sites` 为 `String`，但示例为 JSON 数组
`["www.pku.edu.cn","www.sz.gov.cn"]`。包装器按**示例**发送 JSON 数组。

**待验证**：以 JSON 数组发送是否被接口接受。若接口实际要求以分号分隔的
字符串形式（如 `"www.pku.edu.cn;www.sz.gov.cn"`），需要在
`_build_request_body` 中改写 `sites` 序列化方式。

### 8.3 count 夹取上限与实际返回数量

华为文档注明「实际返回结果可能会小于 `count` 指定的数量」，不保证返回量。
不同入口对 `max_web_search_results` 的取值范围约束不同：

| 入口 | 文件 | 取值范围 | 说明 |
|---|---|---|---|
| 包装器 `_build_request_body` | `search_api/agc_ainetworking/api_wrapper.py` | 1 至 50 | `min(max(max_web_search_results, 1), 50)`，夹取到华为接口允许的上限 50 |
| 公开 SDK 配置模型 | `openjiuwen_deepsearch/config/config.py` | 1 至 10 | `Field(ge=1, le=10)`，SDK 层收紧上限，避免单次请求过载 |
| 服务端试运行（引擎管理页试搜） | `server/deepsearch/core/manager/web_search_engine_service.py` | 固定 3 | `MAX_SEARCH_RESULT_NUM = 3`，不读取用户配置，仅用于一次性试搜验证 |

因此「`max_web_search_results` 上限 50」仅指包装器层对华为接口的合规夹取，
实际可配置上限由调用入口决定。上游若依赖固定条数需自行兜底。

### 8.4 publishTime 字段格式

文档未明确 `publishTime` 的取值范围与时间基准。包装器当前实现：
`int()` 成功且 >0 视为 Unix 秒，转 UTC 日期 ISO 字符串；`"0"`/非数字/
缺失均不输出 `published` 键。若华为实际返回的是毫秒时间戳或其他格式，
需要调整解析逻辑。

## 关键代码路径

- 枚举：`openjiuwen_deepsearch/utils/constants_utils/search_engine_constants.py`
- 包装器：`openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/agc_ainetworking/api_wrapper.py`
- 注册映射与会话级约束：
  `openjiuwen_deepsearch/framework/openjiuwen/tools/web_search.py`
- 通用 exclude 后置过滤：
  `openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`
  （`process_common_search_result` / `filter_search_results_by_exclude_domains`）
- URL 与域名归一化：
  `openjiuwen_deepsearch/utils/url_utils.py`
  （`normalize_domains` / `validate_search_service_url`）

主要测试：

- `tests/tools/search_api/test_agc_ainetworking.py`
- `tests/tools/test_web_search.py`（追加 includes 合并用例与 excludes 闭环用例）

## 测试与验证

```bash
# 包装器单测
cd deepsearch && uv run pytest tests/tools/search_api/test_agc_ainetworking.py -m "not llm" -v

# 会话级约束与 excludes 闭环
cd deepsearch && uv run pytest tests/tools/test_web_search.py -m "not llm" -v

# 静态检查
cd deepsearch && uv run ruff check openjiuwen_deepsearch/framework/openjiuwen/tools/

# 全量回归
cd deepsearch && uv run pytest -m "not llm"
```

## 相关文档

- [模板与联网搜索引擎管理](./server/template-and-web-search-engine-management.md)
- [搜索工具注册与运行时 API 工具](./framework/search-tool-registration.md)
- [信息采集](./algorithm/research-collector.md)
- [时间约束软过滤](./temporal-soft-filter.md)
- 官方文档：
  https://developer.huawei.com/consumer/cn/doc/AppGallery-connect-References/agc-ainetworking-interfaceinvoking_webpage-0000002312954296
