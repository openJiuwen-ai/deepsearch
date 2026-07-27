# openjiuwen_deepsearch 目录结构说明

本文档基于当前 `deepsearch/openjiuwen_deepsearch` 的最新代码，说明目录结构与主要模块职责。

## 目录结构概览

```
openjiuwen_deepsearch/
├── algorithm/                      # 核心算法模块
│   ├── prompts/                    # 提示词模板
│   ├── query_understanding/        # 查询理解（router/outliner/planner/interpreter）
│   ├── report/                     # 报告生成
│   ├── report_template/            # 报告模板解析与生成
│   ├── research_collector/         # 信息收集与评估
│   ├── source_trace/               # 溯源与校验
│   ├── source_tracer_infer/        # 溯源推理
│   └── user_feedback_processor/    # 报告生成后的用户反馈局部优化
├── framework/                      # 框架层实现
│   └── openjiuwen/
│       ├── agent/                  # 工作流与节点
│       ├── core/                   # WorkflowAgent与控制器
│       ├── tools/                  # 搜索工具封装
│       └── llm/                    # LLM模型工厂
├── config/                         # 配置管理
├── common/                         # 公共异常与状态码
├── utils/                          # 工具函数
└── llm/                            # LLM统一封装
```

---

## 目录详细说明

### algorithm/ - 核心算法模块

**功能**：研究工作流中各阶段的核心算法实现。

**主要子目录**：

