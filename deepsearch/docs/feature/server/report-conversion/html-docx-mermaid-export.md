# HTML、DOCX 与 Mermaid 导出

## 维护范围

本文档覆盖 `openjiuwen_deepsearch.algorithm.report_export` 中共享的 Markdown 预处理、HTML/DOCX 转换、Mermaid 渲染和 Word 后处理。

## 可见行为

- HTML 导出会生成完整 HTML 文件并注入报告 CSS。
- HTML 导出在转换层根据报告目录链接给对应 H1 添加 `id="chapter-N"`，普通页面和美化页面均可点击跳转；生成的原始 `report.md` 在报告生成阶段已在正文每个 H1 标题之后插入独立 `<a id="chapter-N"></a>` 锚点行，目录可在原生 Markdown 中点击跳转，转换层会先清理这些 HTML 锚点并改用 `{#chapter-N}` 属性，避免重复 ID。
- 美化 HTML 按报告顺序保留目录、摘要和章节：摘要位于目录之后、正文首章之前。
- HTML 中数学公式通过 KaTeX 脚本（`katex.min.js` + `auto-render.min.js` + `katex.min.css`，版本固定 0.16.11）渲染，使用 `$...$` / `$$...$$` 作为定界符；`\bm` 宏映射为 `\boldsymbol{#1}`，`throwOnError=false` 保证无法解析的公式不打断页面渲染。
- HTML 在 KaTeX 渲染前会做"货币美元保护"：遍历正文文本节点，把 `$` + 数字开头且不像公式的片段替换为全角 `＄`（U+FF04）占位符，渲染完成后还原为 `$`，避免 `$4`、`$1,200.50` 等金额被 KaTeX 误配对为公式定界符。
- DOCX 导出使用纯 Python 流水线从 Markdown 生成 Word 文件。
- DOCX 导出在转换层将 `#chapter-N` 目录链接转换为 Word 内部超链接，并将对应 H1 ID 转换为书签；普通外部链接行为不变。
- HTML 路径通过 `conversion_utils.protect_math_spans`、DOCX 路径通过 `word_utils._iter_math_spans` 切分公式段；两者复用 `conversion_utils` 中的公式判别函数 `_is_likely_inline_math` / 货币判别函数 `_is_currency_start` / `_find_inline_math_end` / `_is_escaped` / `_is_double_dollar`，保证两侧对"哪些 `$...$` 是公式、哪些是货币或纯文本"的判定一致。
- DOCX 超链接文本中若包含 `$...$` 或 `$$...$$`，会把公式段单独切出并转为 OMML 公式 run，其余文本保持为普通文本 run；HTML 实体先经 `html.unescape` 解码再进入公式处理。
- DOCX 列表嵌套按列表类型显式分支：同类型嵌套（`ul→ul` / `ol→ol`）沿用同一编号并增加缩进层级；异类型嵌套（如 `ul` 内含 `ol`）创建独立编号，避免编号串号。
- Mermaid 代码块通过纯 Python SVG 渲染器（`mermaid_renderer` + `chart_svg`）渲染为内联 SVG，不依赖外部命令行工具。
- xychart 和 timeline Mermaid 会做预处理以提升离线渲染稳定性。
- DOCX 表格、字体和标题会做后处理规范化。
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

1. Markdown 先经过正文预处理，保护数学公式、修正引用、表格、列表和标题边界。
2. HTML 导出读取 Markdown，替换 Mermaid 代码块，转换为 HTML 并注入样式与 KaTeX 资源（CSS + JS + auto-render 脚本）。
3. DOCX 导出读取 Markdown，替换 Mermaid 代码块为图片，再把 HTML/Markdown 结构写入 docx；行内文本和超链接文本通过 `word_utils._iter_math_spans` 切分公式与非公式段，公式段经 `_latex_to_omml` 转为 OMML 插入段落或超链接 run。
4. Mermaid 渲染前会清理代码和解析 frontmatter。
5. timeline/xychart 会做文本压缩、单位归一化和值标签补强。
6. DOCX 生成后会规范化字体、表格居中和图片尺寸。
7. 两种格式共用 Markdown-to-HTML fragment：保护数学公式，处理引用、表格和列表边界。
8. Mermaid 预处理提取受支持图表数据，并统一单位、布局和补充说明；每个导出图只预处理一次。
9. HTML 后端生成内联 SVG；DOCX 后端以同一 SVG 场景绘制内存 PNG，不写入临时 HTML。
10. HTML 对 bundle 内 `charts/<id>.png` 进行 Data URI 内联；DOCX 写入 Word 图像关系。
11. HTML 包装页面 CSS；DOCX 继续执行 Word 字体、标题、表格和图片尺寸规范化。

