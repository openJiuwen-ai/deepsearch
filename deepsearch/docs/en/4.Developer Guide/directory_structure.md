# `openjiuwen_deepsearch` directory layout

This document reflects the current `deepsearch/openjiuwen_deepsearch` tree and what each major area does.

## Overview

```
openjiuwen_deepsearch/
├── algorithm/                      # Core algorithms
│   ├── prompts/                    # Prompt templates
│   ├── query_understanding/        # Query understanding (router/outliner/planner/interpreter)
│   ├── report/                     # Report generation
│   ├── report_template/            # Template parse/generate
│   ├── research_collector/         # Gathering and evaluation
│   ├── source_trace/               # Provenance and validation
│   ├── source_tracer_infer/        # Provenance reasoning
│   └── user_feedback_processor/    # Post-report local edits from user feedback
├── framework/                      # Framework integration
│   └── openjiuwen/
│       ├── agent/                  # Workflow and nodes
│       ├── core/                   # WorkflowAgent and controller
│       ├── tools/                  # Search tool wrappers
│       └── llm/                    # LLM factory
├── config/                         # Configuration
├── common/                         # Shared exceptions and status codes
├── utils/                          # Utilities
└── llm/                            # Unified LLM wrapper
```

---

## Details

### `algorithm/` — core algorithms

**Functions**: Core algorithm implementations for each stage of the research workflow.

**Main subdirectories**:

