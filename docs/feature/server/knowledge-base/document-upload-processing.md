# 知识库文档上传与处理

## 维护范围

本文档覆盖知识库文档上传、metadata 同步、文件类型/大小校验、OBS 同步、后台串行处理、文件解析、分块和状态流转。

不覆盖 Milvus 写入细节；见索引文档。

## 功能目的

文档上传与处理把用户上传的 PDF、Word、文本和 Markdown 文件转为可索引的文档对象，并通过状态字段向前端暴露处理进度和失败原因。

## 可见行为

- 上传接口支持多文件表单上传。
- Router 层允许 `.pdf`、`.doc`、`.docx`、`.txt`、`.md`。
- Manager 层再次校验文件类型并限制单文件最大 20 MiB。
- `metadata.doc_list` 存在时，会删除当前知识库中不在列表内的文档记录。
- 已存在于 `metadata.doc_list` 的文档 ID 会跳过重复上传。
- 上传成功的文档初始状态为 `uploaded`。
- 处理接口只处理 `uploaded` 状态文档；已 `indexed` 的文档会跳过。
- 后台处理会按文档串行执行，失败时把状态更新为 `failed` 并写入错误信息。

## 关键代码路径

- `server/routers/knowledge_base.py`
- `server/local_retrieval/core/manager/knowledge_base.py`
- `server/local_retrieval/core/parser/resilient_pdf_parser.py`
- `server/local_retrieval/models/knowledge_base_document.py`
- `server/schemas/knowledge_base.py`

## 核心流程

1. 上传接口校验文件列表、扩展名和 metadata JSON。
2. manager 校验知识库存在和 OBS 配置要求。
3. 为每个文件分配 `doc_id`，保存到本地知识库目录。
4. Redis 多实例模式下上传到 OBS，并把对象名写入文档记录。
5. 创建文档数据库记录，状态为 `uploaded`。
6. 处理接口读取文档和知识库配置，生成 `task_id` 和基础 `process_info`。
7. 有效文档状态更新为 `processing`，并创建后台串行处理任务。
8. 单文档处理先解析文件，再调用索引流程，成功后置为 `indexed`，失败后置为 `failed`。

## 数据契约与依赖

- `DocumentProcessRequest` 包含 parsing、segmentation 和 indexing 三类策略。
- 分段策略支持 `chunk_size` 或 `max_tokens`，`chunk_overlap` 或 `chunk_overlap_percent`。
- `chunk_unit=token` 时会把 embedding model 传给 chunker。
- `.doc` 实际为 docx 时，会创建临时 `.docx` 副本用于解析，结束后清理。
- PDF 解析使用 resilient parser，避免异常 bbox 导致解析中断。

## 边界与错误处理

- 文档不存在、状态不合法或缺少文件路径时，会标记失败并记录 `process_info.error`。
- OBS 必需但未配置或上传失败时，对应文件上传失败并清理本地临时文件。
- 文件解析、OBS 下载和索引构建异常都会提取完整错误信息写入文档状态。
- 批量处理某个文档失败不会中断后续文档。
- `document_get_status_batch` 会格式化错误信息供前端展示。

## 测试与验证

- `PYTHONDONTWRITEBYTECODE=1 uv run python -m compileall -q server/local_retrieval server/routers/knowledge_base.py`
- 修改 schema 校验时，建议补充知识库 router 单测。
- 修改解析或分块策略时，补充覆盖 PDF、doc/docx、txt 和 md 上传处理路径。

## 相关文档

- [知识库管理](../knowledge-base.md)
- [知识库索引、检索与存储](./index-retrieval-storage.md)