## 依赖与边界

- Mermaid 静态渲染为纯 Python 实现，不依赖 `mmdc`、Node、Chrome 或其他外部命令行工具；仅使用仓库内置的 `chart_generation/fonts/kt_font.ttf`。
- HTML 和 DOCX 导出都从 bundle 的 `report.md` 读取输入。
- 本地图片必须解析到 bundle 或允许的工作区路径内。
- DOCX 默认字体在转换工具中统一设置。
- HTML 公式渲染依赖 KaTeX CDN（jsdelivr），输出 HTML 会在 `<head>` 注入 `katex.min.css`，在 `<body>` 末尾注入 `katex.min.js`、`auto-render.min.js` 与一段 `DOMContentLoaded` 脚本：先做货币美元保护，再调用 `renderMathInElement`，最后还原占位符。
- 公式保护阶段（`protect_math_spans`）的输出在 HTML 路径中会被 `restore_math_spans` 用 `html.escape` 转义后原样插回（保留 `$...$` 定界符，KaTeX 在客户端解析），不再转换为 `\(...\)` / `$$...$$` 显式定界符形式。
- 公式判别（`_is_likely_inline_math`）只依据内容特征判定，包含但不限于：含数学运算符 / 希腊字母 / 反斜杠命令；纯字母或字母+数字变量名（长度不限，由总体 250 字符上限约束）；数字算术表达式；带 `%` 的百分比；函数调用 `f(t)`、`P(1,2,3)`；变量列表 `a, b, c`；带括号的元组；含 `=` 的方程式。
- 货币判别（`_is_currency_start`）把 `$` + 数字开头、且其后既无 `%` 也无数学延续字符（`\\_^([{=%` 等）、附近无配对 `$` 且段间含数学运算符的情况视为货币而非公式。

## 边界与错误处理

- Mermaid 渲染失败时会保存失败源代码到 debug 路径，具体行为由渲染工具控制。
- 不安全或无法解析的本地图片不会越权读取。
- 数学公式保护只描述当前启发式行为，新增公式语法时需要覆盖 HTML 和 DOCX 两条路径；二者共享 `conversion_utils` 中的扫描与判别函数，改动任一侧必须同时验证另一侧。
- `word_utils._iter_math_spans`（DOCX）与 `conversion_utils._protect_inline_math_spans`（HTML）均复用 `conversion_utils` 中的 `_is_currency_start` / `_is_escaped` / `_is_double_dollar` / `_find_inline_math_end` / `_is_likely_inline_math`，两侧必须得到一致的公式段切分结果。
- LaTeX → OMML 转换（`latex2mathml` → `mathml2omml`）失败时，段落内和超链接内均回退为保留原始文本（`$...$`）并发出 `logger.warning` 记录前 200 字符与异常信息，不抛出中断导出。
- HTML 中双重转义实体（如 `&amp;#92;` → `&#92;`）在 `postprocess_html` 阶段由 `_fix_double_escaped_entities` 还原为单层实体，避免页面显示字面量而非反斜杠等字符。
- 列表嵌套状态显式分支：`block_state` 为 None 或无 `list_num_id` 时新建编号；同列表类型时沿用编号并提升 `list_depth`；不同列表类型时另起编号，`list_depth` 归零，避免编号错乱。
- 修改 Mermaid 预处理时要同时验证 HTML 和 DOCX，因为两者共享部分代码但输出载体不同。
- Mermaid 静态渲染不使用外部 Mermaid 命令行、Node、Chrome、额外矢量图形运行时、Mermaid.js 或系统字体包。
- PNG 默认只使用仓库内置的 `chart_generation/fonts/kt_font.ttf`；该路径是内部实现，不是外部 API 契约。
- 非 VLM 图片原有行为不变。
- 不新增 Base64 大小、图像格式或像素限制。

## 测试与验证

- `uv run pytest tests/algorithm/report_export/test_mermaid_renderer.py`
- `uv run pytest tests/algorithm/report_export/test_report_export_service.py`
- `uv run pytest tests/server/report_manager/test_report_processor.py`