- **prompts/** - 提示词模板（`.md`）
  - `synonym_rewrite_expand.md` - 扩写提示词
  - `synonym_rewrite_polish.md` - 润色提示词
  - `synonym_rewrite_shorten.md` - 缩写提示词
  - `supplementary_search_task.md` - 补充检索任务生成提示词
  - `supplementary_search_rewrite_selected_only.md` - 仅改写选区的补充检索提示词
  - `supplementary_search_rewrite_selected_and_related.md` - 整章联动改写的补充检索提示词
  - `new_task_assessment.md` - 新增任务资料充分性评估提示词
  - `new_task_rewrite_section.md` - 新增任务章节改写提示词
  - `truth_verification_assessment.md` - 内容真实性核验评估提示词
  - `truth_verification_search_task.md` - 内容真实性核验补充检索任务提示词
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
  - `generate_html.py`
  - `html_template.py`
  - `infer.py`
  - `infer_call_model.py`
  - `infer_extract_info.py`
  - `number_node.py`
  - `supplement_graph.py`
- **user_feedback_processor/** - 用户反馈局部优化模块
  - `action_definitions.py` - 前端 action 与统一动作定义映射
  - `common.py` - 公共工具函数（会话/模型上下文获取、LLM 调用入口）
  - `history.py` - 改写历史与大纲更新管理
  - `new_task_processor.py` - 新增任务动作处理逻辑
  - `report_edit_utils.py` - citation / inference 标记剥离与偏移更新工具
  - `section_locator.py` - 根据选区定位最小 Markdown 标题区块
  - `supplementary_search.py` - 补充检索与局部/整章改写执行逻辑
  - `synonym_rewrite.py` - 扩写、润色、缩写执行逻辑
  - `truth_verification.py` - 内容真实性核验
  - `user_feedback_processor.py` - 反馈解析、校验、执行与结果发送

---

### framework/ - 框架层实现

**功能**：基于 openjiuwen 的工作流与节点编排。

**主要子目录**：

- **openjiuwen/agent/** - 工作流与节点
  - `workflow.py` - Agent与工作流入口
  - `main_graph_nodes.py` - 主图节点（Start/Entry/Outline/Reporter/SourceTracer等）
  - `editor_team_manager_node.py` - 编辑团队子图管理
  - `reasoning_writing_graph/` - 编辑团队子图节点与状态
    - `editor_team_nodes.py`
    - `dependency_reasoning_team_nodes.py`
    - `dependency_writing_team_nodes.py`
    - `section_context.py`
  - `collector_graph/` - 信息收集子图
    - `collector_execution_service.py` - 复用型信息采集执行服务
    - `graph_builder.py`
    - `info_collector.py`
    - `collector_context.py`
  - `agent_factory.py` - Agent工厂
  - `base_node.py` - 节点基类
  - `search_context.py` - 搜索上下文数据模型

- **openjiuwen/core/workflow_agent/** - 工作流Agent与控制器
  - `config.py`
  - `workflow_controller.py`
  - `workflow_agent.py`

- **openjiuwen/tools/** - 搜索工具封装
  - `web_search.py`
  - `local_search.py`
  - `search_api/` - 联网增强引擎封装
    - `external_tool/`
    - `petal/`
    - `tavily/`
    - `serper/`
    - `xunfei/`
    - `local_search_api/`
    - `native_local_search_api/`

- **openjiuwen/llm/** - LLM模型工厂
  - `llm_model_factory.py`
  - `llm_adapter.py`

---

### config/ - 配置管理

**主要文件**：
- `config.py` - 配置类（LLMConfig、AgentConfig、ServiceConfig等）
- `method.py` - 执行方式枚举
- `search_mode.py` - 搜索模式枚举

---

### common/ - 公共模块

**主要文件**：
- `common_constants.py` - 公共常量定义
- `exception.py` - 自定义异常类
- `status_code.py` - 状态码定义

---

### utils/ - 工具函数

**主要文件**：

- `common_utils/` - 通用工具函数
  - `llm_utils.py`
  - `security_utils.py`
  - `stream_utils.py`
  - `text_utils.py`
  - `url_utils.py`
- `constants_utils/` - 常量工具函数
  - `node_constants.py`
  - `session_contextvars.py`
  - `search_engine_constants.py`
- `debug_utils/` - 调试工具函数
  - `node_debug.py`
  - `outline_visualization.py`
  - `result_exporter.py`
- `log_utils/` - 日志工具函数
  - `log_common.py`
  - `log_handlers.py`
  - `log_interface.py`
  - `log_manager.py`
  - `log_metrics.py`
- `validation_utils/` - 校验工具函数
  - `field_validation.py`
  - `param_validation.py`
- `rate_limiter_utils/` - QPS 限流工具
  - `qps_limiter.py`

---

### llm/ - LLM封装

**主要文件**：
- `llm_wrapper.py` - LLM调用统一封装
- `llm_request_adapter.py` - LLM 请求参数适配，包括大模型思考模式开关的厂商规则

---

## 模块关系

```
用户请求
    ↓
framework/openjiuwen/agent/workflow.py
    ├── 组装并校验 agent_config
    ├── 初始化 LLM 与搜索工具
    └── Runner.run_agent_streaming(...)
            ↓
framework/openjiuwen/agent/main_graph_nodes.py
    ├── StartNode （初始化上下文与配置）
    ├── EntryNode → algorithm/query_understanding/router.py
    ├── [GenerateQuestionsNode -> FeedbackHandlerNode]（HITL可选）
    ├── OutlineNode / DependencyOutlineNode → algorithm/query_understanding/outliner.py
    ├── OutlineInteractionNode / DependencyOutlineInteractionNode（大纲交互可选）
    ├── EditorTeamNode / DependencyReasoningTeamNode / DependencyWritingTeamNode
    │   ├── ResearchPlanReasoningNode → algorithm/query_understanding/planner.py
    │   ├── InfoCollectorNode → collector_graph/
    │   └── SubReporterNode → algorithm/report/report.py
    ├── ReporterNode → algorithm/report/report.py
    ├── SourceTracerNode → algorithm/source_trace/
    ├── SourceTracerInferNode → algorithm/source_tracer_infer/
    └── UserFeedbackProcessorNode → algorithm/user_feedback_processor/
```

---

## 快速定位指南

- **想了解工作流** → `framework/openjiuwen/agent/`
- **想了解算法** → `algorithm/`
- **想修改配置** → `config/config.py`
- **想接入联网增强引擎** → `framework/openjiuwen/tools/search_api/`
- **想修改提示词** → `algorithm/prompts/`
- **想了解上下文模型** → `framework/openjiuwen/agent/search_context.py`

---

## 设计原则

1. **分层设计**：`algorithm/` 负责算法逻辑，`framework/` 负责工作流编排
2. **模块化**：节点与算法解耦，便于维护与扩展
3. **可配置**：统一使用 `config/` 管理参数
4. **工具复用**：`utils/` 提供通用能力与基础设施
