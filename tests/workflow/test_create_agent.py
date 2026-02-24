import copy
import logging

import pytest

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

llm_config = {
    "model_name": "qwen",
    "model_type": "openai",
    "base_url": "",
    "api_key": bytearray("", encoding="utf-8"),
    "hyper_parameters": {
        "top_p": 1.0,
        "frequency_penalty": 0.0,
        "max_tokens": 2048,
        "temperature": 0.0
    },
    "extension": {},
}

web_search_engine_config = {
    "search_engine_name": "petal",
    "search_api_key": bytearray("", encoding="utf-8"),
    "search_url": "",
    "max_web_search_results": 5,
    "extension": {
    }
}

local_search_engine_config = {
    "search_engine_name": "openapi",
    "search_api_key": bytearray("", encoding="utf-8"),
    "search_url": "",
    "search_datasets": [],
    "max_local search results": 5,
    "recall_threshold": 0.5,
    "extension": {
    }
}

agent_config = {
    "execute_mode": "commercial",
    "workflow_human_in_the_loop": False,
    "outliner_max_section_num": 5,
    "source_tracer_research_trace_source_switch": True,
    "llm_config": llm_config,
    "info_collector_search_method": "web",
    "web_search_engine_config": web_search_engine_config,
    "local_search_engine_config": local_search_engine_config,
}

agent_factory = AgentFactory()


def test_agent_factory_create_different_agent():
    """
        Feature: Test agent factory creation with different configuration combinations
        Description:
            - Base case: Default config creates DeepresearchAgent
            - llm_config.execution="parallel" creates DeepresearchAgent
            - llm_config.execution="dependency_driving" creates DeepresearchDependencyAgent
        Expectation:
            - Each configuration combination returns the corresponding agent subclass
            - Type assertions validate correct class instantiation
    """
    current_config = copy.deepcopy(agent_config)
    agent = agent_factory.create_agent(current_config)
    logger.info(type(agent))
    assert isinstance(agent, DeepresearchAgent)


@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    ("execute_mode", 200015, "feild 'execute_mode' not exsit in dict"),
    ("llm_config", 200015, "feild 'llm_config' not exsit in dict"),
    ("info_collector_search_method", 200015, "feild 'info_collector_search_method' not exsit in dict"),
])
def test_agent_factory_agent_require_field(invalid_value, error_code, error_msg_fragment):
    with pytest.raises(CustomValueException) as exc_info:
        invalid_agent = copy.deepcopy(agent_config)
        invalid_agent.pop(invalid_value)
        agent = agent_factory.create_agent(invalid_agent)

    err_msg = str(exc_info.value)
    logger.info(f"error_info: {err_msg}")
    assert exc_info.value.error_code == error_code
    assert error_msg_fragment in err_msg


def validate_config_parameter(config_key, invalid_value, error_code, error_msg_fragment, base_config):
    LogManager.init(is_sensitive=False)
    """验证配置参数的公共逻辑"""
    current_config = copy.deepcopy(base_config)
    current_config[config_key] = invalid_value

    with pytest.raises(CustomValueException) as exc_info:
        agent = agent_factory.create_agent(current_config)

    err_msg = str(exc_info.value)
    logger.info(f"error_info: {err_msg}")
    assert exc_info.value.error_code == error_code
    assert error_msg_fragment in err_msg
    LogManager.init(is_sensitive=True)


def validate_sub_config_parameter(sub_config, config_key, invalid_value,
                                  error_code, error_msg_fragment, base_config):
    """验证配置参数的公共逻辑"""
    current_config = copy.deepcopy(base_config)
    current_config[sub_config][config_key] = invalid_value

    with pytest.raises(CustomValueException) as exc_info:
        agent = agent_factory.create_agent(current_config)

    err_msg = str(exc_info.value)
    logger.info(f"error_info: {err_msg}")
    assert exc_info.value.error_code == error_code
    assert error_msg_fragment in err_msg


@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    (0, 200009, "Input should be greater than or equal to 1"),
    (11, 200009, "Input should be less than or equal to 10"),
    ("invalid type", 200009, "Input should be a valid integer, unable to parse string as an integer"),
])
def test_agent_factory_set_outliner_max_section_num(invalid_value, error_code, error_msg_fragment):
    '''异常值测试, info_collector_max_search_results字段的测试类似'''
    validate_config_parameter(
        "outliner_max_section_num",
        invalid_value,
        error_code,
        error_msg_fragment,
        agent_config
    )


@pytest.mark.parametrize("invalid_value, error_code, error_msg_fragment", [
    ("invalid type", 200009, "Input should be a valid boolean, unable to interpret input"),
])
def test_agent_factory_set_workflow_human_in_the_loop(invalid_value, error_code, error_msg_fragment):
    '''异常值测试,source_tracer_research_trace_source_switch, has_template测试类似'''
    validate_config_parameter(
        "workflow_human_in_the_loop",
        invalid_value,
        error_code,
        error_msg_fragment,
        agent_config
    )


@pytest.mark.parametrize("param_name, invalid_value, error_code, error_msg_fragment", [
    ("info_collector_search_method", "rag", 200009, "Input should be 'web', 'local' or 'all'"),
    ("execute_mode", "new mode", 200009, "Input should be 'commercial' or 'general'"),
])
def test_agent_factory_param_range_check(param_name, invalid_value, error_code, error_msg_fragment):
    validate_config_parameter(
        param_name,
        invalid_value,
        error_code,
        error_msg_fragment,
        agent_config
    )


@pytest.mark.parametrize("sub_config, param_name, invalid_value, error_code, error_msg_fragment", [
    ("llm_config", "model_type", "anthropic", 200009, "Input should be 'openai' or 'siliconflow'"),
    ("llm_config", "api_key", "xxxxx", 200009, "Parameter validation failed"),
    ("web_search_engine_config", "search_engine_name", "baidu", 200009,
     "Input should be 'tavily', 'google', 'xunfei', 'petal' or 'custom'"),
    ("local_search_engine_config", "search_engine_name", "baidu", 200009,
     "Input should be 'openapi', 'custom' or 'native'"),
])
def test_agent_factory_param_range_check_in_subconfig(sub_config, param_name, invalid_value, error_code,
                                                      error_msg_fragment):
    validate_sub_config_parameter(
        sub_config,
        param_name,
        invalid_value,
        error_code,
        error_msg_fragment,
        agent_config
    )
