# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import os

from pydantic import ValidationError

from jiuwen_deepsearch.common.exception import CustomValueException
from jiuwen_deepsearch.common.status_code import StatusCode
from jiuwen_deepsearch.config.config import AgentConfig, Config
from jiuwen_deepsearch.config.method import ExecutionMethod
from jiuwen_deepsearch.framework.jiuwen.agent.workflow import DeepresearchAgent, DeepresearchDependencyAgent
from jiuwen_deepsearch.utils.validation_utils.field_validation import validate_agent_required_field
from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

os.environ["WORKFLOW_EXECUTE_TIMEOUT"] = str(Config().service_config.workflow_execution_timeout)


class AgentFactory:
    '''
    Agent factory class to create different types of agents based on the configuration.
    '''

    def __init__(self):
        self.agent_map = {
            ExecutionMethod.PARALLEL.value: DeepresearchAgent,
            ExecutionMethod.DEPENDENCY_DRIVING.value: DeepresearchDependencyAgent,
        }

    def create_agent(self, agent_config: dict):
        '''
        Create an agent based on the provided configuration.

        Args:
            agent_config (dict): Configuration dictionary for the agent.
        Returns:
            An instance of the appropriate agent class.
        '''
        validate_agent_required_field(agent_config)
        try:
            candidate_config = AgentConfig.model_validate(agent_config)
            agent_config = candidate_config.model_dump()
        except ValidationError as e:
            if LogManager.is_sensitive():
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT.errmsg)
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(e=str(e))
            ) from e

        execution_method = agent_config.get("execution_method", ExecutionMethod.PARALLEL.value)
        agent_class = self.agent_map.get(execution_method)
        if not agent_class:
            raise CustomValueException(
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.code,
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.errmsg.format(
                    config=f"execution method is {execution_method}"
                )
            )
        agent = agent_class()
        return agent
