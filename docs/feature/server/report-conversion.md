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

## 边界与错误处理

- `final_result` 不是字典、`response_content` 为空或类型不对，会返回报告转换校验异常。
- base64 或 UTF-8 解码失败会返回校验异常。
- 转换执行失败返回报告转换执行异常。
- Router 将报告转换业务异常映射为 HTTP 400，其他异常映射为 HTTP 500。
- 所有导出工作区使用临时目录，避免把中间文件写入固定运行目录。

## 测试与验证

- `uv run pytest tests/server/test_report_convert.py`
- `uv run pytest tests/server/report_manager/test_report_processor.py`
- `uv run pytest tests/server/report_manager/test_report_bundle.py`

## 相关文档

- [报告生成](../algorithm/report-generation.md)
- [图表生成](../algorithm/chart-generation.md)
- [全局溯源](../algorithm/source-trace.md)
