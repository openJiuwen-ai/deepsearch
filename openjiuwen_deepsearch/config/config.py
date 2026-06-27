# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, List, Literal, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen_deepsearch.config.runtime_api_models import ApiToolsConfig

OUTLINER_SECTION_NUM_MAX = 15


class LLMConfig(BaseModel):
    model_name: str = Field(default="", description="模型名称")
    model_type: Literal["openai", "siliconflow"] = Field(default="openai", description="模型类型")
    base_url: str = Field(default="", description="模型服务地址")
    api_key: bytearray = Field(default=bytearray("", encoding="utf-8"), description="模型调用密钥")
    hyper_parameters: dict = Field(default_factory=dict, description="模型调用超参数设置，根据具体模型接口设置")
    extension: dict = Field(default_factory=dict, description="模型扩展配置项，根据具体模型接口设置")
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )
    timeout: int = Field(default=600, description="请求超时时间（秒）")
    max_tries: int = Field(default=4, description="最大重试次数")
    append_think_tags_to_messages: bool = Field(
        default=False,
        description="是否在消息中追加 think 标签"
    )


class WebSearchEngineConfig(BaseModel):
    search_engine_name: Literal[
        "tavily",
        "google",
        "xunfei",
        "petal",
        "custom",
        "bocha",
        "jina",
        "perplexity",
        "serper",
    ] = Field(default="tavily",
                                                                                         description="联网增强引擎名称")
    search_api_key: bytearray = Field(default=bytearray("", encoding="utf-8"), description="联网增强引擎调用密钥")
    search_url: str = Field(default="", description="联网增强引擎调用地址")
    max_web_search_results: int = Field(default=5, ge=1, le=10, description="最大搜索结果数量")
    extension: dict = Field(default_factory=dict, description="联网增强引擎扩展配置项，根据具体联网增强引擎接口设置")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class EmbedModelConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model_name: str = Field(..., description="Embedding模型名称")
    api_key: bytearray = Field(..., description="Embedding模型密钥")
    base_url: str = Field(..., description="接口地址")
    max_batch_size: int = Field(..., description="最大批次大小")
    timeout: int = Field(default=60, description="请求超时时间")
    max_retries: int = Field(default=3, description="最大重试次数")


class VectorStoreConfig(BaseModel):
    uri: str = Field(..., description="向量数据库连接地址")
    token: str = Field(..., description="连接令牌")
    collection_name: str = Field(..., description="集合名称，形如kb_{kb_id}_chunks")


class NativeKnowledgeBaseConfig(BaseModel):
    id: str = Field(..., description="知识库 ID")
    index_type: Literal["vector"] = Field(default="vector", description="索引类型")
    embed_model_config: EmbedModelConfig = Field(..., description="Embedding模型配置")
    vector_store: VectorStoreConfig = Field(..., description="向量库配置")


class LocalSearchEngineConfig(BaseModel):
    search_engine_name: Literal["openapi", "custom", "native"] = Field(default="openapi",
                                                                       description="本地搜索引擎名称")
    search_api_key: bytearray = Field(default=bytearray("", encoding="utf-8"), description="本地搜索引擎调用密钥")
    search_url: str = Field(default="", description="本地搜索引擎调用地址")
    search_datasets: list = Field(default_factory=list, description="本地搜索引擎数据集配置")
    max_local_search_results: int = Field(default=5, ge=1, le=10, description="最大本地搜索结果数量")
    recall_threshold: float = Field(default=0.5, description="本地搜索文档召回相似度阈值")
    search_mode: Literal["doc", "keyword", "mix"] = Field(default="doc", description="检索策略模式："
                                                                                     "doc：语义检索"
                                                                                     "keyword：关键词检索"
                                                                                     "mix：混合检索")
    knowledge_base_type: Literal["internal", "external"] = Field(default="internal", description="知识库类型")
    source: Literal["KooSearch", "LakeSearch"] = Field(default="KooSearch", description="知识库来源")
    extension: dict = Field(default_factory=dict, description="本地搜索引擎扩展配置项，根据具体搜索引擎接口设置")
    knowledge_base_configs: List[NativeKnowledgeBaseConfig] = Field(default_factory=list, description="本地知识库配置")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CustomWebSearchConfig(BaseModel):
    custom_web_search_file: str = Field(default="", description="自定义联网增强引擎工具文件路径")
    custom_web_search_func: str = Field(default="", description="自定义联网增强引擎工具函数名称")
    extension: dict = Field(default_factory=dict, description="自定义联网增强引擎工具扩展配置项，根据具体联网增强引擎接口设置")


