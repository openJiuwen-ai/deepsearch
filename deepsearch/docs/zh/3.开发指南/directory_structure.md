# `openjiuwen_deepsearch` 目录结构

本文档描述 `deepsearch/openjiuwen_deepsearch/` 的当前稳定模块边界和关键入口；按职责列举目录，不列举缓存与易变的内部实现文件。

## 概览

```text
openjiuwen_deepsearch/
├── algorithm/                  # 研究、检索、写作和溯源算法
├── common/                     # 异常、状态码和公共定义
├── config/                     # Pydantic 配置与运行时 API 工具模型
├── framework/openjiuwen/       # 工作流编排、节点、工具和 LLM 适配
├── llm/                        # 统一 LLM 调用封装
└── utils/                      # 安全、日志、限流、校验和常量工具
```

## `algorithm/`：领域算法

```text
algorithm/
├── brief_report/               # Brief 报告、素材合并与 HTML 输出
├── chart_generation/           # 图表生成和沙箱资源
├── paper_report/、paper_research/ # 论文报告与研究辅助
├── prompts/                    # 提示词模板
├── query_understanding/        # 意图、素材、大纲、计划与澄清
├── report/                     # 子报告/总报告、证据和可视化
├── report_export/、report_style/、report_template/
├── research_collector/         # 收集、证据融合和网页正文增强
├── search_agent/、search_index/、search_nodes/、search_tools/
├── source_trace/、source_tracer_infer/
└── user_feedback_processor/    # 报告后局部编辑与补充检索
```

`query_understanding/` 的关键入口为 `intent_recognition.py`、`material_processing.py`、`outline_mode_router.py`、`interpreter.py`、`outliner.py` 和 `planner.py`。用户素材的约束和章节绑定由此产生，供收集和写作阶段消费。

## `framework/openjiuwen/`：运行时编排

```text
framework/openjiuwen/
├── agent/
│   ├── workflow.py             # 流式 Agent 入口与工作流组装
│   ├── main_graph_nodes.py     # 主图节点
│   ├── brief_nodes.py          # Brief 专用节点
│   ├── metadata_injectors.py   # 请求 metadata 注入器
│   ├── search_context.py       # 工作流状态模型
│   ├── collector_graph/        # 收集子图、证据账本、网页增强
│   └── reasoning_writing_graph/# 章节推理/写作子图
├── core/workflow_agent/        # WorkflowAgent 与控制器适配
├── llm/                        # 工作流 LLM 工厂与适配器
└── tools/
    ├── fetch_api/              # 网页抓取 provider（含 jina）
    ├── runtime_api/            # 运行时 HTTP 工具构建与调用
    └── search_api/             # 联网、本地和学术搜索 provider
```

`search_api/` 包括 `agc_ainetworking`、`harness_web_search`、`jina`、`petal`、`serper`、`tavily`、`xunfei` 和 `scholarly_search/`（PubMed、arXiv、Semantic Scholar、全文获取）；外部及本地适配位于 `external_tool/`、`local_search_api/`、`native_local_search_api/`。

## 配置与工具

`config/config.py` 定义 `AgentConfig`、`ServiceConfig` 和 provider 配置；`runtime_api_models.py` 定义运行时 HTTP 工具模型。`utils/` 包含 `common_utils/`、`constants_utils/`、`debug_utils/`、`log_utils/`、`rate_limiter_utils/`、`validation_utils/`。

## 主调用链与开发入口

```text
workflow.py → main_graph_nodes.py / brief_nodes.py
→ query_understanding → collector_graph + research_collector
→ reasoning_writing_graph + report → source_trace / source_tracer_infer
→ 流式输出或 HITL 等待事件
```

- 工作流节点：`framework/openjiuwen/agent/`。
- 报告算法和提示词：`algorithm/report/`、`algorithm/prompts/`。
- 搜索/抓取 provider：`framework/openjiuwen/tools/search_api/`、`fetch_api/`，并同步 `config/config.py`。
- 状态契约：`framework/openjiuwen/agent/search_context.py`，并同步 API 参考。
