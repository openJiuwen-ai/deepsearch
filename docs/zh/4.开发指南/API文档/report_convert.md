# `/reports/convert`

## 接口说明

`/reports/convert` 用于将 DeepSearch 工作流输出的 `final_result` 转换为报告导出包。

在默认后端路由注册方式下，完整接口路径为 `/api/v1/agent/deepsearch/reports/convert`。

当前支持两种导出类型：

- `html`
- `docx`

返回值是一个 base64 编码后的 ZIP 压缩包。

## 请求参数

```json
{
  "final_result": {
    "response_content": "# 报告标题",
    "infer_messages": [],
    "chart_messages": [],
    "warning_info": "",
    "exception_info": ""
  },
  "convert_type": "html"
}
```

字段说明：

- `final_result.response_content`
  - 报告正文 Markdown 内容。
- `final_result.infer_messages`
  - 溯源推理图资源列表。要让推理图资源进入 ZIP 包，至少需要提供以下字段：
    - `id`：推理图资源标识，用于将正文里的 `#inference:<id>` 重写为相对链接。
    - `html_base64`：推理图 HTML 的 base64 编码内容，接口会将其解码为独立 HTML 文件。
- `final_result.chart_messages`
  - VLM 图资源列表。要让图表资源进入 ZIP 包，至少需要提供以下字段：
    - `chart_id`：图表资源标识，用于匹配正文中的 `(#insertChart:<chart_id>)` 占位符。
    - `base64`：图表图片的 base64 编码内容，接口会将其解码为图片文件。
    - `chart_title`：可选；存在时会作为重写后 Markdown 图片引用的 alt 文本。
- `convert_type`
  - 目标导出格式，支持 `html` 或 `docx`。

## 响应参数

```json
{
  "code": 200,
  "msg": "success",
  "convert_content": "<base64-zip>"
}
```

字段说明：

- `convert_content`
  - base64 编码后的 ZIP 压缩包。

## ZIP 内容结构

ZIP 压缩包始终包含 `report_bundle/report.md` 和当前导出格式的主文件；推理图、图表资源和 Mermaid 调试文件会按需出现。

```text
report_bundle/
  report.md
  report.html | report.docx
  infer/                       # 可选
    inference_0.html
  charts/                      # 可选
    chart_0.png
  report_mermaid_0.mmd         # 可选，仅 Mermaid 渲染失败时出现
  report_mermaid_0.error.txt   # 可选，仅 Mermaid 渲染失败且存在错误信息时出现
```

说明：

- `report.md`
  - 中间 Markdown 文件，已经完成溯源链接和图表占位符重写。
- `report.html` 或 `report.docx`
  - 主导出结果。
- `infer/`
  - 溯源推理图 HTML 资源目录；仅当 `infer_messages` 中存在可写出的资源时出现。
- `charts/`
  - VLM 图资源目录；仅当 `chart_messages` 中存在可写出的资源时出现。
- `report_mermaid_0.mmd` / `report_mermaid_0.error.txt`
  - Mermaid 渲染失败时保留的调试文件，用于排查 Mermaid 源码或运行环境问题。

## 图表处理规则

### Mermaid 图

- HTML 导出时，优先通过 `mmdc` 离线渲染为 SVG 并内嵌到 HTML。
- DOCX 导出时，优先通过 `mmdc` 离线渲染为 PNG，再通过 pandoc 转入 DOCX。
- 如果当前环境缺少 `mmdc`，则不会中断整个导出流程，而是保留 Mermaid 源代码块。

### 数学公式

- HTML 导出会写入 MathJax 配置来渲染 LaTeX 公式，但 MathJax 运行时代码当前从 jsDelivr CDN 加载。
- 在离线或内网部署中，如果无法访问该 CDN，HTML 公式可能不会渲染；需要部署侧提供可访问的替代运行时。
- DOCX 导出会在导出阶段将规范化公式转换为 Word OMML，查看时不依赖 MathJax。

