# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum


class StatusCode(Enum):
    """Status code enum"""

    # 公共类型错误（20 开头，子类型编码范围00-09）
    PARAM_CHECK_ERROR_MUST_GREATER_THAN_ZERO = (200000, "chunk_size must be > 0, got {chunk_size}")
    PARAM_CHECK_ERROR_OVERLAP_INVALID = (200001, "chunk_overlap must be >= 0, got {chunk_overlap}")
    PARAM_CHECK_ERROR_DOCUMENTS_MISMATCH_INDEX = (200002, "The documents given don't match the index corpus!")
    PARAM_CHECK_ERROR_DOCUMENTS_MISMATCH_CORPUS = (200003, "The documents given don't match the corpus!")
    PARAM_CHECK_ERROR_REPORT_NAME_REQUIRED = (200004, "Report name is required.")
    PARAM_CHECK_ERROR_FIELD_TYPE_MISMATCH = (200005, "Mismatched type '{expected_type}' for field '{field}'")
    PARAM_CHECK_ERROR_CONFIG_FIELD_NOT_FOUND = (200006, "No field '{'.'.join(fields)}' in file '{cls._CONFIG_FILE}'")
    PARAM_CHECK_ERROR_INDEX_OUT_OF_RANGE = (200007, "content index {content_idx} is out of range for contents.")
    PARAM_CHECK_ERROR_INDEX_OUT_OF_RANGE_GENERIC = (200008, "Parameter validation failed: index out of range (generic)")
    PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR = (200009, "Parameter validation failed, {e}")
    PARAM_CHECK_ERROR_FIELD_NOT_STRING = (200010, "Parameter validation failed, type of feild '{field}' must be str")
    PARAM_CHECK_ERROR_FIELD_EMPTY = (200011,
                                     "Parameter validation failed, type of feild '{field}' must not be empty")
    PARAM_CHECK_ERROR_INTERRUPT_FEEDBACK_ERROR = (200012,
                                                  "Parameter 'interrupt_feedback' must be either an empty string or 'accepted'.")
    PARAM_CHECK_ERROR_COMMON_INVALID = (200013, "Parameter {param} is invalid")
    PARAM_CHECK_ERROR_PARAM_NOT_IN_RANGE = (200014, "Parameter {param} must be one of {param_range}")
    PARAM_CHECK_ERROR_FIELD_NOT_EXIST = (200015, "Parameter validation failed, feild '{field}' not exsit in dict")
    PARAM_CHECK_ERROR_SUFFIX_INVALID = (200016, "Unsupported {file_type} file type:"
                                                "{suffix}, allowed: {allowed_suffix}")
    PARAM_CHECK_ERROR_TEMPLATE_NAME_REQUIRED = (200017, "Template name is required.")
    PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT = (200018, "Parameter validation failed")
    PARAM_CHECK_ERROR_FIELD_NOT_BYTEARRAY = (200019,
                                             "Parameter validation failed, type of feild '{field}' must be bytearray")
    PARAM_CHECK_ERROR_FIELD_NOT_BOOL = (200020, "Parameter validation failed, type of feild '{field}' must be bool")
    PARAM_CHECK_ERROR_URL_EXCEED_LENGTH = (200021, "URL length must be less than 8192")
    PARAM_CHECK_ERROR_LOG_DIR_INVALID = (200022, "Invalid log directory: {log_dir}")
    PARAM_CHECK_ERROR_LOG_DIR_UNSAFE = (200023, "Unsafe log directory: {log_dir}, it must be within: {safe_base}")
    PARAM_CHECK_ERROR_STRING_LENGTH = (200024, "Parameter validation failed, string length invalid")
    PARAM_CHECK_ERROR_VAL_OUT_OF_RANGE = (200025,
                                          "Parameter {param} value {value} is out of range [{min_val}, {max_val}]")
    CONVERT_DOCX_FILE_FAILED = (200105, "Failed to convert docx file: {e}")
    FILE_NOT_FOUND_ERROR_PROMPT = (200106, "Prompt file {name}.md not found.")
    APPLY_SYSTEM_PROMPT_FAILED = (200107, "Applying system prompt template with {name}.md failed")
    CONVERT_PDF_FILE_TO_MARKDOWN_FAILED = (200108, "Failed to convert pdf file to markdown")
    TEMPLATE_NAME_INVALID = (200204, "Invalid template name: {name}. Only Chinese/English letters, numbers,"
                                     "underscores (_), hyphens (-), and dots (.) are allowed.")
    WORKFLOW_ROUTER_INIT_TYPE_ERROR = (200401, "next_nodes must be either str or list[str]")
    WORKFLOW_TYPE_NOT_EXIST_ERROR = (200402, "Workflow doesn't exsit, config is {config}")
    LLM_INSTANCE_NONE_ERROR = (200800, "llm instance is None when ainvoke, check if obtain llm first")
    LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR = (201000, "local search engine instance {name} when ainvoke, check if register")
    WEB_SEARCH_INSTANCE_OBTAIN_ERROR = (201100, "web search engine instance is {name} when ainvoke, check if register")

    # 业务类型相关错误（ 21开头，子类型编码范围 10-99）
    AGENT_RETRY_FAILED_ALL_ATTEMPTS = (211001, "Failed to get response after all retries")
    AGENT_RUN_NOT_SUPPORT = (211003, "Agent run is not supported")

    SOURCE_TRACER_TRACE_SOURCE_ERROR = (211100, "Source tracer trace source error {e}")
    SOURCE_TRACER_ADD_SOURCE_ERROR = (211101, "Source tracer add source error {e}")
    CITATION_CHECKER_DATA_LEN_ERROR = (211102, "Citation checker data len error {e}")
    CITATION_VERIFIER_DATA_LEN_ERROR = (211103, "Citation verifier data len error {e}")
    CITATION_VERIFIER_LLM_RESPONSE_ERROR = (211104, "Citation verifier llm response error {e}")
    CITATION_CHECKER_POST_PROCESS_ERROR = (211105, "Citation checker post process error {e}")
    SOURCE_TRACER_NODE_ERROR = (211106, "Source tracer node error {e}")

    JIUWEN_BASE_EXCEPTION_NOT_SUPPORTED = (211200, "_pre_handle is not supported")
    LLM_RESPONSE_ERROR = (211300, "LLM response has something wrong")
    LLM_RESPONSE_NONE = (211301, "LLM response is none")
    LOAD_EXTEND_TOOLS_FAILED = (211400, "Failed to load extend tools")
    TOOL_LOG_ERROR = (211500, "Tool log has something wrong, error: {e}")
    TOOL_EXEC_ERROR = (211600, "Tool execution has something wrong, error: {e}")

    EDITORTEAM_MANAGER_MISSING_OUTLINE = (212001, "Fail to get previously generated outline at the editor_team node.")
    EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION = (212002, "Fail to get the outline sections at the editor_team node.")
    EDITORTEAM_MANAGER_EMPTY_SUB_REPORT = (212003, "Sub report content list is empty at the editor_team node.")

    REPORT_GENERATE_ERROR = (212200, "Report generate has something wrong, error: {e}")
    SUB_REPORT_GENERATE_ERROR = (212300, "Error when generate sub report, error: {e}")
    ENTRY_GENERATE_ERROR = (211704, "Error when EntryNode classify the query")
    INTERPRETATION_GENERATE_ERROR = (211804, "Query interpretation failed with error")
    OUTLINER_GENERATE_ERROR = (211904, "Error when Outliner generate an outline")
    FEEDBACK_HANDLER_INVALID_MODE_ERROR = (212005, "Invalid feedback_mode, should be cmd or web")
    FEEDBACK_HANDLER_INVALID_FEEDBACK_ERROR = (212005, "Invalid feedback, length is 0 or type is invalid")
    PLANNER_GENERATE_ERROR = (211906, "Error when Planner generate a plan, error: {e}")
    INFO_COLLECTING_EMPTY = (211920, "Info collecting exists Abnormal, No doc infos found.")
    SECTION_INFOS_EMPTY = (211930, "Section collecting info is empty, No doc infos found.")

    @property
    def errmsg(self):
        """Return error message"""
        return self.value[1]

    @property
    def code(self):
        """Return error code"""
        return self.value[0]
