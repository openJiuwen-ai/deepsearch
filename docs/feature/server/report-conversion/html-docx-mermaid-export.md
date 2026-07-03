# HTML、DOCX 与 Mermaid 导出

## 维护范围

本文档覆盖 server 报告转换中的 Markdown 预处理、HTML 导出、DOCX 导出、Mermaid 离线渲染、图表预处理和 Word 样式处理。

不覆盖 bundle 输入契约；见报告 Bundle 文档。

## 功能目的

导出流水线把 bundle 中的 Markdown 报告转换为可浏览的 HTML 或可编辑的 DOCX，同时尽量保留表格、数学公式、Mermaid 图、图表值标签、引用和本地图片。

## 可见行为

- HTML 导出会生成完整 HTML 文件并注入报告 CSS。
- HTML 中数学公式通过 MathJax 脚本渲染。
- DOCX 导出使用纯 Python 流水线从 Markdown 生成 Word 文件。
- Mermaid 代码块会尝试通过 Mermaid CLI 渲染为图片或 SVG。
- xychart 和 timeline Mermaid 会做预处理以提升离线渲染稳定性。
- DOCX 表格、字体和标题会做后处理规范化。

## 关键代码路径

- `server/deepsearch/core/manager/report_manager/html_export.py`
- `server/deepsearch/core/manager/report_manager/docx_export.py`
- `server/deepsearch/core/manager/report_manager/mermaid_offline.py`
- `server/deepsearch/core/manager/report_manager/mermaid_preprocess.py`
- `server/deepsearch/core/manager/report_manager/mermaid_common.py`
- `server/deepsearch/core/manager/report_manager/xychart_value_labels.py`
- `server/deepsearch/core/manager/report_manager/word_utils.py`
- `server/deepsearch/core/manager/report_manager/conversion_utils.py`
- `server/deepsearch/core/manager/report_manager/css/style.css`
- `tests/server/report_manager/test_report_processor.py`

## 核心流程

1. Markdown 先经过正文预处理，保护数学公式、修正引用、表格、列表和标题边界。
2. HTML 导出读取 Markdown，替换 Mermaid 代码块，转换为 HTML 并注入样式。
3. DOCX 导出读取 Markdown，替换 Mermaid 代码块为图片，再把 HTML/Markdown 结构写入 docx。
4. Mermaid 渲染前会清理代码和解析 frontmatter。
5. timeline/xychart 会做文本压缩、单位归一化和值标签补强。
6. DOCX 生成后会规范化字体、表格居中和图片尺寸。

## 数据契约与依赖

- Mermaid 离线渲染依赖本机可用的 `mmdc` 或相关 Mermaid CLI 路径。
- HTML 和 DOCX 导出都从 bundle 的 `report.md` 读取输入。
- 本地图片必须解析到 bundle 或允许的工作区路径内。
- DOCX 默认字体在转换工具中统一设置。

## 边界与错误处理

- Mermaid 渲染失败时会保存失败源代码到 debug 路径，具体行为由渲染工具控制。
- 不安全或无法解析的本地图片不会越权读取。
- 数学公式保护只描述当前启发式行为，新增公式语法时需要覆盖 HTML 和 DOCX 两条路径。
- 修改 Mermaid 预处理时要同时验证 HTML 和 DOCX，因为两者共享部分代码但输出载体不同。

## 测试与验证

- `uv run pytest tests/server/report_manager/test_report_processor.py`
- 修改 bundle 资源重写时，补充运行 `uv run pytest tests/server/report_manager/test_report_bundle.py`。
- 若本机安装 Mermaid CLI，可用实际含 Mermaid 的 `final_result` 做手工导出验证。

## 相关文档

- [Server 报告转换](../report-conversion.md)
- [报告 Bundle 组装](./report-bundle.md)
- [图表生成](../../algorithm/chart-generation.md)
