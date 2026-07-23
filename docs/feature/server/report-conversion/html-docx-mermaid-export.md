# HTML、DOCX 与 Mermaid 导出

## 维护范围

本文档覆盖 `openjiuwen_deepsearch.algorithm.report_export` 中共享的 Markdown 预处理、HTML/DOCX 转换、Mermaid 渲染和 Word 后处理。

## 可见行为

- `/reports/convert` 的默认 HTML 使用既有基础页面外壳；开启 HTML 美化时在共享 HTML fragment 上添加语义结构和 LLM CSS。
- 支持项目生成器输出的纵向/横向 `xychart-beta`、`pie` 和 `timeline` Mermaid 图表。
- HTML 输出文本转义的内联 SVG；DOCX 使用 Pillow 在内存中生成 PNG 并通过 `BytesIO` 插入。
- `xychart` 的工程量级缩放和 `showDataLabel`、饼图名称和值、时间轴说明在两个后端保持语义一致。
- VLM PNG 在两种 HTML 内联为 Data URI，仍保留在 ZIP 的 `charts/`；DOCX 读取 bundle 内图片。
- 图表无法解析、渲染或加载 `chart_generation/fonts/kt_font.ttf` 时，只有该图保留 Mermaid 源码块。
- Mermaid fence 以 /run 图文并茂流程输出为契约：仅第 0 列、小写、独立 mermaid 代码块进入静态渲染。

## 关键代码路径

- `openjiuwen_deepsearch/algorithm/report_export/conversion_utils.py`
- `openjiuwen_deepsearch/algorithm/report_export/mermaid_preprocess.py`
- `openjiuwen_deepsearch/algorithm/report_export/chart_svg.py`
- `openjiuwen_deepsearch/algorithm/report_export/mermaid_renderer.py`
- `openjiuwen_deepsearch/algorithm/report_export/html_export.py`
- `openjiuwen_deepsearch/algorithm/report_export/docx_export.py`
- `openjiuwen_deepsearch/algorithm/report_export/word_utils.py`

## 核心流程

1. 两种格式共用 Markdown-to-HTML fragment：保护数学公式，处理引用、表格和列表边界。
2. Mermaid 预处理提取受支持图表数据，并统一单位、布局和补充说明；每个导出图只预处理一次。
3. HTML 后端生成内联 SVG；DOCX 后端以同一 SVG 场景绘制内存 PNG，不写入临时 HTML。
4. HTML 对 bundle 内 `charts/<id>.png` 进行 Data URI 内联；DOCX 写入 Word 图像关系。
5. HTML 包装页面 CSS；DOCX 继续执行 Word 字体、标题、表格和图片尺寸规范化。

## 依赖与边界

- Mermaid 静态渲染不使用外部 Mermaid 命令行、Node、Chrome、额外矢量图形运行时、Mermaid.js 或系统字体包。
- PNG 默认只使用仓库内置的 `chart_generation/fonts/kt_font.ttf`；该路径是内部实现，不是外部 API 契约。
- MathJax CDN 和非 VLM 图片原有行为不变。
- 不安全或无法解析的本地图片不会越权读取。
- 不新增 Base64 大小、图像格式或像素限制。

## 测试与验证

- `uv run pytest tests/algorithm/report_export/test_mermaid_renderer.py`
- `uv run pytest tests/algorithm/report_export/test_report_export_service.py`
- `uv run pytest tests/server/report_manager/test_report_processor.py`
