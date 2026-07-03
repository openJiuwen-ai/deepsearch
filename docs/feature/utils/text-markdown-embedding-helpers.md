# 文本、Markdown 与 Embedding 辅助

## 维护范围

本文档覆盖轻量文本处理、Markdown URL 边界解析和 embedding 请求 SSL verify 参数解析。

不覆盖 LLM 调用、URL SSRF 校验或报告 Markdown 生成算法；这些内容分别见 [LLM 调用辅助](./llm-invocation-utils.md)、
[参数校验、安全目录与 URL 处理](./validation-security-url.md) 和 [报告生成](../algorithm/report-generation.md)。

## 功能目的

这些辅助函数为报告处理、溯源、输入校验和 embedding 客户端提供小型、可复用的文本边界处理能力。

## 可见行为

- 文本工具支持 HTML 转义、Markdown 链接文本转义、中文句子切分、字符串长度校验和截断。
- Markdown URL 工具能处理 URL 中的嵌套括号和转义括号。
- embedding SSL 工具根据 `EMBEDDING_SSL_VERIFY` 和 `EMBEDDING_SSL_CERT` 生成 requests/http 客户端可用的 verify 参数。
- embedding base URL 非 https 时，verify 返回 `True`。
- 未设置 `EMBEDDING_SSL_VERIFY` 时，https embedding 默认不校验证书。

## 关键代码路径

- `openjiuwen_deepsearch/utils/common_utils/text_utils.py`
- `openjiuwen_deepsearch/utils/common_utils/markdown_url_utils.py`
- `openjiuwen_deepsearch/utils/common_utils/embedding_utils.py`
- `openjiuwen_deepsearch/utils/common_utils/url_utils.py`
- `tests/utils/test_url_utils.py`
- `tests/source_tracer/test_add_source.py`

## 核心流程

1. 文本进入报告、溯源或日志展示前，按场景调用 HTML 或 Markdown 转义。
2. 需要切句时，`split_into_sentences` 按中文标点、英文感叹号/问号、分号和换行切分。
3. Markdown 链接解析从左括号偏移开始扫描，维护括号深度。
4. 遇到反斜杠转义字符时，跳过下一个字符，避免误判 URL 结束。
5. embedding 请求创建前先校验 base URL，再根据环境变量返回 verify 参数。

## 数据契约与依赖

- Markdown URL 提取返回 `(url, end_offset)`，未闭合时返回 `None`。
- `truncate_string` 会先把输入转成字符串并 strip，再按 start/max_length 截断。
- `EMBEDDING_SSL_VERIFY=false` 或未设置时，https verify 为 `False`。
- `EMBEDDING_SSL_CERT` 设置时，在启用 SSL verify 的情况下返回证书路径字符串。

## 边界与错误处理

- `escape_html_text` 和 `escape_markdown_link_text` 对空输入返回空字符串。
- `validate_string_length(None)` 返回 `False`。
- `truncate_string` 在转换字符串失败时返回空字符串并记录 error。
- Markdown URL 未闭合或起点不是 `(` 时返回 `None`。
- embedding base URL 会复用 SSRF 校验，不安全地址会抛参数错误。

## 测试与验证

- `uv run pytest tests/utils/test_url_utils.py`
- 修改 Markdown URL 边界行为时，补充运行溯源和报告相关测试。

## 相关文档

- [参数校验、安全目录与 URL 处理](./validation-security-url.md)
- [报告生成](../algorithm/report-generation.md)
- [全局溯源](../algorithm/source-trace.md)
