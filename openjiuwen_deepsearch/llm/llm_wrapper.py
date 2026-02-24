# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging

from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_model_factory import LLMModelFactory
from openjiuwen_deepsearch.config.config import AgentConfig, Config
from openjiuwen_deepsearch.utils.common_utils.security_utils import zero_secret
from openjiuwen_deepsearch.utils.validation_utils.param_validation import validate_llm_obj_params

logger = logging.getLogger(__name__)


def create_llm_obj(agent_config: AgentConfig):
    """创建自定义llm"""
    llm_config = agent_config.llm_config
    try:
        validate_llm_obj_params(llm_config)
        model = LLMModelFactory().get_model(
            model_provider=llm_config.model_type,
            api_key=bytes(llm_config.api_key).decode('utf-8'),
            api_base=llm_config.base_url,
            timeout=Config().service_config.llm_timeout
        )
        model_name = llm_config.model_name
        return dict(model=model, model_name=model_name)
    finally:
        zero_secret(llm_config.api_key)
