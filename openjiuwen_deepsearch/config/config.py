# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Literal, Dict

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    model_name: str = Field(default="", description="模型名称")
    model_type: Literal["openai", "siliconflow"] = Field(default="openai", description="模型类型")
    base_url: str = Field(default="", description="模型服务地址")
    api_key: bytearray = Field(default=bytearray("", encoding="utf-8"), description="模型调用密钥")
    hyper_parameters: dict = Field(default_factory=dict, description="模型调用超参数设置，根据具体模型接口设置")
    extension: dict = Field(default_factory=dict, description="模型扩展配置项，根据具体模型接口设置")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class WebSearchEngineConfig(BaseModel):
    search_engine_name: Literal["tavily", "google", "xunfei", "petal", "custom"] = Field(default="tavily",
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


class AgentConfig(BaseModel):
    '''
    Agent配置类
    '''
    execute_mode: Literal["commercial", "general"] = Field(default="commercial",
                                                           description='执行模式，可选值: ["commercial", "general"]')
    execution_method: Literal["dependency_driving", "parallel"] = Field(default="parallel",
                                                                        description="执行方法: "
                                                                                    "dependency_driving: 依赖驱动工作流执行"
                                                                                    "paralles: 并行工作流执行")
    workflow_human_in_the_loop: bool = Field(default=True, description="工作流是否启用人机交互")
    outliner_max_section_num: int = Field(default=5, ge=1, le=10, description="最大规划章节数量，取值范围:[1,10]")
    source_tracer_research_trace_source_switch: bool = Field(default=True, description="溯源功能开关")
    llm_config: Dict[
        Literal["general", "plan_understanding", "info_collecting", "writing_checking"], LLMConfig
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


class ServiceConfig(BaseModel):
    '''
    服务配置类
    '''
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

    # 大纲节点基础参数
    outliner_max_generate_outline_retry_num: int = Field(default=3, description="最大生成大纲重试次数")
    outliner_specified_llm: str = Field(default="", description='默认使用基础llm，可选值:["", "qwen"]')

    # 规划节点基础参数
    planner_max_step_num: int = Field(default=3, description="最大步骤数量")
    planner_specified_llm: str = Field(default="", description='默认使用基础llm，可选值:["", "qwen"]')
    planner_max_retry_num: int = Field(default=3, description="最大重试次数")

    # 信息收集节点参数
    info_collector_max_react_recursion_limit: int = Field(default=8, description="React代理最大递归限制")
    info_collector_initial_search_query_count: int = Field(default=3, description="初始搜索查询数量")
    info_collector_max_research_loops: int = Field(default=2, description="最大研究循环次数")
    info_collector_max_retry_num: int = Field(default=3, description="最大重试次数")
    info_collector_allow_programmer: bool = Field(default=False, description="")

    # 报告节点参数
    sub_report_classify_doc_infos_single_time_num: int = Field(default=60,
                                                               description="子报告中单次llm处理筛选收集到的数量")
    sub_report_classify_doc_infos_res_top_k_num: int = Field(default=10,
                                                             description="子报告中单次llm处理返回的top_k数量")
    report_max_generate_retry_num: int = Field(default=3, description="生成内容最大重试次数")
    visualization_enable: bool = Field(default=True, description="报告插入图表开关")

    # 溯源节点参数
    source_tracer_citation_verify_max_concurrency_num: int = Field(default=30, description="溯源校验最大并发数量")
    source_tracer_citation_verify_batch_size: int = Field(default=1, description="溯源校验批次大小")

    # 统计性能信息参数
    stats_info_node_duration: bool = Field(default=False, description="节点持续时间统计")
    stats_info_llm: bool = Field(default=False, description="LLM调用统计")
    stats_info_search: bool = Field(default=False, description="搜索工具调用统计")

    # 大模型超时参数
    llm_timeout: int = Field(default=300, description="大模型调用超时时间，单位秒")

    # debug辅助工具参数
    node_debug_enable: bool = Field(default=False, description="节点格式化记录debug日志开关")
    export_intermediate_results: bool = Field(default=False, description="可视化任务执行中间结果开关")

    # 联网增强引擎 QPS 流控配置
    web_search_max_qps: float = Field(default=0, description="联网增强引擎最大 QPS，0 表示不限流，支持浮点数如 0.5 表示每 2 秒 1 个请求")


class Config(BaseModel):
    '''
    总配置类
    '''
    agent_config: AgentConfig = Field(default_factory=AgentConfig, description="对外开放的Agent参数")
    service_config: ServiceConfig = Field(default_factory=ServiceConfig, description="SDK服务默认参数")
