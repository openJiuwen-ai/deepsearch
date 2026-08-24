# CodeSearch 检索智能体

## 维护范围

检索智能体的算法层：工具集、过滤智能体、片段记忆、提示词组织
（CodeSearch `react`/`graph` 五工具路径）。
编排与图结构见 [codesearch-workflow.md](../framework/codesearch-workflow.md)；
Retropus 工具与配置见 [retropus-agent.md](../framework/retropus-agent.md)。

## 双模型架构

| 角色 | 默认模型 | 环境变量 | 职责 |
|---|---|---|---|
| 决策模型（main） | `openai/gpt-5` | `CODESEARCH_LLM_MODEL` | 多轮工具调用：搜什么、看哪里、何时提交 |
| 过滤模型（filter） | `openai/gpt-5-mini` | `CODESEARCH_FILTER_LLM_MODEL` | 对每个检索到的 chunk 逐行提取与 issue 相关的行区间（`save_relevant_lines` 结构化输出，有界并发） |

密钥与端点：`CODESEARCH_LLM_API_KEY` / `CODESEARCH_LLM_BASE_URL`（见产品文档安装指导）。

用量结构：决策模型调用次数少但单次输入大（累积记忆随轮次增长），
过滤模型调用次数多而单次输入小（每次只看一个 chunk）。
小仓实测一次检索：决策模型 6 次调用 input 14036 / output 1593，
过滤模型 13 次调用 input 4250 / output 4079——决策侧主导输入，
过滤侧主导输出。绝对值随仓库规模变化，此处仅示结构。
结果只报告 token（`total_input_tokens` / `total_output_tokens`），
不报告金额：单价随端点与时间变动，由使用方按自己的计费口径折算。

## 工具集（algorithm/search_tools/，registry 分发）

| 工具 | 行为 |
|---|---|
| `view_repo_map` | 返回该 revision 的全部文件路径列表 |
| `search_codebase` | 稀疏检索；`use_trigram=False` 词元 BM25 / `True` 字符三元组 BM25（精确子串）；`target_file` 经文本头前缀提权 |
| `expand_context` | 按文件+行区间直接取索引内容（区间按 chunk 边界裁剪后入记忆） |
| `delete_snippets` | 从记忆删除误检片段（要求给出理由） |
| `submit_final_snippets` | 提交最终片段 ID，结束检索 |

## 片段记忆（domain/memory.py）

- 检索命中经过滤模型提取后，以"chunk → 行区间列表"入记忆，重叠/相邻区间自动合并；
- 每轮将记忆渲染为 `CURRENT SAVED SNIPPETS` 文本**重写进首条消息**（非追加），
  智能体始终可见当前收集状态；
- 最终结果：每个不相交区间独立成条，按 `(file_path, start_line)` 排序（行为契约）。

## 提示词

`algorithm/prompts/*.md` 文件化（`code_search.md` 主提示词含 `{topk}` 约束，
`filter_chunk.md` 过滤提示词；Retropus 见 `retropus_*.md` + `retropus.py`），
类级缓存加载。

## 提前终止

- **停滞**：连续 `stagnation_rounds`（默认 3）个含检索的轮次零新增 → 提前结束，
  降级返回记忆现存内容（防空烧轮次）；
- **fail-fast**：索引未就绪直接返回，不消耗任何 LLM 调用；
- 临近 `max_turns` 时注入"必须提交"系统警告（最后 `warn_before_turns` 轮）。
