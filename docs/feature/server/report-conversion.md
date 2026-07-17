# Server 报告转换

## 维护范围

本文档覆盖 `/reports/convert` 把 DeepSearch `final_result` 转换为 HTML/DOCX ZIP bundle 的 server 能力。子能力细节见：

- [报告 Bundle 组装](./report-conversion/report-bundle.md)
- [HTML、DOCX 与 Mermaid 导出](./report-conversion/html-docx-mermaid-export.md)

不覆盖 algorithm 层报告生成过程。

## 功能目的

报告转换为前端和 API 调用方提供离线导出能力，将工作流最终结果中的 Markdown 正文、推理链 HTML 和图表图片统一打包为可下载的 ZIP，并在 ZIP 内包含指定格式的主报告文件。

## 可见行为

- 请求体包含 `final_result` 和 `convert_type`。
- `convert_type=html` 时生成 `report.html`。
- `convert_type=docx` 时生成 `report.docx`。
- 响应中的 `convert_content` 是 ZIP 压缩包的 base64 字符串。
- ZIP bundle 会包含中间 `report.md`、主导出文件、推理链 HTML 和图表图片资源。
- 转换成功返回 `code=200` 和 `msg=success`。
- `/reports/stylize` 在生成同结构的 HTML ZIP bundle 后，调用配置的 LLM 生成桌面报告视觉系统 CSS；成功时返回 `style_applied=true` 和 `style_status=applied`。
- `/reports/stylize` 会将受支持的 Mermaid 图表 Markdown 代码块确定性转换为内嵌 SVG：支持纵向 `xychart-beta` 柱状图/折线图、以 `xychart-beta horizontal` 或 `horizontal: true` 标记的横向柱状图、`pie` 饼图和 `timeline` 时间轴。混合正负值柱状图会单独绘制并标注零基线，避免与最小值网格线混淆。此路径不调用 `mmdc`、Mermaid.js 或额外 LLM，不创建 Mermaid CLI 中间文件或调试文件。其他 Mermaid 类型保留为源码块。
- 基础 HTML 会确定性地加入 `report-page`、`report-shell`、`report-cover`、`report-abstract`、`report-content`、`report-section`、`report-figure`、`report-table` 语义包装器，不改写报告文本、链接、SVG 或资源路径。
- 第一个 h1 至 h6 标题（包括“摘要”或“Abstract”）会作为封面；后续相同层级标题开启章节，较低层级标题保留为章节内嵌内容。没有标题时第一个非空白内容块作为封面，其余正文进入稳定章节；仅首个标题之后的任意层级“摘要”或“Abstract”标题形成摘要区。即使全部源内容被封面消费，`report-content` 仍包含一个稳定的 `report-section`。
- 基础 HTML 会为 `.report-section > h1` 提供一级章节层级样式，并在 `.report-table th` 上直接提供高对比度的背景色与文字颜色。模型主题 CSS 可以覆盖这些默认值；当模型遗漏相应选择器时，报告仍不会出现无层级章节或白底白字表头。
- 基础 HTML 的页面画布由 `.report-shell` 的固定 1280px 宽度定义。样式提示词仍建议模型不使用媒体查询且不改变该宽度，但这是视觉指导，不是运行时限制。
- 样式生成超时、模型失败或 CSS 为空/类型不合法时，`/reports/stylize` 返回未美化的 HTML ZIP，并以 `style_applied=false`、`style_status=fallback` 标识回退。

## 关键代码路径

- `server/routers/report.py`
- `server/deepsearch/core/manager/report.py`
- `server/schemas/report.py`
- `server/deepsearch/core/manager/report_manager/report_processor.py`
- `server/deepsearch/core/manager/report_manager/report_bundle.py`
- `tests/server/test_report_convert.py`
- `tests/server/report_manager/test_report_processor.py`
- `tests/server/report_manager/test_report_bundle.py`

## 核心流程

1. Router 接收 `ReportConvertReq`。
2. `ReportFormat.get_processor` 按 `convert_type` 选择 `ReportHtml` 或 `ReportWord`。
3. 处理器创建临时目录并调用 `convert_from_final_result`。
4. `build_report_bundle` 校验并组装导出工作区。
5. HTML 或 DOCX 导出器读取 bundle 中的 Markdown 生成主报告文件。
6. `pack_bundle_to_base64` 将 `report_bundle` 目录打包为 ZIP 并 base64 编码。
7. manager 返回 `ReportConvertRes`。

## 数据契约与依赖

- `final_result.response_content` 是必填 Markdown 正文。
- `final_result.infer_messages` 是可选列表，元素可包含 `id` 和 `html_base64`。
- `final_result.chart_messages` 是可选列表，元素可包含 `chart_id`、`chart_title` 和 `base64`。
- bundle 内推理链文件路径为 `infer/inference_<id>.html`。
- bundle 内图表文件路径为 `charts/<chart_id>.png`。
- `/reports/stylize` 额外要求 `llm_config`；支持直接模型配置，或优先选择 `writing_checking`、其次 `general` 的类别配置。
- 样式模型只接收报告标题、标题树、完整摘要（无摘要时首个正文块最多 4,000 字符）和资源统计；不接收完整长报告正文。
- `/reports/stylize` 的受支持 Mermaid 图表不依赖外部运行时，SVG 已直接包含在 `report.html` 中，可离线查看；不支持的 Mermaid 不会加载额外脚本。
- 样式提示词使用上述受限上下文，要求 CSS-only 输出，并要求模型按报告主题设计封面、摘要、章节、数据表格、图表、图注和引用。提示词明确封面、摘要和一级章节均使用 h1，且表头必须在 `.report-table th` 上直接设置背景色与文字颜色。它面向固定桌面画布，并建议不输出 `@media` 或改变由 `.report-shell` 控制的 1280px 页面宽度。
- 样式服务仅校验模型 CSS 为非空字符串并移除可选 CSS 围栏，随后将剩余文本原样注入基础 HTML；不会对选择器、属性、at-rule、内容声明或宽度声明进行运行时过滤。

## 边界与错误处理

- `final_result` 不是字典、`response_content` 为空或类型不对，会返回报告转换校验异常。
- base64 或 UTF-8 解码失败会返回校验异常。
- 转换执行失败返回报告转换执行异常。
- Router 将报告转换业务异常映射为 HTTP 400，其他异常映射为 HTTP 500。
- 所有导出工作区使用临时目录，避免把中间文件写入固定运行目录。
- `/reports/stylize` 的请求或 LLM 配置非法返回 HTTP 400；基础 bundle、HTML 或 ZIP 导出失败返回 HTTP 500。
- 样式服务不额外施加调用墙钟超时；底层 LLM 客户端按其运行时配置处理请求超时。仅样式分支失败时必须保留基础 HTML 并正常返回 HTTP 200 fallback。

## 测试与验证

- `uv run pytest tests/server/test_report_convert.py`
- `uv run pytest tests/server/report_manager/test_report_processor.py`
- `uv run pytest tests/server/report_manager/test_report_bundle.py`
- `uv run pytest tests/framework/test_report_style_runtime.py tests/algorithm/report_style tests/server/test_report_convert.py`

## 相关文档

- [报告生成](../algorithm/report-generation.md)
- [图表生成](../algorithm/chart-generation.md)
- [全局溯源](../algorithm/source-trace.md)