- **prompts/** - Prompt templates (`.md`)
  - `synonym_rewrite_expand.md` - Prompt for expansion
  - `synonym_rewrite_polish.md` - Prompt for polishing
  - `synonym_rewrite_shorten.md` - Prompt for shortening
  - `supplementary_search_task.md` - Prompt for supplementary-search task generation
  - `supplementary_search_rewrite_selected_only.md` - Prompt for supplementary search that rewrites only the selected span
  - `supplementary_search_rewrite_selected_and_related.md` - Prompt for supplementary search that rewrites the entire related section
  - `new_task_assessment.md` - Prompt for assessing sufficiency of historical evidence for new-task requests
  - `new_task_rewrite_section.md` - Prompt for rewriting sections based on new-task edit strategy
  - `truth_verification_assessment.md` - Prompt for content truth verification assessment
  - `truth_verification_search_task.md` - Prompt for truth-verification supplementary search task generation
- **query_understanding/** - Query understanding
  - `interpreter.py` - Generate clarification questions
  - `outliner.py` - Generate outlines
  - `planner.py` - Generate section plans
  - `router.py` - Decide whether to enter deep search
- **report/** - Report generation
  - `report.py` - Main report generation logic
  - `report_utils.py` - Report utility functions
  - `config.py` - Report style and formatting
- **report_template/** - Template generation and parsing
  - `template_generator.py`
  - `template_utils.py`
- **research_collector/** - Information collection and evaluation
  - `collector_function.py`
  - `doc_evaluation.py`
  - `tool_log.py`
- **source_trace/** - Provenance module
  - `source_tracer.py`
  - `checker.py`
  - `add_source.py`
  - `citation_checker_research.py`
  - `citation_verify_research.py`
  - `content_analyzer.py`
  - `source_matcher.py`
  - `source_tracer_preprocessors.py`
- **source_tracer_infer/** - Provenance reasoning module
  - `generate_html.py`
  - `html_template.py`
  - `infer.py`
  - `infer_call_model.py`
  - `infer_extract_info.py`
  - `number_node.py`
  - `supplement_graph.py`
- **user_feedback_processor/** - User-feedback local editing module
  - `action_definitions.py` - Mapping between frontend actions and unified internal actions
  - `common.py` - Shared utilities (session/model context resolution, LLM invocation entry)
  - `history.py` - Rewrite history and outline update management
  - `new_task_processor.py` - New-task action processing logic
  - `report_edit_utils.py` - Tools for stripping citation / inference markers and updating offsets
  - `section_locator.py` - Locate the smallest Markdown heading block for a selection
  - `supplementary_search.py` - Execution logic for supplementary search and local / whole-section rewriting
  - `synonym_rewrite.py` - Execution logic for expansion, polishing, and shortening
  - `truth_verification.py` - Content truth verification
  - `user_feedback_processor.py` - Parse feedback, validate, execute, and send results

---

### `framework/` — orchestration

**Functions**: Workflow and node orchestration based on openjiuwen.

**Main subdirectories**:

- **openjiuwen/agent/** - Workflows and nodes
  - `workflow.py` - Agent and workflow entry
  - `main_graph_nodes.py` - Main graph nodes (Start/Entry/Outline/Reporter/SourceTracer, etc.)
  - `editor_team_manager_node.py` - Editor-team subgraph manager
  - `reasoning_writing_graph/` - Editor-team subgraph nodes and state
    - `editor_team_nodes.py`
    - `dependency_reasoning_team_nodes.py`
    - `dependency_writing_team_nodes.py`
    - `section_context.py`
  - `collector_graph/` - Information-collection subgraph
    - `collector_execution_service.py` - Reusable information-collection execution service
    - `graph_builder.py`
    - `info_collector.py`
    - `collector_context.py`
  - `agent_factory.py` - Agent factory
  - `base_node.py` - Base class for nodes
  - `search_context.py` - Search context model

- **`openjiuwen/core/workflow_agent/`** — WorkflowAgent & controller  
  - `config.py`, `workflow_controller.py`, `workflow_agent.py`  

- **`openjiuwen/tools/`** — search tools  
  - `web_search.py`, `local_search.py`, `search_api/` (`external_tool/`, `petal/`, `tavily/`, `serper/`, `xunfei/`, `local_search_api/`, `native_local_search_api/`)  

- **`openjiuwen/llm/`** — LLM factory  
  - `llm_model_factory.py`, `llm_adapter.py`  

---

### `config/`

- `config.py` — `LLMConfig`, `AgentConfig`, `ServiceConfig`, etc.  
- `method.py` — execution mode enum  
- `search_mode.py` — search mode enum  

---

### `common/`

- `common_constants.py`  
- `exception.py`  
- `status_code.py`  

---

### `utils/`

- `common_utils/` — `llm_utils.py`, `security_utils.py`, `stream_utils.py`, `text_utils.py`, `url_utils.py`  
- `constants_utils/` — `node_constants.py`, `session_contextvars.py`, `search_engine_constants.py`  
- `debug_utils/` — `node_debug.py`, `outline_visualization.py`, `result_exporter.py`  
- `log_utils/` — logging helpers  
- `validation_utils/` — `field_validation.py`, `param_validation.py`  
- `rate_limiter_utils/` — `qps_limiter.py`  

---

### `llm/`

- `llm_wrapper.py` — unified LLM calls  
- `llm_request_adapter.py` — LLM request parameter adaptation, including provider rules for the model thinking-mode switch

---

## Module relationships

```
User request
    ↓
framework/openjiuwen/agent/workflow.py
    ├── validate & merge agent_config
    ├── init LLM & search tools
    └── Runner.run_agent_streaming(...)
            ↓
framework/openjiuwen/agent/main_graph_nodes.py
    ├── StartNode
    ├── EntryNode → algorithm/query_understanding/router.py
    ├── [GenerateQuestionsNode -> FeedbackHandlerNode] (optional HITL)
    ├── OutlineNode / DependencyOutlineNode → algorithm/query_understanding/outliner.py
    ├── OutlineInteractionNode / DependencyOutlineInteractionNode (optional)
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

## Where to look

- **Workflow** → `framework/openjiuwen/agent/`  
- **Algorithms** → `algorithm/`  
- **Config** → `config/config.py`  
- **Web search backends** → `framework/openjiuwen/tools/search_api/`  
- **Prompts** → `algorithm/prompts/`  
- **Context model** → `framework/openjiuwen/agent/search_context.py`  

---

## Design principles

1. **Layering**: `algorithm/` = logic; `framework/` = orchestration.  
2. **Modularity**: nodes stay decoupled from algorithm details.  
3. **Configuration**: `config/` is the single place for tunables.  
4. **Reuse**: `utils/` holds shared infrastructure.  
