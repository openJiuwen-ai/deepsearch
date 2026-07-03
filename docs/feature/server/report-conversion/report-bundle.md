# 报告 Bundle 组装

## 维护范围

本文档覆盖报告转换中的 bundle 工作区构建、`final_result` 校验、推理链链接重写、图表占位符重写和 ZIP 打包。

不覆盖 HTML/DOCX 渲染细节。

## 功能目的

Bundle 组装把工作流最终结果整理成稳定的磁盘目录结构，使不同导出格式共用同一份 Markdown、推理链 HTML 和图表资源。

## 可见行为

- 会在临时目录下创建 `report_bundle/`。
- `response_content` 写入 `report_bundle/report.md`。
- 推理链 HTML 写入 `report_bundle/infer/`。
- 图表图片写入 `report_bundle/charts/`。
- Markdown 中的 `#inference:<id>` 链接会改写为 bundle 内相对 HTML 链接。
- Markdown 中的 `#insertChart:<chart_id>` 占位符会改写为 bundle 内图片引用。

## 关键代码路径

- `server/deepsearch/core/manager/report_manager/report_bundle.py`
- `server/deepsearch/core/manager/report_manager/conversion_utils.py`
- `server/deepsearch/core/manager/report_manager/report_processor.py`
- `tests/server/report_manager/test_report_bundle.py`

## 核心流程

1. 校验 `final_result` 为字典，且 `response_content` 是非空字符串。
2. 校验 `infer_messages` 和 `chart_messages` 是字典列表。
3. 创建 `report_bundle`、`infer`、`charts` 和 `assets` 目录。
4. 校验资源 ID 只包含字母、数字、下划线和连字符。
5. 解码推理链 HTML 和图表图片 base64 内容并写入对应目录。
6. 去除内部引用标记，重写推理链链接和图表占位符。
7. 写入 `report.md` 并返回 `ReportBundle`。
8. 打包时按 `report_bundle` 父目录为相对根写入 ZIP。

## 数据契约与依赖

- 安全资源 ID 正则为 `^[A-Za-z0-9_-]+$`。
- 推理链原始链接格式为 `[label](#inference:<id>)`。
- 图表占位符格式为 `(#insertChart:<chart_id>)`。
- 缺少 `html_base64` 或图表 `base64` 的资源会被跳过。

## 边界与错误处理

- 资源 ID 包含路径分隔符或其他非法字符时返回校验异常，防止路径穿越。
- base64 解码使用 strict validation。
- `infer_messages` 或 `chart_messages` 为 `None` 时按空列表处理。
- ZIP 打包只包含实际文件，不包含空目录。

## 测试与验证

- `uv run pytest tests/server/report_manager/test_report_bundle.py`
- 修改 `final_result` 契约时，补充运行 `uv run pytest tests/server/test_report_convert.py`。

## 相关文档

- [Server 报告转换](../report-conversion.md)
- [HTML、DOCX 与 Mermaid 导出](./html-docx-mermaid-export.md)
