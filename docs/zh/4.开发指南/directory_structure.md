# jiuwen_deepsearch 目录结构说明

本文档基于当前 `deepsearch/jiuwen_deepsearch` 的最新代码，说明目录结构与主要模块职责。

## 目录结构概览

```
jiuwen_deepsearch/
├── algorithm/                      # 核心算法模块
│   ├── prompts/                    # 提示词模板
│   ├── query_understanding/        # 查询理解（router/outliner/planner/interpreter）
│   ├── report/                     # 报告生成
│   ├── report_template/            # 报告模板解析与生成
│   ├── research_collector/         # 信息收集与评估
│   ├── source_trace/               # 溯源与校验
│   └── source_tracer_infer/         # 溯源推理
├── framework/                      # 框架层实现
│   └── jiuwen/
│       ├── agent/                  # 工作流与节点
│       ├── tools/                  # 搜索工具封装
│       ├── config/                 # 工具配置
│       ├── llm/                    # LLM模型工厂
│       └── utils/                  # 框架工具函数
├── config/                         # 配置管理
├── common/                         # 公共异常与状态码
├── utils/                          # 通用工具函数
└── llm/                            # LLM统一封装
```

---

## 目录详细说明

### algorithm/ - 核心算法模块

**功能**：研究工作流中各阶段的核心算法实现。

**主要子目录**：

- **prompts/** - 提示词模板（`.md`）
- **query_understanding/** - 查询理解
  - `interpreter.py` - 生成澄清问题
  - `outliner.py` - 生成大纲
  - `planner.py` - 生成章节计划
  - `router.py` - 判断是否进入深度搜索
- **report/** - 报告生成
  - `report.py` - 报告生成主逻辑
  - `report_utils.py` - 报告工具函数
  - `config.py` - 报告样式与格式
- **report_template/** - 模板生成与解析
  - `template_generator.py`
  - `template_utils.py`
- **research_collector/** - 信息收集与评估
  - `collector_function.py`
  - `doc_evaluation.py`
  - `tool_log.py`
- **source_trace/** - 溯源模块
  - `source_tracer.py`
  - `checker.py`
  - `add_source.py`
  - `citation_checker_research.py`
  - `citation_verify_research.py`
  - `content_analyzer.py`
  - `source_matcher.py`
  - `source_tracer_preprocessors.py`
- **source_tracer_infer/** - 溯源推理模块
  - `infer.py`
  - `infer_call_model.py`
  - `infer_extract_info.py`
  - `number_node.py`
  - `supplement_graph.py`
  - `html_template.py`

---

### framework/ - 框架层实现

**功能**：基于 openjiuwen 的工作流与节点编排。

**主要子目录**：

- **jiuwen/agent/** - 工作流与节点
  - `workflow.py` - Agent与工作流入口
  - `main_graph_nodes.py` - 主图节点（Start/Entry/Outline/Reporter/SourceTracer等）
  - `editor_team_manager_node.py` - 编辑团队子图管理
  - `reasoning_writing_graph/` - 编辑团队子图节点与状态
    - `editor_team_nodes.py`
    - `section_state.py`
  - `collector_graph/` - 信息收集子图
    - `graph_builder.py`
    - `info_collector.py`
    - `collector_state.py`
  - `agent_factory.py` - Agent工厂
  - `base_node.py` - 节点基类
  - `search_context.py` - 搜索上下文数据模型

- **jiuwen/tools/** - 搜索工具封装
  - `web_search.py`
  - `local_search.py`
  - `Search_API/` - 搜索引擎封装
    - `external_tool/`
    - `petal/`
    - `tavily/`
    - `serper/`
    - `xunfei/`
    - `local_search_api/`
    - `native_local_search_api/`

- **jiuwen/config/** - 工具配置
  - `tools.py`

- **jiuwen/utils/** - 框架工具函数
  - `common_utils.py`
  - `debug_logger.py`

- **jiuwen/llm/** - LLM模型工厂
  - `llm_model_factory.py`

---

### config/ - 配置管理

**主要文件**：
- `config.py` - 配置类（LLMConfig、AgentConfig、ServiceConfig等）
- `method.py` - 执行方式枚举
- `search_mode.py` - 搜索模式枚举

---

### common/ - 公共模块

**主要文件**：
- `exception.py` - 自定义异常类
- `status_code.py` - 状态码定义

---

### utils/ - 通用工具函数

**主要文件**：
- `common_constants.py`
- `llm_utils.py`
- `log_handlers.py`
- `log_interface.py`
- `log_manager.py`
- `log_time.py`
- `log_utils.py`
- `node_constants.py`
- `stream_utils.py`
- `text_utils.py`
- `url_utils.py`
- `runtime_contextvars.py`

---

### llm/ - LLM封装

**主要文件**：
- `llm_wrapper.py` - LLM调用统一封装

---

## 模块关系

```
用户请求
    ↓
framework/jiuwen/agent/workflow.py
    ↓
framework/jiuwen/agent/main_graph_nodes.py
    ├── StartNode 初始化上下文与配置
    ├── EntryNode → algorithm/query_understanding/router.py
    ├── [GenerateQuestionsNode -> FeedbackHandlerNode]（HITL可选）
    ├── OutlineNode → algorithm/query_understanding/outliner.py
    ├── EditorTeamNode → editor_team_manager_node.py
    │   ├── ResearchPlanReasoningNode → algorithm/query_understanding/planner.py
    │   ├── InfoCollectorNode → collector_graph/
    │   └── SubReporterNode → algorithm/report/report.py
    ├── ReporterNode → algorithm/report/report.py
    ├── SourceTracerNode → algorithm/source_trace/
    └── SourceTracerInferNode → algorithm/source_tracer_infer/
```

---

## 快速定位指南

- **想了解工作流** → `framework/jiuwen/agent/`
- **想了解算法** → `algorithm/`
- **想修改配置** → `config/config.py`
- **想接入搜索引擎** → `framework/jiuwen/tools/Search_API/`
- **想修改提示词** → `algorithm/prompts/`
- **想了解上下文模型** → `framework/jiuwen/agent/search_context.py`

---

## 设计原则

1. **分层设计**：`algorithm/` 负责算法逻辑，`framework/` 负责工作流编排
2. **模块化**：节点与算法解耦，便于维护与扩展
3. **可配置**：统一使用 `config/` 管理参数
4. **工具复用**：`utils/` 提供通用能力与基础设施
