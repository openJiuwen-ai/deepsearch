# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
import os
from typing import Any, Dict, Optional

from openjiuwen.core.session.checkpointer import CheckpointerFactory
from sqlalchemy.orm import Session

from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from server.core.database import get_db
from server.core.manager.model_manager.utils import SecurityUtils
from server.deepsearch.common.exception.exceptions import (
    ReportTemplateNotFoundException,
    SearchEngineConfigException,
    WebSearchEngineConfigGetException,
    WebSearchEngineNotFoundException, LocalSearchEngineConfigGetException,
)
from server.deepsearch.core.manager.repositories.report_template_repository import ReportTemplateRepository
from server.deepsearch.core.manager.repositories.web_search_engine_repository import \
    WebSearchEngineRepository
from server.schemas.deepsearch_run import DeepSearchRequest, WebSearchConfig, LocalSearchConfig

logger = logging.getLogger(__name__)


class DeepSearchAgentManager:
    """
    管理 DeepSearchAgent 的配置构建、缓存与实例化。
    支持按会话级别缓存 Agent 实例，避免重复初始化。
    """

    def __init__(self, agent_factory: Optional[AgentFactory] = None):
        self._agent_factory = agent_factory or AgentFactory()
        # 缓存格式: {search_mode:execution_method: agent_instance}
        # Agent 本身为工作流定义容器，可跨会话复用。
        self._agent_cache: Dict[str, Any] = {}
        self._security_utils = SecurityUtils()

    @staticmethod
    def _create_vector_store_param(kb_id: str):
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_token = os.getenv("MILVUS_TOKEN", None)

        # 组合 Milvus URI (格式: http://host:port 或 tcp://host:port)
        # 默认使用 http:// 协议
        milvus_uri = f"http://{milvus_host}:{milvus_port}"

        return {
            "collection_name": f"ds_kb_{kb_id}_chunks",
            "uri": milvus_uri,
            "token": milvus_token
        }

    def get_or_create_agent(self, request: DeepSearchRequest, db: Session) -> Any:
        """
        根据请求获取或创建 DeepSearchAgent 实例。
        
        Args:
            request: DeepSearch 请求对象
            db: Session 数据库会话对象
            
        Returns:
            Agent 实例
        """
        full_config = self.build_agent_config(request, db)
        execution_method = full_config.get("execution_method", "parallel")
        search_mode = full_config.get("search_mode", "research")
        cache_key = f"{search_mode}:{execution_method}"

        # 按搜索模式+执行方式缓存 agent，避免跨模式误复用
        if cache_key in self._agent_cache:
            return self._agent_cache[cache_key]

        agent = self._agent_factory.create_agent(full_config)

        self._agent_cache[cache_key] = agent
        return agent

    async def cleanup_session_cache(self, space_id: str, conversation_id: str):
        """清理会话状态（由 checkpointer 管理），agent 缓存不按会话删除。"""
        del space_id  # keep signature compatible with existing caller
        try:
            checkpointer = CheckpointerFactory.get_checkpointer()
            if checkpointer:
                # conversation_id 即 openjiuwen 的 session_id
                # release 内部会清理 workflow/agent 相关状态。
                release_result = checkpointer.release(conversation_id)
                if hasattr(release_result, "__await__"):
                    await release_result
                logger.info("Requested checkpointer release for session: %s", conversation_id)
        except Exception as e:
            logger.warning("Failed to release checkpointer session %s: %s", conversation_id, str(e))

    def build_agent_config(self, request: DeepSearchRequest, db: Session):
        """构建完整的 Agent 配置字典"""
        space_id = request.space_id

        llm_config = request.llm_config
        if hasattr(llm_config, "model_dump"):
            llm_config = llm_config.model_dump()
        if not request.web_search_config and request.info_collector_search_method != "local":
            raise SearchEngineConfigException("web_search_config must be provided.")
        if not request.local_search_config and request.info_collector_search_method != "web":
            raise SearchEngineConfigException("local_search_config must be provided.")

        has_template = False
        template_id = request.template_id
        if isinstance(template_id, int) and template_id > 0:
            has_template = True

        res = {
            "search_mode": request.search_mode,
            "execute_mode": "commercial",
            "execution_method": request.execution_method,
            "workflow_human_in_the_loop": request.workflow_human_in_the_loop,
            "outliner_max_section_num": request.outliner_max_section_num,
            "source_tracer_research_trace_source_switch": request.source_tracer_research_trace_source_switch,
            "source_tracer_infer_switch": request.source_tracer_infer_switch,
            "info_collector_search_method": request.info_collector_search_method,
            "llm_config": dict(general=llm_config) if "model_name" in llm_config else llm_config,
            "has_template": has_template,
            # 大纲交互配置
            "outline_interaction_enabled": request.outline_interaction_enabled,
            "outline_interaction_max_rounds": request.outline_interaction_max_rounds,
            "web_search_max_qps": request.web_search_max_qps,
            "user_feedback_processor_enable": request.user_feedback_processor_enable,
            "user_feedback_processor_max_interactions": request.user_feedback_processor_max_interactions,
        }
        if request.web_search_config:
            res["web_search_engine_config"] = self._load_web_search_config(
                space_id, request.web_search_config, db
            )
        if request.local_search_config:
            res["local_search_engine_config"] = self._load_local_search_config(
                space_id, request.local_search_config, db
            )
        return res

    def _load_web_search_config(self, space_id: str, web_search_config: WebSearchConfig, db: Session) -> Dict[str, Any]:
        try:
            repo = WebSearchEngineRepository(db)
            config_id = web_search_config.web_search_config_id
            detail = repo.get_engine_detail_by_id(space_id, config_id)
            if not detail:
                raise WebSearchEngineNotFoundException(
                    f"Web search engine ID {config_id} not found under space {space_id}."
                )
            config = {
                "search_engine_name": detail.search_engine_name,
                "search_api_key": bytearray(detail.search_api_key, encoding="utf-8"),
                "search_url": detail.search_url,
                "max_web_search_results": web_search_config.max_web_search_results,
                "extension": {},
            }
            logger.info("Built web search config for ID: %s", config_id)
            return config
        except Exception as e:
            logger.error("Failed to load web search config: %s", str(e))
            raise WebSearchEngineConfigGetException(f"Failed to build config: {str(e)}") from e

    def _load_local_search_config(self, space_id: str, local_search_config: LocalSearchConfig, db: Session) -> Dict[
        str, Any]:
        """从请求中的 LocalSearchConfig 构建本地搜索配置"""
        try:
            if local_search_config.embed_model_config is None:
                raise SearchEngineConfigException(
                    "local_search_config.embed_model_config is required when using local search."
                )
            em = local_search_config.embed_model_config
            api_key = em.api_key
            if not isinstance(api_key, (bytes, bytearray)):
                api_key = bytearray(str(api_key).encode("utf-8"))
            else:
                api_key = bytearray(api_key)
            embed_model_config_dict = {
                "model_name": em.model_name,
                "api_key": api_key,
                "base_url": em.base_url,
                "max_batch_size": em.max_batch_size,
            }

            kb_configs = []
            for kb_id in local_search_config.local_search_config_ids:
                kb_configs.append({
                    "id": kb_id,
                    "embed_model_config": embed_model_config_dict,
                    "vector_store": self._create_vector_store_param(kb_id),
                    "index_type": "vector"
                })

            return {
                "search_engine_name": "native",
                "max_local_search_results": local_search_config.max_local_search_results,
                "recall_threshold": local_search_config.recall_threshold,
                "knowledge_base_configs": kb_configs
            }
        except SearchEngineConfigException:
            raise
        except Exception as e:
            logger.error("Failed to load local search config: %s", str(e))
            raise LocalSearchEngineConfigGetException(f"Failed to build config: {str(e)}") from e

    def load_template_content(self, space_id: str, template_id: int) -> Dict[str, Any]:
        try:
            db = next(get_db())
            repo = ReportTemplateRepository(db)
            template = repo.get_by_id(space_id, template_id)
            if not template:
                logger.info("Report template ID %s not found under space %s.", template_id, space_id)
                raise ReportTemplateNotFoundException(
                    f"Report template ID {template_id} not found under space {space_id}."
                )
            return template.template_content
        except Exception as e:
            logger.error("Failed to load template content: %s", str(e))
            raise ReportTemplateNotFoundException(f"Failed to load report template: {str(e)}") from e