class CustomLocalSearchConfig(BaseModel):
    custom_local_search_file: str = Field(default="", description="自定义本地搜索工具文件路径")
    custom_local_search_func: str = Field(default="", description="自定义本地搜索工具函数名称")
    extension: dict = Field(default_factory=dict, description="自定义本地搜索工具扩展配置项，根据具体搜索引擎接口设置")


class ActionSamplingConfig(BaseModel):
    """
    Action 采样策略配置
    """
    depth_weight: bool = Field(default=True, description="是否使用深度权重")
    promote_unique_states: bool = Field(default=False, description="是否提升唯一状态")
    random_sample: bool = Field(default=False, description="是否随机采样")


class PerQuestionParams(BaseModel):
    """
    单个问题（一次搜索 / 推理过程）的控制参数
    """

    max_workers: int = Field(default=5, description="并发执行 action 的最大协程数")
    retry_count_on_empty_action_space: int = Field(
        default=3,
        description=(
            "When the action pool has no runnable actions and no workers are busy, re-run find_action "
            "this many times before stopping (each attempt decrements the counter)."
        ),
    )
    time_limit: int = Field(default=4800, description="单个问题最大运行时间（秒）")
    tool_map: Literal["search_fetch", "retrieve"] = Field(default="search_fetch", description="工具映射")
    actions_explored_limit: int = Field(default=200, description="最大探索 action 数量，200=无限制")
    fail_limit: int = Field(default=0, description="最大连续失败次数，0=无限制")
    answer_mode_top_k: int = Field(
        default=1,
        description=(
            "Number of candidate answers to collect before selecting the best. "
            "<=1 returns on the first answer found (original behaviour). "
            ">1 collects that many answers and returns the one with the highest candidate score; "
            "on timeout the best answer collected so far is returned."
        ),
    )
    provide_best_guess: bool = Field(
        default=False,
        description=(
            "When True and the search times out without a confirmed answer, "
            "scan all completed actions for the one whose answer variable has the "
            "highest candidate_strength and return it as a best-guess prediction "
            "(termination reason: timeout_guess)."
        ),
    )


