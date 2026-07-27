# `/reports/convert`

## 接口说明

`/reports/convert` 将 DeepSearch 工作流输出的 `final_result` 转换为报告导出包。默认路由下完整路径为 `/api/v1/agent/deepsearch/reports/convert`。

支持 `html` 和 `docx` 两种 `convert_type`；响应的 `convert_content` 是 base64 编码的 ZIP 压缩包。HTML 可通过可选开关请求 LLM CSS 美化。

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
  "convert_type": "html",
  "enable_html_styling": false
}
```

- `final_result.response_content`：必填，报告 Markdown 正文。
- `final_result.infer_messages`：可选推理图资源。每个可导出元素包含 `id` 和 `html_base64`；正文中 `#inference:<id>` 链接会改写为 ZIP 内独立 HTML 的相对链接。
- `final_result.chart_messages`：可选 VLM PNG 资源。每个可导出元素包含 `chart_id`、`base64` 和可选 `chart_title`；正文中 `(#insertChart:<chart_id>)` 会改写为图像引用。
- `convert_type`：`html` 或 `docx`。
- `enable_html_styling`：可选，默认 `false`。仅 `html` 生效。
- `llm_config`：仅在 `convert_type=html` 且 `enable_html_styling=true` 时必填；可传顶层直接模型配置，或包含 `general` 的分类配置。分类配置只使用 `general`。

## 响应参数

```json
{
  "code": 200,
  "msg": "success",
  "convert_content": "<base64-zip>",
  "style_applied": false,
  "style_status": "not_requested"
}
```

`style_status` 取值为：默认 HTML 的 `not_requested`、DOCX 的 `not_supported`、样式成功的 `applied`、样式阶段回退的 `fallback`。

## HTML 样式美化

当 `convert_type=html` 且 `enable_html_styling=true` 时，`/reports/convert` 在基础 HTML 生成后按报告标题、章节树和摘要请求 LLM 生成 CSS。`llm_config` 可传顶层直接模型配置，或包含 `general` 的分类配置；分类配置只使用 `general`。LLM 不会改写 Markdown 正文、链接、图表或推理资源。

```json
{
  "code": 200,
  "msg": "success",
  "convert_type": "html",
  "enable_html_styling": true,
  "llm_config": {
    "general": {
      "model_name": "your-model",
      "model_type": "openai",
      "base_url": "https://example.com/v1",
      "api_key": "your-key"
    }
  }
}
```

样式成功时响应为：

```json
{
  "code": 200,
  "msg": "success",
  "convert_content": "<base64-zip>",
  "style_applied": true,
  "style_status": "applied"
}
```

样式生成或注入失败时，接口仍返回 HTTP 200、可打开的语义化基础 HTML、`style_applied=false` 和 `style_status=fallback`。美化请求缺少或使用非法 `llm_config` 时返回 HTTP 400；基础 bundle 或导出失败返回 HTTP 500。`docx` 始终不初始化 LLM，也不校验 `llm_config`。

## ZIP 内容结构

```text
report_bundle/
  report.md
  report.html | report.docx
  infer/                       # 可选
    inference_<id>.html
  charts/                      # 可选
    <chart_id>.png
```

- `report.md` 是完成引用链接和图表占位符重写后的中间 Markdown。
- `infer/` 中的推理 HTML 保持独立资源，不直接内嵌进主报告。
- `charts/` 始终保留 VLM 的原始 PNG；两个 HTML 导出会额外把主 HTML 中对应图片改为 Data URI，以便解压后单独打开 `report.html`。

## 图表与公式

### Mermaid

报告 Markdown 继续以 Mermaid 源码表达图表契约。`/reports/convert` 的普通和美化 HTML、DOCX 共用确定性解析和布局：仅处理项目图表生成器实际输出的纵向 `xychart-beta` 柱状图/折线图、以 `xychart-beta horizontal` 或 `horizontal: true` 标记的横向柱状图、`pie` 和 `timeline`。

- HTML 将受支持图表输出为经过文本转义的内联 SVG。
- DOCX 使用 Pillow 与仓库内置 `chart_generation/fonts/kt_font.ttf` 在内存中输出 PNG，再经 `BytesIO` 插入 Word，不创建 Mermaid 临时图片。
- 工程量级缩放和 `showDataLabel` 会在两个载体上保持相同的数据与布局语义；饼图图例显示名称和值，时间轴保持现有说明语义。
- 单个图表解析、渲染或字体加载失败时，仅保留该 Mermaid 源码块，不中断整份导出。
- 不支持其他 Mermaid 类型，也不会加载 Mermaid.js。

该功能不依赖外部 Mermaid 命令行、Node、Chrome 或额外的矢量图形运行时，不需要相关环境变量或安装步骤。

### 数学公式

- HTML 使用 MathJax 配置渲染 LaTeX，运行时代码仍从 jsDelivr CDN 加载。
- DOCX 在导出时将规范化公式转换为 Word OMML，不依赖 MathJax。

### VLM 图

- `chart_messages[*].base64` 按现有 PNG 契约解码并写入 `charts/`。
- 主 HTML 将这些 PNG 内联为 Data URI；DOCX 仍从 bundle 内图片路径插入。
- 没有新增 Base64 大小、像素或图片格式限制。

## 失败与降级行为

- `response_content` 为空、`final_result` 不是字典，或推理图/VLM 图 base64 非法时，接口返回既有校验错误。
- 主报告或 ZIP 生成失败时返回既有执行错误。
- Mermaid 单图失败是局部降级，不生成 Mermaid CLI 调试文件。
- 既有请求字段、格式枚举和 ZIP 顶层结构保持不变；响应新增样式状态字段。
