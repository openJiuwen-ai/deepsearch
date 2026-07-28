# CodeSearch 检索智能体

## 维护范围

检索智能体的算法层：工具集、过滤智能体、片段记忆、提示词组织。
编排与图结构见 [codesearch-workflow.md](../framework/codesearch-workflow.md)。

## 双模型架构

| 角色 | 默认模型 | 职责 |
|---|---|---|
| 决策模型 | openai/gpt-5 | 多轮工具调用：搜什么、看哪里、何时提交 |
| 过滤模型 | openai/gpt-5-mini | 对每个检索到的 chunk 逐行提取与 issue 相关的行区间（`save_relevant_lines` 结构化输出，有界并发） |

成本结构（32 题实测）：决策模型占 ~88%（长上下文），过滤模型 ~48 次/题仅 ~$0.04/题。

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
`filter_chunk.md` 过滤提示词），类级缓存加载。

## 提前终止

- **停滞**：连续 `stagnation_rounds`（默认 3）个含检索的轮次零新增 → 提前结束，
  降级返回记忆现存内容（防空烧轮次）；
- **fail-fast**：索引未就绪直接返回，不消耗任何 LLM 调用；
- 临近 `max_turns` 时注入"必须提交"系统警告（最后 `warn_before_turns` 轮）。
