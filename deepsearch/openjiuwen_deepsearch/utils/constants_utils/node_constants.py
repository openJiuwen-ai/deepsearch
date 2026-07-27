# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum


class NodeId(enum.Enum):
    START = "start"
    END = "end"
    FRAMEWORK = "framework"

    # research 相关
    INTENT_RECOGNITION = "intent_recognition"
    GENERATE_QUESTIONS = "generate_questions"
    FEEDBACK_HANDLER = "feedback_handler"
    OUTLINE = "outline"
    OUTLINE_INTERACTION = "outline_interaction"
    EDITOR_TEAM = "editor_team"
    REPORTER = "reporter"
    EVALUATOR = "evaluator"
    HUMAN_EVALUATOR = "human_evaluator"
    SOURCE_TRACER = "source_tracer"
    SOURCE_TRACER_INFER = "source_tracer_infer"
    USER_FEEDBACK_PROCESSOR = "user_feedback_processor"
    DEPENDENCY_EDITOR_TEAM = "dependency_editor_team"
    VLM_CHART_GENERATOR = "vlm_chart_generator"

    # 子图
    INFO_COLLECTOR = "info_collector"
    PLAN_REASONING = "plan_reasoning"
    SUB_EVALUATOR = "sub_evaluator"
    SUB_SOURCE_TRACER = "sub_source_tracer"
    SUB_REPORTER = "sub_reporter"
    SEARCH_INFO_COLLECTOR = "search_info_collector"
    SEARCH_PLAN_REASONING = "search_plan_reasoning"

    # search 相关
    END_NODE = "end_node"
    FIND_ACTION_SPACE = "find_action_space"
    INITIAL_STATE = "initial_state"
    RUN_ACTION = "run_action"
    START_NODE = "start_node"
    TOOL = "tool"
    VALIDATE_NEW_STATE = "validate_new_state"
    SIMPLE_REACT_SEARCH = "simple_react_search"

    # info collector 子图相关
    COLLECTOR_QUERY_GEN = "collector_query_generation"
    COLLECTOR_INFO = "collector_info_retrieval"
    COLLECTOR_WEBPAGE_ENRICHMENT = "collector_webpage_enrichment"
    COLLECTOR_SUPERVISOR = "collector_supervisor"
    COLLECTOR_SUMMARY = "collector_summary"
    COLLECTOR_PROGRAMMER = "collector_programmer"
    COLLECTOR_END = "collector_end"
    DOC_EVALUATOR = "doc_evaluator"
    INFO_EVALUATOR = "info_evaluator"
    INFO_ORAGNIZER = "info_organizer"

    # 模板流程
    TEMPLATE = "template"


