# HTML、DOCX 与 Mermaid 导出

## 维护范围

本文档覆盖 server 报告转换中的 Markdown 预处理、HTML 导出、DOCX 导出、Mermaid 离线渲染、图表预处理和 Word 样式处理。

不覆盖 bundle 输入契约；见报告 Bundle 文档。

## 功能目的

导出流水线把 bundle 中的 Markdown 报告转换为可浏览的 HTML 或可编辑的 DOCX，同时尽量保留表格、数学公式、Mermaid 图、图表值标签、引用和本地图片。

## 可见行为

- HTML 导出会生成完整 HTML 文件并注入报告 CSS。
- HTML 中数学公式通过 KaTeX 脚本（`katex.min.js` + `auto-render.min.js` + `katex.min.css`，版本固定 0.16.11）渲染，使用 `$...$` / `$$...$$` 作为定界符；`\bm` 宏映射为 `\boldsymbol{#1}`，`throwOnError=false` 保证无法解析的公式不打断页面渲染。
- HTML 在 KaTeX 渲染前会做"货币美元保护"：遍历正文文本节点，把 `$` + 数字开头且不像公式的片段替换为全角 `＄`（U+FF04）占位符，渲染完成后还原为 `$`，避免 `$4`、`$1,200.50` 等金额被 KaTeX 误配对为公式定界符。
- DOCX 导出使用纯 Python 流水线从 Markdown 生成 Word 文件。
- HTML 与 DOCX 共用同一套公式扫描器 `conversion_utils._iter_math_spans` / 公式判别函数 `_is_likely_inline_math` / 货币判别函数 `_is_currency_start`，保证两侧对"哪些 `$...$` 是公式、哪些是货币或纯文本"的判定一致。
- DOCX 超链接文本中若包含 `$...$` 或 `$$...$$`，会把公式段单独切出并转为 OMML 公式 run，其余文本保持为普通文本 run；HTML 实体先经 `html.unescape` 解码再进入公式处理。
- DOCX 列表嵌套按列表类型显式分支：同类型嵌套（`ul→ul` / `ol→ol`）沿用同一编号并增加缩进层级；异类型嵌套（如 `ul` 内含 `ol`）创建独立编号，避免编号串号。
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
2. HTML 导出读取 Markdown，替换 Mermaid 代码块，转换为 HTML 并注入样式与 KaTeX 资源（CSS + JS + auto-render 脚本）。
3. DOCX 导出读取 Markdown，替换 Mermaid 代码块为图片，再把 HTML/Markdown 结构写入 docx；行内文本和超链接文本通过 `_iter_math_spans` 切分公式与非公式段，公式段经 `_latex_to_omml` 转为 OMML 插入段落或超链接 run。
4. Mermaid 渲染前会清理代码和解析 frontmatter。
5. timeline/xychart 会做文本压缩、单位归一化和值标签补强。
6. DOCX 生成后会规范化字体、表格居中和图片尺寸。

## 数据契约与依赖

- Mermaid 离线渲染依赖本机可用的 `mmdc` 或相关 Mermaid CLI 路径。
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
- `_iter_math_spans` 复用 `_is_currency_start` / `_is_escaped` / `_is_double_dollar` / `_find_inline_math_end` / `_is_likely_inline_math`，HTML 与 DOCX 必须得到一致的公式段切分结果。
- LaTeX → OMML 转换（`latex2mathml` → `mathml2omml`）失败时，段落内和超链接内均回退为保留原始文本（`$...$`）并发出 `logger.warning` 记录前 200 字符与异常信息，不抛出中断导出。
- HTML 中双重转义实体（如 `&amp;#92;` → `&#92;`）在 `postprocess_html` 阶段由 `_fix_double_escaped_entities` 还原为单层实体，避免页面显示字面量而非反斜杠等字符。
- 列表嵌套状态显式分支：`block_state` 为 None 或无 `list_num_id` 时新建编号；同列表类型时沿用编号并提升 `list_depth`；不同列表类型时另起编号，`list_depth` 归零，避免编号错乱。
- 修改 Mermaid 预处理时要同时验证 HTML 和 DOCX，因为两者共享部分代码但输出载体不同。

## 测试与验证

- `uv run pytest tests/server/report_manager/test_report_processor.py`
- 修改 bundle 资源重写时，补充运行 `uv run pytest tests/server/report_manager/test_report_bundle.py`。
- 若本机安装 Mermaid CLI，可用实际含 Mermaid 的 `final_result` 做手工导出验证。

## 相关文档

- [Server 报告转换](../report-conversion.md)
- [报告 Bundle 组装](./report-bundle.md)
- [图表生成](../../algorithm/chart-generation.md)
