# 报告模板生成

## 维护范围

本文档覆盖 `openjiuwen_deepsearch/algorithm/report_template/` 下的报告模板生成能力，包括上传文件校验、PDF/DOCX/Markdown/HTML 内容提取、模板结构抽取、语义抽取和模板数量/大小限制。

本文档不覆盖服务端模板存储接口、报告正文生成和最终文档转换。

## 功能目的

报告模板生成用于把用户上传的样例报告或已有模板转换为系统可复用的 Markdown 模板。样例报告会经过 LLM 两阶段抽取；已有模板则保留结构并做后处理。

## 可见行为

- 输入文件名后缀决定处理路径。
- 样例报告支持 `.md`、`.html`、`.pdf`、`.docx`。
- 已有模板当前只接受 `.md`。
- 成功时返回 base64 编码的模板内容，失败时返回 `status=fail` 和错误信息。
- 样例报告抽取会先提取结构，再结合结构提取语义模板。

## 关键代码路径

- 模板生成入口：`openjiuwen_deepsearch/algorithm/report_template/template_generator.py`
- 文件解析与校验：`openjiuwen_deepsearch/algorithm/report_template/template_utils.py`

相关 Prompt：

- `openjiuwen_deepsearch/algorithm/prompts/template_structure_extract.md`
- `openjiuwen_deepsearch/algorithm/prompts/template_semantic_extract.md`

主要测试：

- `tests/report_template/test_template_generate.py`
- `tests/report_template/test_template_utils.py`

## 核心流程

1. `TemplateGenerator.generate_template` 校验 agent config 并注册 general LLM。
2. 根据 `is_template` 校验文件后缀。
3. PDF/DOCX/文本类输入被转换为 Markdown 或文本内容。
4. 已有模板走结构保留后处理。
5. 样例报告走结构抽取 Prompt，再走语义抽取 Prompt。
6. 结果经过 bad signal 和空内容检查。
7. 成功内容以 UTF-8 base64 返回。

## 数据契约与依赖

输入字段：

- `file_name`
- `file_stream`：base64 文件内容。
- `is_template`
- `agent_config`：必须包含 general LLM 配置。

输出字段：

- `status`：`success` 或 `fail`。
- `template_content`：base64 编码的模板内容。
- `error_message`：失败原因。

## 边界与错误处理

- 文件名为空、后缀不支持、模板数量过多或文件过大时抛出参数类异常。
- PDF 页数、DOCX 解压大小和 XML 大小都有上限。
- LLM 返回空内容或包含错误信号时会重试，重试耗尽后返回失败。
- 敏感日志模式下不输出模板正文或原始报告内容。
- `llm_context` token 必须在成功或异常路径中 reset。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report_template
```

## 相关文档

- [报告生成](./report-generation.md)
- [Prompt 模板系统](./prompt-template-system.md)
