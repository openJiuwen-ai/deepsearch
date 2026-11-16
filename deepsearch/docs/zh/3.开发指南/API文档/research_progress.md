# SDK 报告进度查询

```python
snapshot = agent.get_progress(conversation_id)
```

适用于 `search_mode=research` 下的 Brief 和专业版报告。
使用正在执行该报告的 Agent 实例查询，可从其他协程或线程调用；新建另一个 Agent 不能查询已有实例的任务。
调用方开始读取 `agent.run(...)` 的输出后，才登记进度。

| 字段 | 含义 |
| --- | --- |
| `conversation_id` | 原报告会话标识 |
| `status` | `running` 执行中、`waiting_user_input` 等待用户输入、`cancelling` 取消收尾中 |
| `node_id` | 最近进入的主图节点名称，初始化期间可能为 `None` |
| `updated_at` | 最近节点或状态更新的执行进程本地时间（ISO 8601 格式，带时区偏移量） |
| `collected_info_count` | 已有采集记录中的检索结果条目数，不保证跨查询或跨章节去重 |
| `total_sections` | 当前大纲章节总数，尚未确定大纲时为 `None`（HTTP 为 `null`） |
| `completed_sections` | 正常结束并产出非空正文的章节数，失败退出或空结果不计入 |
| `processing_sections` | 最多五个处理中的章节，只含 `section_id` 和 `title`；SDK 为不可变元组，HTTP 为数组 |

返回值为不可变的 `ProgressSnapshot`。未知会话、不属于该 Agent 或已经结束时返回 `None`。
等待用户输入时仍可查询，继续执行后返回新一轮状态。
节点名称直接复用工作流定义，章节内部节点不覆盖报告整体位置，不返回完成比例或预计时间。

专业版 `parallel` 使用 `editor_team`，`dependency_driving` 使用 `dependency_editor_team`，
`hybrid` 根据路由选择两者之一；Brief 使用 `brief_*` 专用节点。
完整的[各模式 node_id 清单](../../../feature/framework/report-progress.md#node_id)集中维护在特性文档中。
清单不是固定执行顺序，调用方应兼容未来新增节点。

查询只读取当前进度，不执行报告或改变任务状态。`updated_at` 不表示心跳或健康状态。

资料数复用既有记录：专业版按各章节、计划、步骤的查询结果条目数汇总；Brief 使用现有
`collection_context.search_results` 的数量，不计用户素材、引用登记数或全文读取数。
同一业务记录更新时替换原数量，不因重复通知或内部重试额外累加；不同查询或章节的相同资料可能重复计入。
交互继续时，从主图已有大纲、章节计划、报告和 Brief 状态恢复，支持换 worker；不保存第二套累计历史。
大纲版本变化时移除旧版本观察数据，章节进展以当前版本为准。
依赖模式研究结束不计为章节完成，需等待写作结果；Brief 按单章实际完成更新。
章节标识最多 128 字符、标题最多 160 字符，处理中章节只展示大纲顺序的前五个，不代表全部并发章节。
仅汇总变化不会修改 `updated_at`，该字段继续表示主图节点或状态变化时间。
