# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum


class StatusCode(Enum):
    """Status code enum

    openJiuwen-DeepSearch错误码由6位数字组成，结构如下：
    前两位：错误类型（20：公共类型错误。21：具体模块错误）
    中间两位：子类型，具体参见下表，编号范围 00~99
    后两位：具体错误编号，编号范围 00-99，即同一个子类型下的不同错误码

    子类型说明表
    1. 公共类型错误子类型
    |    分类编号    |        分类名称        |                     说明                     |
    |--------------|-----------------------|---------------------------------------------|
    |      00      |  参数校验错误           |  参数校验不合法、缺失、类型不匹配等                |
    |      01      |  文件相关错误           |  文件不存在、文件格式错误、写入失败等              |

    2. 模块错误子类型
    |    分类编号    |        分类名称        |                     说明                                         |
    |--------------|-----------------------|-----------------------------------------------------------------|
    |      10      |  agent错误             |  agent创建失败、方法未实现等                                        |
    |      11      |  workflow错误          |  workflow创建失败、不存在等                                        |
    |      12      |  LLM错误               |  LLM注册失败、LLM实例化失败、LLM调用超时等                            |
    |      13      |  TOOL错误              |  Tool注册失败、Tool实例化失败、Tool调用超时等                         |
    |      14      |  流程异常错误            |  StartNode异常、EndNode异常、pre_handler异常、post_handler异常等    |
    |      15      |  管理节点模块错误         |  EditorTeamNode模块异常等                                         |
    |      16      |  意图理解模块错误         |  entry模块异常：结果执行失败等                                      |
    |      17      |  人机交互模块错误         |  GenerateQuestionsNode模块异常、FeedbackHandlerNode模块异常等      |
    |      18      |  研究规划模块错误         |  Outline模块异常、Plan模块异常等                                   |
    |      19      |  信息收集模块错误         |  信息获取异常、信息评估等后处理失败等                                 |
    |      20      |  报告撰写模块错误         |  子章节筛选文档失败、子报告生成失败、图表异常、总报告生成失败等            |
    |      21      |  溯源引用模块错误         |  溯源生成失败、溯源校验失败等                                        |
    |      22      |  模板功能模块错误         |  模板提取失败异常等                                                |
    |      23      |  溯源推理模块错误         |  溯源推理生成失败等                                                |
    |      24      |  用户反馈处理模块错误      |  UserFeedbackProcessorNode模块异常：改写失败、偏移量不匹配等          |

    """

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
    PARAM_CHECK_ERROR_FIELD_EMPTY = (
        200011, "Parameter validation failed, type of feild '{field}' must not be empty")
    PARAM_CHECK_ERROR_INTERRUPT_FEEDBACK_ERROR = (
        200012, "Parameter 'interrupt_feedback' must be either an empty string or 'accepted' or 'cancel'.")
    PARAM_CHECK_ERROR_COMMON_INVALID = (200013, "Parameter {param} is invalid")
    PARAM_CHECK_ERROR_PARAM_NOT_IN_RANGE = (200014, "Parameter {param} must be one of {param_range}")
    PARAM_CHECK_ERROR_FIELD_NOT_EXIST = (200015, "Parameter validation failed, feild '{field}' not exsit in dict")
    PARAM_CHECK_ERROR_SUFFIX_INVALID = (
        200016, "Unsupported {file_type} file type: {suffix}, allowed: {allowed_suffix}")
    PARAM_CHECK_ERROR_TEMPLATE_NAME_REQUIRED = (200017, "Template name is required.")
    PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT = (200018, "Parameter validation failed")
    PARAM_CHECK_ERROR_FIELD_NOT_BYTEARRAY = (
        200019, "Parameter validation failed, type of feild '{field}' must be bytearray")
    PARAM_CHECK_ERROR_FIELD_NOT_BOOL = (200020, "Parameter validation failed, type of feild '{field}' must be bool")
    PARAM_CHECK_ERROR_URL_EXCEED_LENGTH = (200021, "URL length must be less than 8192")
    PARAM_CHECK_ERROR_LOG_DIR_INVALID = (200022, "Invalid log directory: {log_dir}")
    PARAM_CHECK_ERROR_LOG_DIR_UNSAFE = (200023, "Unsafe log directory: {log_dir}, it must be within: {safe_base}")
    PARAM_CHECK_ERROR_STRING_LENGTH = (200024, "Parameter validation failed, string length invalid")
    PARAM_CHECK_ERROR_VAL_OUT_OF_RANGE = (
        200025, "Parameter {param} value {value} is out of range [{min_val}, {max_val}]")
    PARAM_CHECK_ERROR_FILE_DIR_INVALID = (200026, "Invalid file directory: {file_dir}")
    PARAM_CHECK_ERROR_FILE_DIR_UNSAFE = (200027, "Unsafe file directory: {file_dir}, it must be within: {safe_base}")

    FILE_NOT_FOUND_ERROR_PROMPT = (200100, "Prompt file {name}.md not found.")
    APPLY_SYSTEM_PROMPT_FAILED = (200101, "Applying system prompt template with {name}.md failed")
    CONVERT_DOCX_FILE_FAILED = (200102, "Failed to convert docx file: {e}")
    CONVERT_PDF_FILE_TO_MARKDOWN_FAILED = (200103, "Failed to convert pdf file to markdown")

    # 业务类型相关错误（21开头，子类型编码范围 10-99）
    AGENT_RETRY_FAILED_ALL_ATTEMPTS = (211000, "Failed to get response after all retries")
    AGENT_RUN_NOT_SUPPORT = (211001, "Agent run is not supported")

    WORKFLOW_ROUTER_INIT_TYPE_ERROR = (211100, "next_nodes must be either str or list[str]")
    WORKFLOW_TYPE_NOT_EXIST_ERROR = (211101, "Workflow doesn't exsit, config is {config}")

    LLM_INSTANCE_NONE_ERROR = (211200, "llm instance is None when ainvoke, check if obtain llm first")
    LLM_RESPONSE_ERROR = (211201, "LLM response has something wrong")
    LLM_RESPONSE_NONE = (211202, "LLM response is none")
    LLM_CONFIG_NONE = (211203, "LLM is not configured, at least the general model needs to be configured")

    WEB_SEARCH_INSTANCE_OBTAIN_ERROR = (211300, "web search engine instance is {name} when ainvoke, check if register")
    LOCAL_SEARCH_INSTANCE_OBTAIN_ERROR = (211301, "local search engine instance {name} when ainvoke, check if register")
    LOAD_EXTEND_TOOLS_FAILED = (211302, "Failed to load extend tools")
    TOOL_LOG_ERROR = (211303, "Tool log has something wrong, error: {e}")
    TOOL_EXEC_ERROR = (211304, "Tool execution has something wrong, error: {e}")
    RATE_LIMIT_TIMEOUT_ERROR = (211305, "Rate limit timeout after {timeout:.1f}s, max_qps={max_qps}")

    JIUWEN_BASE_EXCEPTION_NOT_SUPPORTED = (211400, "_pre_handle is not supported")

    EDITORTEAM_MANAGER_MISSING_OUTLINE = (211500, "Fail to get previously generated outline at the editor_team node.")
    EDITORTEAM_MANAGER_MISSING_OUTLINE_SECTION = (211501, "Fail to get the outline sections at the editor_team node.")
    EDITORTEAM_MANAGER_EMPTY_SUB_REPORT = (211502, "Sub report content list is empty at the editor_team node.")

    ENTRY_GENERATE_ERROR = (211600, "Error when EntryNode classify the query")

    INTERPRETATION_GENERATE_ERROR = (211700, "Query interpretation failed with error")
    FEEDBACK_HANDLER_INVALID_MODE_ERROR = (211701, "Invalid feedback_mode, should be cmd or web")
    FEEDBACK_HANDLER_INVALID_FEEDBACK_ERROR = (211702, "Invalid feedback, length is 0 or type is invalid")

    OUTLINER_GENERATE_ERROR = (211800, "Error when Outliner generate an outline")
    PLANNER_GENERATE_ERROR = (211801, "Error when Planner generate a plan, error: {e}")

    INFO_COLLECTING_EMPTY = (211900, "Info collecting exists Abnormal, No doc infos found.")
    SECTION_INFOS_EMPTY = (211901, "Section collecting info is empty, No doc infos found.")

    SUB_REPORT_GENERATE_ERROR = (212000, "Error when generate sub report, error: {e}")
    REPORT_GENERATE_ERROR = (212001, "Report generate has something wrong, error: {e}")

    SOURCE_TRACER_TRACE_SOURCE_ERROR = (212100, "Source tracer trace source error {e}")
    SOURCE_TRACER_ADD_SOURCE_ERROR = (212101, "Source tracer add source error {e}")
    CITATION_CHECKER_DATA_LEN_ERROR = (212102, "Citation checker data len error {e}")
    CITATION_VERIFIER_DATA_LEN_ERROR = (212103, "Citation verifier data len error {e}")
    CITATION_VERIFIER_LLM_RESPONSE_ERROR = (212104, "Citation verifier llm response error {e}")
    CITATION_CHECKER_POST_PROCESS_ERROR = (212105, "Citation checker post process error {e}")
    SOURCE_TRACER_NODE_ERROR = (212106, "Source tracer node error {e}")

    TEMPLATE_NAME_INVALID = (212201, "Invalid template name: {name}. Only Chinese/English letters, numbers,"
                                     "underscores (_), hyphens (-), and dots (.) are allowed.")
    
    SOURCE_TRACER_INFER_ERROR = (212300, "Source tracer infer error {e}")
    SOURCE_TRACER_INFER_DATA_TYPE_ERROR = (212301, "Source tracer infer data type error {e}")
    SOURCE_TRACER_INFER_DATA_LEN_ERROR = (212302, "Source tracer infer data length error {e}")

    USER_FEEDBACK_PROCESSOR_DISABLED = (212400, "User feedback processor is disabled")
    USER_FEEDBACK_PROCESSOR_MAX_INTERACTIONS_REACHED = (212401, "Max interaction limit reached: {max_interactions}")
    USER_FEEDBACK_PROCESSOR_TEXT_TOO_LONG = (212402, "Selected text exceeds max length: {max_length}")
    USER_FEEDBACK_PROCESSOR_OFFSET_MISMATCH = (
        212403, "Selected text does not match content at offset range [{start}, {end})"
    )
    USER_FEEDBACK_PROCESSOR_INVALID_ACTION = (212404, "Invalid action: {action}")
    USER_FEEDBACK_PROCESSOR_REWRITE_ERROR = (212405, "Rewrite failed: {e}")
    USER_FEEDBACK_PROCESSOR_INVALID_JSON = (212406, "Invalid JSON format in user feedback: {e}")
    USER_FEEDBACK_PROCESSOR_INVALID_PARAM_TYPE = (
        212407, "Invalid parameter type for {param}, expected {expected_type}"
    )
    USER_FEEDBACK_PROCESSOR_INVALID_OFFSET_RANGE = (212408, "Invalid offset range [{start}, {end})")

    @property
    def errmsg(self):
        """Return error message"""
        return self.value[1]

    @property
    def code(self):
        """Return error code"""
        return self.value[0]