class MilvusConfig(BaseModel):
    """
    Milvus 配置。
    """

    milvus_host: str = Field(default="localhost", description="Milvus 主机地址")
    milvus_port: int = Field(default=19530, description="Milvus 端口")
    database_name: str = Field(default="deepsearch_benchmarks", description="数据库名称")
    collection_name: str = Field(default="browsecompplus_with_bm25", description="集合名称")
    embedder_model_name: str = Field(
        default="",
        description="Embedding 模型名称（需与索引构建时所用模型一致）",
    )
    embedder_api_key: bytearray = Field(
        default=bytearray("", encoding="utf-8"),
        description="Embedding 模型密钥",
    )
    embedder_base_url: str = Field(
        default="",
        description="Embedding 服务地址，例如：http://localhost:11450/v1/embeddings",
    )
    embedder_timeout: int = Field(default=100, description="Embedding 请求超时时间（秒），例如：100")
    retriever_class: Optional[Any] = Field(
        default=None,
        description="Optional retriever implementation class for RetrieveTool (e.g. BrowsecompPlusMilvusRetriever).",
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class InitStateAgentConfig(BaseModel):
    """
    Init State Agent
    """

    max_tries: int = Field(default=10, description="最大重试次数")
    llm_config: Dict[
        Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig
    ] = Field(default_factory=dict, description="LLM配置")


class FindActionAgentConfig(BaseModel):
    """
    Find Action Agent
    """

    llm_config: Dict[
        Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig
    ] = Field(default_factory=dict, description="LLM配置")
    action_proposals_limit: int = Field(default=5, description="最大 action 提案数")
    action_pool_depleted_strategy: Literal["simple_retry", "dependent_retry"] = Field(
        default="dependent_retry",
        description="Strategy when action pool is depleted: "
                    "simple_retry re-runs find_action with no context; "
                    "dependent_retry includes previously explored directions",
    )


class ValidatorAgentConfig(BaseModel):
    """
    State / Answer 校验 Agent
    """

    validate_new_states: bool = Field(default=False, description="是否验证新状态")
    validate_answer: bool = Field(default=False, description="是否验证答案")
    llm_config: Dict[
        Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig
    ] = Field(default_factory=dict, description="LLM配置")


class RetrievalSettingsConfig(BaseModel):
    """
    Retrieval Settings
    """

    retrieval_prompt: Literal["retrieve", "retrieve_given_multihop_query"] = Field(
        default="retrieve", description="检索提示"
    )
    top_k: int = Field(default=3, description="最大检索结果数量")
    top_k_multiply_factor: int = Field(default=5, description="最大检索结果数量乘数因子")
    add_instruction: bool = Field(default=True, description="是否添加指令")
    mode: Literal["dense", "sparse", "hybrid"] = Field(default="hybrid", description="检索模式")    


class StateCreationAgentConfig(BaseModel):
    """
    State 扩展与评估策略
    """

    log_fetch: bool = Field(default=False, description="是否记录检索日志")
    log_search: bool = Field(default=False, description="是否记录搜索日志")

    web_fetch_log_file: str = Field(default="gnosis/tool_log/web_fetch_log.jsonl", description="检索日志文件路径")
    web_search_log_file: str = Field(default="gnosis/tool_log/web_search_log.jsonl", description="搜索日志文件路径")

    use_candidate_strength: bool = Field(default=True, description="是否使用候选强度")
    discovered_clues_mode: Literal["report", "blacklist"] = Field(default="blacklist", description="发现线索模式")

    max_llm_calls_per_run: int = Field(default=100, description="单次 state creation 最大 LLM 调用数")
    context_limit_reached_strategy: Literal[
        "fail",
        "reduced_retrieval_request",
        "delete_tool_responses",
        "delete_tool_input_and_responses",
    ] = Field(
        default="reduced_retrieval_request",
        description=(
            "Strategy when the LLM context limit is hit during a run-action call. "
            "'fail' terminates the action immediately. "
            "'reduced_retrieval_request' halves top_k / top_k_multiply_factor and retries "
            "(only effective when retrieval tool is in use). "
            "'delete_tool_responses' strips all tool result messages from the conversation "
            "and retries with the reduced context. "
            "'delete_tool_input_and_responses' strips both assistant tool_calls and "
            "tool result messages, then retries."
        ),
    )
    llm_config: Dict[
        Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig
    ] = Field(default_factory=dict, description="LLM配置")
    retrieval_settings: RetrievalSettingsConfig = Field(default_factory=RetrievalSettingsConfig)
    validator_agent: ValidatorAgentConfig = Field(default_factory=ValidatorAgentConfig)


class SearchWorkflowConfig(BaseModel):
    """
    Search Workflow
    """
    action_sampling: ActionSamplingConfig = Field(default_factory=ActionSamplingConfig)
    init_state_agent: InitStateAgentConfig = Field(default_factory=InitStateAgentConfig)
    find_action_agent: FindActionAgentConfig = Field(default_factory=FindActionAgentConfig)
    state_creation_agent: StateCreationAgentConfig = Field(default_factory=StateCreationAgentConfig)


class AgentConfig(BaseModel):
    """
    Agent配置类
    """
    execute_mode: Literal["commercial", "general"] = Field(default="commercial",
                                                           description='执行模式，可选值: ["commercial", "general"]')
    execution_method: Literal["dependency_driving", "parallel"] = Field(default="parallel",
                                                                        description="执行方法: "
                                                                                    "dependency_driving: 依赖驱动工作流执行"
                                                                                    "parallel: 并行工作流执行")
    workflow_human_in_the_loop: bool = Field(default=True, description="工作流是否启用人机交互")
    outliner_max_section_num: int = Field(
        default=5,
        ge=1,
        le=OUTLINER_SECTION_NUM_MAX,
        description=f"大纲章节数量，取值范围:[1,{OUTLINER_SECTION_NUM_MAX}]",
    )
    outline_interaction_enabled: bool = Field(default=True, description="大纲交互开关")
    outline_interaction_max_rounds: int = Field(default=3, ge=1, le=100, description="大纲交互最大轮次")
    source_tracer_research_trace_source_switch: bool = Field(default=True, description="溯源功能开关")
    source_tracer_generated_citation_switch: bool = Field(default=True, description="新增引用生成开关")
    source_tracer_infer_switch: bool = Field(default=True, description="溯源推理功能开关")
    llm_config: Dict[
        Literal["general", "plan_understanding", "info_collecting", "writing_checking",
                "vlm_chart_generating"], LLMConfig
    ] = Field(default_factory=dict, description="LLM配置")
    info_collector_search_method: Literal["web", "local", "all"] = Field(default="web",
                                                                         description="搜索方式: "
                                                                                     "web: 联网搜索"
                                                                                     "local: 本地搜索工具搜索"
                                                                                     "all: 联网+本地融合搜索")
    web_search_engine_config: WebSearchEngineConfig = Field(default_factory=WebSearchEngineConfig)
    local_search_engine_config: LocalSearchEngineConfig = Field(default_factory=LocalSearchEngineConfig)
    custom_web_search_config: CustomWebSearchConfig = Field(default_factory=CustomWebSearchConfig)
    custom_local_search_config: CustomLocalSearchConfig = Field(default_factory=CustomLocalSearchConfig)
    search_mode: Literal["research", "search", "react"] = Field(
        default="research",
        description="research: 研究报告; search: DeepSearch 图; react: 简单 ReAct + 与 search 相同的工具",
    )
    enable_question_router: bool = Field(
        default=False,
        description="为 True 且 search_mode 为 search 时，先经 LLM 路由：0→react，1→search（DeepSearch）",
    )
    search_workflow_per_question_params: PerQuestionParams = Field(default_factory=PerQuestionParams)
    search_workflow_milvus_config: MilvusConfig = Field(default_factory=MilvusConfig)
    jina_api_key: bytearray = Field(default=bytearray("", encoding="utf-8"), description="Jina API密钥")
    serper_api_key: bytearray = Field(default=bytearray("", encoding="utf-8"), description="Serper API密钥")
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 联网增强引擎 QPS 流控配置
    web_search_max_qps: float = Field(default=0, description="联网增强引擎最大 QPS，0 表示不限流，支持浮点数如 0.5 表示每 2 秒 1 个请求")
    api_tools_config: ApiToolsConfig = Field(default_factory=ApiToolsConfig, description="API tools config")

    # 用户反馈局部优化参数
    user_feedback_processor_enable: bool = Field(default=False, description="是否启用用户反馈优化功能")
    user_feedback_processor_max_interactions: int = Field(default=100, ge=1, le=100, description="最大交互次数")

    # 统计性能信息参数
    stats_info_llm: bool = Field(default=False, description="LLM调用统计")

    # vlm迭代生成图参数
    vlm_chart_generator_enable: bool = Field(default=False, description="vlm迭代生成图开关")
    vlm_chart_generator_max_iterations: int = Field(default=1, ge=1, le=3, description="vlm迭代生成图最大迭代次数")

    agent_llm_timeouts: Dict[str, int] = Field(default_factory=dict, description="按 agent 配置的 LLM 总超时时间")


class ServiceConfig(BaseModel):
    """
    服务配置类
    """

    # 服务基础配置
    service_allow_origins: List[str] = Field(default_factory=list, description="允许的ip范围")

    # 模板参数
    template_max_generate_retry_num: int = Field(default=3, description="模板生成最大重试次数")

    # 工作流基础参数
    workflow_execution_timeout: int = Field(default=7200, description="工作流执行超时时间")
    workflow_sub_graph_execution_timeout: int = Field(default=6000, description="子图执行超时时间")
    workflow_max_plan_executed_num: int = Field(default=2, description="最大计划执行数量")
    workflow_recursion_limit: int = Field(default=30, description="递归限制")
    workflow_max_gen_question_retry_num: int = Field(default=3, description="最大生成问题执行数量")
    workflow_feedback_mode: str = Field(default="web", description='用户反馈途径, 可选值: ["web", "cmd"]')

    # Search mode 相关参数
    search_workflow: SearchWorkflowConfig = Field(default_factory=SearchWorkflowConfig)

    # 大纲节点基础参数
    outliner_max_generate_outline_retry_num: int = Field(default=3, description="最大生成大纲重试次数")

    # 规划节点基础参数
    planner_max_step_num: int = Field(default=2, description="最大步骤数量")
    planner_max_retry_num: int = Field(default=3, description="最大重试次数")

    # 信息收集节点参数
    info_collector_max_react_recursion_limit: int = Field(default=6, description="React代理最大递归限制")
    info_collector_initial_search_query_count: int = Field(default=2, description="初始搜索查询数量")
    info_collector_max_research_loops: int = Field(default=2, description="最大研究循环次数")
    info_collector_max_retry_num: int = Field(default=3, description="最大重试次数")
    info_collector_allow_programmer: bool = Field(default=False, description="")

    # 报告节点参数
    sub_report_classify_doc_infos_single_time_num: int = Field(default=60,
                                                               description="子报告中单次llm处理筛选收集到的数量")
    sub_report_classify_doc_infos_res_top_k_num: int = Field(default=8,
                                                             description="子报告中单次llm处理返回的top_k数量")
    sub_report_doc_prefilter_multiplier: int = Field(default=5,
                                                     description="子报告文档预筛保留倍数，最大候选数为top_k乘以该值")
    report_max_generate_retry_num: int = Field(default=3, description="生成内容最大重试次数")
    visualization_enable: bool = Field(default=False, description="报告插入图表开关")

    # 溯源节点参数
    source_tracer_citation_verify_max_concurrency_num: int = Field(default=30, description="溯源校验最大并发数量")
    source_tracer_citation_verify_batch_size: int = Field(default=10, ge=1, le=20, description="溯源校验批次大小")
    source_tracer_domain_source_map_path: str = Field(
        default="domain_source_map.json",
        description="域名来源动态映射表JSON文件名（相对路径解析到可写数据目录，绝对路径直接使用）",
    )

    # 统计性能信息参数
    stats_info_node_duration: bool = Field(default=False, description="节点持续时间统计")
    stats_info_search: bool = Field(default=False, description="搜索工具调用统计")

    # 大模型超时参数
    llm_timeout: int = Field(default=300, description="大模型调用超时时间，单位秒")

    # 大模型思考模式开关参数
    llm_thinking_enabled: bool = Field(default=False, description="是否开启大模型思考模式，默认关闭")

    # debug辅助工具参数
    node_debug_enable: bool = Field(default=False, description="节点格式化记录debug日志开关")
    export_intermediate_results: bool = Field(default=False, description="可视化任务执行中间结果开关")


class Config(BaseModel):
    """
    总配置类
    """
    agent_config: AgentConfig = Field(default_factory=AgentConfig, description="对外开放的Agent参数")
    service_config: ServiceConfig = Field(default_factory=ServiceConfig, description="SDK服务默认参数")
