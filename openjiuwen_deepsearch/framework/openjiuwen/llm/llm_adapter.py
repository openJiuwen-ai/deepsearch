# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum

from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId


class LlmConfigCategory(enum.Enum):
    # 1. 通用模型，适用于所有节点及功能，模型综合能力较强
    GENERAL = "general"

    # 2. 推理模型，用于规划，意图识别，减少幻觉
    PLAN_UNDERSTANDING = "plan_understanding"

    # 3. 简单模型，用于任务收集信息
    INFO_COLLECTING = "info_collecting"

    # 4. 编程、数学能力较强模型，用于准确生成报告及图文渲染，校验溯源
    WRITING_CHECKING = "writing_checking"


NODE_LLM_MAPPING = {
    NodeId.OUTLINE.value: LlmConfigCategory.PLAN_UNDERSTANDING.value,
    NodeId.PLAN_REASONING.value: LlmConfigCategory.PLAN_UNDERSTANDING.value,
    NodeId.INFO_COLLECTOR.value: LlmConfigCategory.INFO_COLLECTING.value,
    NodeId.SUB_REPORTER.value: LlmConfigCategory.WRITING_CHECKING.value,
}


def adapt_llm_model_name(session: Session, node_name) -> str:
    """根据当前节点名称，自动适配应使用的 LLM 模型名"""
    llm_config = session.get_global_state("config.llm_config")
    if node_name in NODE_LLM_MAPPING:
        model_category = NODE_LLM_MAPPING.get(node_name)
        if model_category not in llm_config:
            model_category = LlmConfigCategory.GENERAL.value
    else:
        model_category = LlmConfigCategory.GENERAL.value
    llm_model_name = session.get_global_state(f"config.llm_config.{model_category}.model_name")
    return llm_model_name
