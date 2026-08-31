# 模板与联网搜索引擎管理

## 维护范围

本文档覆盖 server 层报告模板导入/查询/更新/删除，以及联网搜索引擎配置的 CRUD 和试运行接口。

不覆盖报告模板生成算法细节或具体外部搜索引擎 API 语义。

## 功能目的

模板与联网搜索引擎管理为前端提供可持久化的运行配置：模板用于影响报告结构，联网搜索引擎配置用于 DeepSearch 运行时加载 web search key、URL 和扩展参数。

## 可见行为

- 模板导入会调用模板生成器，把上传文件或模板文件转换为 `template_content` 后存库。
- 同一 `space_id` 下模板名相同时，导入会覆盖原模板内容和描述。
- 模板名称只允许中文、英文、数字、下划线、连字符和点，最长 200。
- 模板列表按创建时间倒序返回。
- 联网搜索引擎同一 `space_id` 下按 `search_engine_name` 防重。
- 搜索引擎 API key 入库前加密，读取运行详情时解密。
- 搜索引擎试运行最多返回 3 条搜索结果。

## 关键代码路径

- `server/routers/report_template.py`
- `server/deepsearch/core/manager/template_manager.py`
- `server/deepsearch/core/manager/repositories/report_template_repository.py`
- `server/deepsearch/core/models/report_template.py`
- `server/routers/web_search_engine_router.py`
- `server/deepsearch/core/manager/web_search_engine_service.py`
- `server/deepsearch/core/manager/repositories/web_search_engine_repository.py`
- `server/deepsearch/core/models/web_search_engine_model.py`
- `server/schemas/report_template.py`
- `server/schemas/web_search_engine.py`
- `tests/server/test_web_search_engine_schema.py`

## 核心流程

1. 模板 router 用统一 handler 把业务异常映射为 HTTP 400/404/500。
2. 导入模板时把 LLM key 字符串转换为 `bytearray`，再调用 `TemplateGenerator.generate_template`。
3. 生成成功后按 `space_id + template_name` 查重，存在则覆盖，不存在则创建。
4. DeepSearch run 若传入 `template_id>0`，Agent manager 按 `space_id` 加载模板内容。
5. 搜索引擎创建/更新时加密 API key 并写入数据库。
6. DeepSearch run 加载 web search config 时按 `space_id + id` 读取并解密。
7. 试运行接口从 framework 的 `search_engine_mapping` 找 wrapper 并执行查询。

## 数据契约与依赖

- `report_template` 表对 `space_id + template_name` 有唯一约束。
- `web_search_engine` 表保存 `space_id`、`search_engine_name`、`search_api_key`、`search_url`、`extension` 和 `is_active`。
- Web search engine detail 返回前会解密 `search_api_key`。
- 试运行使用 `WebSearchEnginePostRequestDTO.query`，默认值为“人工智能的发展”。

## 边界与错误处理

- 模板生成返回非 success 时抛模板生成异常并回滚事务。
- 模板不存在返回 HTTP 404。
- 搜索引擎不存在、未注册或 API key 解密失败会返回对应业务异常。
- 搜索引擎 wrapper 返回非 list 时试运行结果按空列表处理。
- 外部搜索引擎执行异常会包装为执行异常，避免泄露未分类异常类型。
- 用户配置的 `search_url` 在创建、更新及试运行前会做 SSRF 校验：仅允许 http/https scheme，拒绝 localhost、私网、回环、链路本地等非公网地址，并对域名做 DNS 解析后地址范围校验。空 `search_url` 放行（各 wrapper 回落到官方默认 URL）。内网自托管搜索端点可用环境变量 `SEARCH_SERVICE_ALLOW_UNSAFE_URL=1` 旁路。

## 测试与验证

- `uv run pytest tests/server/test_web_search_engine_schema.py`
- 修改模板生成或持久化时，补充运行 `uv run pytest tests/report_template`。
- 修改搜索引擎 wrapper 运行时，补充运行 `uv run pytest tests/tools/test_web_search.py`。

## 相关文档

- [报告模板生成](../algorithm/report-template.md)
- [搜索工具注册与运行时 API 工具](../framework/search-tool-registration.md)
- [DeepSearch Agent 配置组装](./deepsearch-agent-config.md)
