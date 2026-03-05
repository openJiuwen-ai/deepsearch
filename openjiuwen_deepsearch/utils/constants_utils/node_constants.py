# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum


class NodeId(enum.Enum):
    START = "start"
    END = "end"
    FRAMEWORK = "framework"

    # research 相关
    ENTRY = "entry"
    GENERATE_QUESTIONS = "generate_questions"
    FEEDBACK_HANDLER = "feedback_handler"
    OUTLINE = "outline"
    EDITOR_TEAM = "editor_team"
    REPORTER = "reporter"
    EVALUATOR = "evaluator"
    HUMAN_EVALUATOR = "human_evaluator"
    SOURCE_TRACER = "source_tracer"
    PASS_SOURCE_TRACER_RESULT = "pass_source_tracer_result"
    DEPENDENCY_REASONING_TEAM = "dependency_reasoning_team"
    DEPENDENCY_WRITING_TEAM = "dependency_writing_team"

    # 子图
    INFO_COLLECTOR = "info_collector"
    PLAN_REASONING = "plan_reasoning"
    SUB_EVALUATOR = "sub_evaluator"
    SUB_SOURCE_TRACER = "sub_source_tracer"
    SUB_REPORTER = "sub_reporter"
    SEARCH_INFO_COLLECTOR = "search_info_collector"
    SEARCH_PLAN_REASONING = "search_plan_reasoning"

    # search 相关
    SEARCH_TEAM = "search_team"
    ANSWER = "answer"

    # info collector 子图相关
    COLLECTOR_QUERY_GEN = "collector_query_generation"
    COLLECTOR_INFO = "collector_info_retrieval"
    COLLECTOR_SUPERVISOR = "collector_supervisor"
    COLLECTOR_SUMMARY = "collector_summary"
    COLLECTOR_PROGRAMMER = "collector_programmer"
    COLLECTOR_END = "collector_end"
    DOC_EVALUATOR = "doc_evaluator"
    INFO_EVALUATOR = "info_evaluator"
    INFO_ORAGNIZER = "info_organizer"

    # 模板流程
    TEMPLATE = "template"


NODE_DEBUG_LOGGER = "node_debug_logger"
