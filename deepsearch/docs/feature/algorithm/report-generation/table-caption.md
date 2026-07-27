# 表格 Caption

## 维护范围

本文档覆盖报告生成中的 Markdown 表格 caption 归一能力，包括表格标题识别、编号格式化、上下文引用修正和中文/英文标签处理。

本文档不覆盖报告正文生成 Prompt 和服务端文档转换。

## 功能目的

表格 caption 能力用于给 Markdown 表格生成稳定标题，并修正前后文中对表格的引用，避免报告中表格无标题、重复标题或引用错位。

## 可见行为

- Markdown table 前后的 caption 会被识别和归一。
- 缺少 caption 的表格会基于上下文构造标题。
- 中文使用“表 N”，英文使用对应英文 label。
- 前后文中对该表的引用会尽量同步为稳定 label。

## 关键代码路径

- 表格 caption：`openjiuwen_deepsearch/algorithm/report/table_caption_utils.py`
- 报告生成主体：`openjiuwen_deepsearch/algorithm/report/report.py`

主要测试：

- `tests/report/test_general_report.py`
- `tests/report/test_tools_in_report.py`

## 核心流程

1. 报告按行扫描 Markdown table。
2. 识别 table delimiter、table rows 和已有 caption。
3. 从前后上下文提取 caption 候选。
4. 生成稳定 table label 和标题。
5. 重写或插入 caption 行。
6. 更新相关上下文引用。

## 数据契约与依赖

输入：

- Markdown 报告正文。
- 报告语言。
- section index。

输出：

- caption 归一后的 Markdown 正文。

## 边界与错误处理

- 代码块中的表格文本不应被当成 Markdown table。
- 表格 delimiter 判断要避免误识别普通正文。
- caption 插入不能破坏表格行连续性。
- 标题过长时需要截断或清理。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/report/test_general_report.py
uv run pytest tests/report/test_tools_in_report.py
```

## 相关文档

- [报告生成总览](../report-generation.md)
- [子报告生成](./sub-report-generation.md)