class AgentLlmName(enum.Enum):
    """可用于 agent_llm_timeouts 的 LLM 调用点名称清单。

    该枚举只用于集中维护 ``ainvoke_llm_with_stats`` 的 ``agent_name`` 取值，
    方便配置文档和测试从同一个事实源获取可配置名称。
    """

    QUESTION_MODEL_ROUTER = "question_model_router"
    OUTLINE_MODE_ROUTER = "outline_mode_router"
    TEMPLATE = NodeId.TEMPLATE.value

    COLLECTOR_QUERY_GENERATION = NodeId.COLLECTOR_QUERY_GEN.value
    COLLECTOR_INFO_RETRIEVAL = NodeId.COLLECTOR_INFO.value
    COLLECTOR_WEBPAGE_ENRICHMENT_SELECTION = "collector_webpage_enrichment_selection"
    COLLECTOR_WEBPAGE_ENRICHMENT_COMPRESSION = "collector_webpage_enrichment_compression"
    COLLECTOR_ARTICLE_LINK_FOLLOW_COMPRESSION = "collector_article_link_follow_compression"
    COLLECTOR_SUPERVISOR = NodeId.COLLECTOR_SUPERVISOR.value
    COLLECTOR_SUMMARY = NodeId.COLLECTOR_SUMMARY.value

    INTENT_RECOGNITION = NodeId.INTENT_RECOGNITION.value
    GENERATE_QUESTIONS = NodeId.GENERATE_QUESTIONS.value
    OUTLINE = NodeId.OUTLINE.value
    PLAN_REASONING = NodeId.PLAN_REASONING.value
    DOC_EVALUATOR = NodeId.DOC_EVALUATOR.value

    REPORTER_ABSTRACT = "reporter_abstract"
    REPORTER_CONCLUSION = "reporter_conclusion"
    REPORTER_TRANSACTION = "reporter_transaction"

    SUB_REPORTER = NodeId.SUB_REPORTER.value
    SUB_REPORTER_RATIONALE_GENERATOR = "sub_reporter_rationale_generator"
    SUB_REPORTER_COVERAGE_MATRIX_EVALUATOR = "sub_reporter_coverage_matrix_evaluator"
    SUB_REPORTER_OUTLINE = "sub_reporter_outline"
    SUB_REPORTER_VISUALIZATION_CONTENT = "sub_reporter_visualization_content"
    SUB_REPORTER_CHART_COMPLIANCE = "sub_reporter_chart_compliance"
    SUB_REPORTER_CHART_TRACEABILITY = "sub_reporter_chart_traceability"
    SUB_REPORTER_VISUALIZATION_NORMALIZE = "sub_reporter_visualization_normalize"
    SUB_REPORTER_SUMMARY = "sub_reporter_summary"
    SUB_REPORTER_SIDECAR = "sub_reporter_sidecar"

    SOURCE_TRACER_CONTENT_RECOGNITION = "source_tracer_content_recognition"
    SOURCE_TRACER_SOURCE_MATCHING = "source_tracer_source_matching"
    SOURCE_TRACER_EXTRACT_MESSAGES = "source_tracer_extract_messages"
    SOURCE_TRACER_INFER = NodeId.SOURCE_TRACER_INFER.value
    SOURCE_TRACER_INFER_EXTRACT_CONCLUSION = "source_tracer_infer_extract_conclusion"
    SOURCE_TRACER_INFER_EXTRACT_REFERENCE = "source_tracer_infer_extract_reference"
    SOURCE_TRACER_INFER_INFER = "source_tracer_infer_infer"
    SOURCE_TRACER_INFER_FILTER_INVALID_INFER = "source_tracer_infer_filter_invalid_infer"
    SOURCE_TRACER_INFER_STRUCTURED_INFER = "source_tracer_infer_structured_infer"
    SOURCE_TRACER_INFER_SUPPLEMENT_GRAPH = "source_tracer_infer_supplement_graph"

    USER_FEEDBACK_PROCESSOR_SYNONYM_REWRITER = "user_feedback_processor_synonym_rewriter"
    USER_FEEDBACK_PROCESSOR_SUPPLEMENTARY_SEARCH_TASK = "user_feedback_processor_supplementary_search_task"
    USER_FEEDBACK_PROCESSOR_SUPPLEMENTARY_SEARCH_REWRITE_SELECTED_ONLY = (
        "user_feedback_processor_supplementary_search_rewrite_selected_only"
    )
    USER_FEEDBACK_PROCESSOR_SUPPLEMENTARY_SEARCH_REWRITE_SELECTED_AND_RELATED = (
        "user_feedback_processor_supplementary_search_rewrite_selected_and_related"
    )
    USER_FEEDBACK_PROCESSOR_NEW_TASK_ASSESSMENT = "user_feedback_processor_new_task_assessment"
    USER_FEEDBACK_PROCESSOR_NEW_TASK_REWRITE_SECTION = "user_feedback_processor_new_task_rewrite_section"
    USER_FEEDBACK_PROCESSOR_TRUTH_VERIFICATION_ASSESSMENT = (
        "user_feedback_processor_truth_verification_assessment"
    )
    USER_FEEDBACK_PROCESSOR_TRUTH_VERIFICATION_SEARCH_TASK = (
        "user_feedback_processor_truth_verification_search_task"
    )

    VLM_CHART_GENERATOR = NodeId.VLM_CHART_GENERATOR.value
    VLM_CHART_GENERATOR_FIND_INSERT_POINT = "vlm_chart_generator_find_inserPoint"
    VLM_CHART_GENERATOR_GENERATE_CHART_CODE = "vlm_chart_generator_generate_chart_code"
    VLM_CHART_GENERATOR_VLM_ITERATE = "vlm_chart_generator_vlm_iterate"
    VLM_CHART_GENERATOR_COLLECT_DATA_FOR_CHART = "vlm_chart_generator_collect_data_for_chart"

    FIND_ACTION_SPACE = NodeId.FIND_ACTION_SPACE.value
    INITIAL_STATE = NodeId.INITIAL_STATE.value
    RUN_ACTION = NodeId.RUN_ACTION.value
    TOOL = NodeId.TOOL.value
    VALIDATE_NEW_STATE = NodeId.VALIDATE_NEW_STATE.value
    SIMPLE_REACT_SEARCH = NodeId.SIMPLE_REACT_SEARCH.value


NODE_DEBUG_LOGGER = "node_debug_logger"
