# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import os
from pathlib import Path
import tomllib

from pydantic import ValidationError

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import AgentConfig, Config
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.config.search_mode import SearchMode
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import (
    DeepresearchAgent,
    DeepresearchDependencyAgent,
    DeepresearchIntentHybridAgent,
    DeepSearchAgent,
    SimpleReactSearchAgent,
)
from openjiuwen_deepsearch.utils.validation_utils.field_validation import validate_agent_required_field
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

os.environ["WORKFLOW_EXECUTE_TIMEOUT"] = str(Config().service_config.workflow_execution_timeout)


class AgentFactory:
    """
    Agent factory class to create different types of agents based on the configuration.
    """

    _version: str | None = None

    @classmethod
    def _get_version(cls) -> str:
        if cls._version is not None:
            return cls._version
        # 优先使用 importlib.metadata（pip 安装后可用）
        try:
            from importlib.metadata import version
            cls._version = version("openjiuwen-deepsearch")
            return cls._version
        except Exception as e:
            logger.debug("importlib.metadata unavailable, falling back to pyproject.toml: %s", e)
        # 回退：从源码目录的 pyproject.toml 读取（开发模式）
        try:
            _pyproject = Path(__file__).resolve().parent.parent.parent.parent.parent / "pyproject.toml"
            with open(_pyproject, "rb") as f:
                cls._version = tomllib.load(f)["project"]["version"]
        except Exception as e:
            logger.warning("Failed to read SDK version from importlib.metadata and pyproject.toml: %s", e)
            cls._version = "unknown"
        return cls._version

    def __init__(self):
        self.agent_map = {
            ExecutionMethod.PARALLEL.value: DeepresearchAgent,
            ExecutionMethod.DEPENDENCY_DRIVING.value: DeepresearchDependencyAgent,
            ExecutionMethod.HYBRID.value: DeepresearchIntentHybridAgent,
            SearchMode.SEARCH.value: DeepSearchAgent,
            SearchMode.REACT.value: SimpleReactSearchAgent,
        }

    def create_agent(
        self, agent_config: dict
    ) -> (
        DeepresearchAgent
        | DeepresearchDependencyAgent
        | DeepresearchIntentHybridAgent
        | DeepSearchAgent
        | SimpleReactSearchAgent
    ):
        """
        Create an agent based on the provided configuration.

        Args:
            agent_config (dict): Configuration dictionary for the agent.
        Returns:
            An instance of the appropriate agent class.
        """
        validate_agent_required_field(agent_config)
        try:
            candidate_config = AgentConfig.model_validate(agent_config)
            agent_config = candidate_config.model_dump()
        except ValidationError as e:
            if LogManager.is_sensitive():
                raise CustomValueException(
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT.code,
                    StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR_NO_PRINT.errmsg,
                ) from e
            raise CustomValueException(
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.code,
                StatusCode.PARAM_CHECK_ERROR_REQUEST_PARAM_ERROR.errmsg.format(e=str(e)),
            ) from e
        search_mode = agent_config.get("search_mode", None)

        if search_mode not in {m.value for m in SearchMode}:
            raise CustomValueException(
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.code,
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.errmsg.format(
                    config=f"execution agent not found: {search_mode}"
                ),
            )
        if search_mode == SearchMode.RESEARCH.value:
            execution_method = agent_config.get("execution_method", ExecutionMethod.PARALLEL.value)
            if execution_method not in {m.value for m in ExecutionMethod}:
                raise CustomValueException(
                    StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.code,
                    StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.errmsg.format(
                        config=f"execution agent not found: {execution_method}"
                    ),
                )
            agent_class = self.agent_map.get(execution_method)
        else:
            agent_class = self.agent_map.get(search_mode)

        if agent_class is None:
            raise CustomValueException(
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.code,
                StatusCode.WORKFLOW_TYPE_NOT_EXIST_ERROR.errmsg.format(
                    config=f"execution agent not found for search_mode={search_mode}"
                ),
            )

        agent = agent_class()
        logger.info(
            "DeepSearch SDK version: %s | agent class=%s, research_name=%s, search_mode=%s, execution_method=%s",
            self._get_version(),
            agent.__class__.__name__,
            getattr(agent, "research_name", ""),
            search_mode,
            agent_config.get("execution_method", ""),
        )
        return agent
