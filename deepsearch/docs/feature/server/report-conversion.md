# Server 报告转换

## 维护范围

本文档覆盖 `/reports/convert` 的 API 边界及其共用报告导出能力。导出逻辑归属 `openjiuwen_deepsearch.algorithm.report_export`；server 只负责请求校验、运行时上下文、异常映射和响应组装。

## 可见行为

- `/reports/convert` 接收 `final_result`、`convert_type` 和可选 `enable_html_styling`，生成 HTML 或 DOCX 的 Base64 ZIP。
- HTML 美化只在开关开启时使用 `llm_config` 生成 CSS；可接受顶层直接配置或 `general` 分类配置，后者只使用 `general`。样式分支失败时仍返回 HTTP 200 和 `style_status=fallback`。
- HTML 美化在注入模型 CSS 前会检查封面背景与标题的可解析色彩对比度。低于 4.5:1 时，导出器仅覆盖 `.report-cover > h1` 为对比度更高的黑色或白色；若封面背景不可解析，则只为该标题追加不透明底板，不回退整份主题 CSS。
- 响应始终包含 `style_applied` 与 `style_status`：普通 HTML 为 `not_requested`，DOCX 为 `not_supported`。
- bundle 始终包含 `report.md` 和主导出文件，并按需包含 `infer/` 和 `charts/`。
- 两种 HTML 将 VLM PNG 引用转为 Data URI，同时 ZIP 继续保留 `charts/*.png`。
- 受支持 Mermaid 图表在 HTML 中是内联 SVG，在 DOCX 中是内存 PNG；单图失败保留源码块。
- Mermaid 图表仅识别 /run 图文并茂流程生成的第 0 列、小写、独立 mermaid fence；大小写变体、缩进和行内 fence 按普通 Markdown 保留。
- 报告接口不依赖外部 Mermaid 命令行、Node、Chrome、额外矢量图形运行时或 Mermaid.js。

## 关键代码路径

- `server/routers/report.py`
- `server/deepsearch/core/manager/report.py`
- `server/schemas/report.py`
- `openjiuwen_deepsearch/algorithm/report_export/service.py`
- `openjiuwen_deepsearch/algorithm/report_export/report_bundle.py`
- `openjiuwen_deepsearch/algorithm/report_export/html_export.py`
- `openjiuwen_deepsearch/algorithm/report_export/docx_export.py`
- `tests/server/test_report_convert.py`
- `tests/algorithm/report_export/`

## 核心流程

1. Router 接收 `ReportConvertReq`。
2. manager 在 HTML 美化时标准化 `general` 密钥并建立 framework LLM 上下文，再调用 async `export_report`；其他格式不初始化 LLM。
3. algorithm 校验 `final_result`，构建临时 `report_bundle`，重写引用和图表占位符。
4. 两种 HTML 与 DOCX 共用 Markdown-to-HTML fragment、Mermaid fence 识别和预处理后的图表场景；HTML 再包装页面变体，DOCX 再交由 Word 后端排版。
5. algorithm 将 bundle 打包为 ZIP Base64；manager 组装 `ReportConvertRes`。

## 数据契约与边界

- `final_result.response_content` 为必填非空 Markdown。
- `infer_messages` 元素可包含 `id` 与 `html_base64`；`chart_messages` 元素可包含 `chart_id`、`chart_title` 与 PNG `base64`。
- `ReportFormat` 只表示 HTTP 格式枚举，不再构造 server 处理器。
- HTML 默认保留基础外壳和 CSS；开启美化后使用语义化结构、LLM CSS 注入及 fallback 语义。
- DOCX 保留公式、表格、引用、图片和页面宽度适配；它使用仓库内置字体作为 Mermaid PNG 的内部默认实现，字体不可用时局部回退源码，转换过程不写入临时 HTML 文件。
- 公共层使用 `CustomValueException` 和 `CustomRuntimeException` 表达与 HTTP 无关的校验/执行错误；server 保持 `/convert` 的校验 400、基础导出 500、样式失败 200 fallback 语义。

## 测试与验证

- `uv run pytest tests/algorithm/report_export tests/server/test_report_convert.py`
- `uv run pytest tests/algorithm/report_style tests/server/report_manager`
- `PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q openjiuwen_deepsearch server`

## 相关文档

- [报告 Bundle 组装](./report-conversion/report-bundle.md)
- [HTML、DOCX 与 Mermaid 导出](./report-conversion/html-docx-mermaid-export.md)
- [报告生成](../algorithm/report-generation.md)