### 溯源推理图

- `infer_messages[*].html_base64` 会被解码为独立 HTML 文件。
- 正文中的 `#inference:<id>` 会重写为 ZIP 包内的相对路径链接。
- 溯源推理图不会直接内嵌进主文档，而是作为独立资源保留。

### VLM 图

- `chart_messages[*].base64` 会被解码为图片文件。
- 正文中的 `(#insertChart:<chart_id>)` 占位符会改写为 `charts/<chart_id>.png` 的 Markdown 图片引用。

## 导出环境前置要求

### Mermaid 离线渲染

Mermaid 离线渲染依赖 `mmdc`，通常可通过 npm 安装：

```bash
npm install -g @mermaid-js/mermaid-cli
```

仅安装 `mmdc` 命令本身还不够。`mmdc` 实际运行时还依赖 Puppeteer 可用的 Chrome / `chrome-headless-shell` 运行环境。推荐继续执行：

```bash
npx puppeteer browsers install chrome-headless-shell
```

如果本机已经安装 Chrome，或已经通过 Puppeteer 下载了 `chrome-headless-shell`，也可以通过环境变量显式指定浏览器可执行文件。常见示例如下：

```bash
# macOS
export PUPPETEER_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Linux
export PUPPETEER_EXECUTABLE_PATH="/usr/bin/google-chrome"

# Windows PowerShell
$env:PUPPETEER_EXECUTABLE_PATH="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

也可以通过环境变量指定可执行文件路径：

```bash
export MERMAID_MMDC_PATH=/path/to/mmdc
```

`PUPPETEER_EXECUTABLE_PATH` 也可以直接指向 Puppeteer 下载的 `chrome-headless-shell` 可执行文件。当 `mmdc` 已安装但报 `Could not find Chrome (...)` 时，优先检查并设置该变量。

如果 `/reports/convert` 由后端服务进程提供，修改 `PUPPETEER_EXECUTABLE_PATH` 或 `MERMAID_MMDC_PATH` 后需要重启后端服务，旧进程不会自动读取新的环境变量。

建议不要只通过 `mmdc --version` 判断安装完成，而是执行一次最小渲染验证：

```bash
tmpdir=$(mktemp -d)
cat > "$tmpdir/test.mmd" <<'EOF'
graph TD
A-->B
EOF
mmdc -i "$tmpdir/test.mmd" -o "$tmpdir/test.svg" -b white
```

如果 `test.svg` 能成功生成，说明 Mermaid 离线渲染依赖已经可用。

### DOCX 导出

DOCX 导出依赖 `pypandoc` 和 `pandoc` 二进制：

- 推荐方式：部署环境预先安装 `pandoc`
- 兼容方式：如果环境中没有 `pandoc`，程序会尝试通过 `pypandoc` 自动下载

### 最小验证

建议在调用 `/reports/convert` 前，先分别验证 Mermaid 与 Pandoc 依赖是否可用。

Mermaid 最小验证可直接复用上文中的 `mmdc` 渲染示例。

Pandoc 最小验证：

```bash
pandoc --version
```

如果系统未预装 `pandoc`，也可以在第一次 DOCX 导出时由程序尝试自动下载。

## 失败与降级行为

- `response_content` 为空时，接口直接失败。
- `infer_messages` 或 `chart_messages` 中的 base64 非法时，接口直接失败。
- Mermaid 渲染失败或缺少 `mmdc` 时，导出流程继续执行，但 Mermaid 会以源码块形式保留在主报告中。
- 如果 `mmdc` 已安装但缺少 Chrome / `chrome-headless-shell`，常见报错为 `Could not find Chrome (...)`。这表示 Mermaid CLI 已存在，但浏览器运行时尚未准备完成。
- Mermaid 渲染失败时，ZIP 中可能出现 `.error.txt` 与 `.mmd` 调试文件，用于保存失败原因和当时的 Mermaid 源码，便于排查环境或语法问题。
