# LaTeX → OMML 转换

## 维护范围

本文档覆盖 Markdown 报告导出 DOCX 时的 LaTeX 公式转 OMML 流程，含
`openjiuwen_deepsearch/algorithm/report_export/word_utils.py` 中的归一化、转换与
回退逻辑。HTML/KaTeX 公式渲染走 `html_export.py`，不在本文档范围内，参见
[Server 报告转换](./server/report-conversion.md)。

## 功能目的

把 Markdown 中 `$...$` 与 `$$...$$` 形式的 LaTeX 公式渲染为 Word 原生 OMML
公式对象（`<m:oMath>`），保证 DOCX 报告中数学表达式可见、可编辑、被 Word
识别为公式，而不是退化为纯文本或截图。

## 可见行为

- DOCX 导出时，行内与块级 LaTeX 公式都转为 OMML。
- 转换失败时，公式按原始 `$$...$$` 文本插入段落。导出不中断，行为降级而非崩溃。
- 失败会在日志中记录 `LaTeX→OMML 转换失败` WARNING，附公式前 200 字符与异常类型。

## 关键代码路径

- `openjiuwen_deepsearch/algorithm/report_export/conversion_utils.py`：`render_markdown_html_fragment`、`protect_math_spans`、`restore_math_spans`
- `openjiuwen_deepsearch/algorithm/report_export/word_utils.py`：`_latex_to_omml`、`_normalize_latex_for_omml`、`_merge_arg_min_max`、`_strip_redundant_mathop`、`_process_text_inline`、`_iter_math_spans`、`_insert_omml`
- `openjiuwen_deepsearch/algorithm/report_export/docx_export.py`：`convert_md_to_docx`（入口）
- `tests/server/report_manager/test_report_processor.py`

## 核心流程

1. Markdown 文本经 `protect_math_spans` 用 NUL 占位符替换公式段，避开 Markdown 语法误吞公式。
2. `markdown` 库渲染替换后的文本为 HTML 片段。
3. `restore_math_spans` 把公式段还原到 HTML 文本节点。
4. DOCX 后端遍历文本节点，对每个数学段调 `_iter_math_spans` 切分。
5. 段内 LaTeX 经 `_normalize_latex_for_omml` 归一化，再调 `latex2mathml` 转 MathML，再调 `mathml2omml` 转 OMML。
6. `_insert_omml` 把 OMML 节点插入 `<w:r>` 之内。
7. 任一步抛异常时，`_process_text_inline` 捕获，记录 WARNING，按原始文本重新插入段落。

归一化顺序：`_strip_redundant_mathop(_merge_arg_min_max(_strip_latex_alignment_markers(latex)))`，之后迭代调用 `_wrap_grouped_command_powers` 至多 8 次。`_strip_redundant_mathop` 必须放在 `_merge_arg_min_max` 之后，因为后者会主动产出 `\mathop{\operatorname{...}}` 形态。

## 数据契约与依赖

- 输入：LaTeX 数学模式表达式，不含 `$` 分隔符（`_iter_math_spans` 已剥离）。
- 输出：OMML XML 字符串，可直接插入 python-docx 段落。
- 依赖：
  - `latex2mathml>=3.78.1`（`pyproject.toml`），`uv.lock` 锁定 `3.81.0`；导入名 `latex2mathml`。
  - `mathml2omml-as==0.1.0`（`pyproject.toml` 与 `uv.lock` 固定），导入名 `mathml2omml`。
- 关键正则：`_MATHOP_OPERATORNAME_RE = re.compile(r"\\mathop\s*\{(\\operatorname\*?\{[^{}]*\})\}")`。匹配时把外层 `\mathop{...}` 去掉，保留内部 `\operatorname{...}`。

## 边界与错误处理

### 已覆盖

`\mathop{\operatorname{...}}_{...}` 形态被 `_strip_redundant_mathop` 处理。当外层 `\mathop{...}` 包裹的是 `\operatorname{...}` 或星号变体 `\operatorname*{...}` 时，外层被剥除。`\operatorname` 本身已具备算子语义，无需再套 `\mathop`。

### 未覆盖（已知限制，非回归）

下列三类输入当前正则不匹配，原始 `\mathop{...}` 保留，触发下游崩溃与文本回退：

1. `\mathop{X}` 且 `X` 不是 `\operatorname{...}`。例如 `\mathop{\mathrm{diag}}`、`\mathop{X}`。`latex2mathml 3.81.0` 产出非法 `<mo><mrow>X</mrow></mo>`；`mathml2omml` 的 `MO` 类抛 `AttributeError: 'MO' object has no attribute 'append'`。修复路径：升级 `latex2mathml` 或扩展 `_strip_redundant_mathop`。
2. `\mathop{ \operatorname{...} }`，外层大括号内有空白。正则不匹配，原样保留，触发崩溃与回退。修复路径：在 `_MATHOP_OPERATORNAME_RE` 外层大括号内加 `\s*`，一行改动。
3. `\operatorname{...}` 内部含嵌套大括号，例如 `\operatorname{\mathbf{arg}}`。正则 `[^{}]*` 不匹配，原样保留，触发崩溃与回退。修复路径：用 `_find_latex_group_end` 辅助替代 `[^{}]*`。

### 失败模式

上述任一限制命中时：原始 `\mathop{...}` 保留 → `latex2mathml` 产出非法 MathML → `mathml2omml` 崩溃 → `_process_text_inline` 捕获异常 → 回退把原始 `$$...$$` 文本作为 `<w:t>` 插入。这是设计内的优雅降级，不是 bug。检测方式：日志中出现 `LaTeX→OMML 转换失败` WARNING，附带失败 LaTeX 与异常。

## 测试与验证

- 推荐：`uv run pytest tests/server/report_manager/test_report_processor.py -k "strip_redundant_mathop or normalize_latex_for_omml_strips or mathop_argmax_bug_formula"`
- 覆盖场景：
  - `test_strip_redundant_mathop_strips_operatorname_wrapper`
  - `test_strip_redundant_mathop_strips_starred_operatorname_wrapper`
  - `test_strip_redundant_mathop_leaves_non_operatorname_unchanged`
  - `test_strip_redundant_mathop_leaves_bare_identifier_unchanged`
  - `test_normalize_latex_for_omml_strips_mathop_after_merge_arg_min_max`
  - `test_convert_md_to_docx_renders_mathop_argmax_bug_formula`

## 相关文档

- [AGENTS.md - Feature Documentation](../../AGENTS.md)（feature 文档规范）
- [Feature 文档模板](_template.md)（模板源）
- [Server 报告转换](./server/report-conversion.md)（报告转换 API 边界与外层导出能力）
