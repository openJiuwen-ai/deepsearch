# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from fastapi import status
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
from server.local_retrieval.core.manager.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from server.schemas.deepsearch_run import DeepSearchRequest, WebSearchConfig, LocalSearchConfig, RuntimeApiToolRequest
from server.schemas.knowledge_base import KnowledgeBaseGet

logger = logging.getLogger(__name__)


class DeepSearchAgentManager:
    """
    管理 DeepSearchAgent 的配置构建、缓存与实例化。
    支持按会话级别缓存 Agent 实例，避免重复初始化。
    """

    def __init__(self, agent_factory: Optional[AgentFactory] = None):
        self._agent_factory = agent_factory or AgentFactory()
        # 缓存格式: {cache_key: agent_instance}；cache_key 由请求中影响 Agent 构建的字段派生
        #（含 space_id、本地知识库 ID 列表、联网引擎 ID 等），避免请求里配置变了，但缓存键没变，导致仍用旧 Agent的问题。
        self._agent_cache: Dict[str, Any] = {}
        # 记录会话到缓存键的映射，便于会话释放时定向驱逐 agent 缓存，避免长时间运行后缓存无限增长。
        self._session_cache_keys: Dict[str, set[str]] = {}
        self._security_utils = SecurityUtils()

    @staticmethod
    def _sanitize_tool_name(name: str, fallback: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "").strip())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if not normalized:
            normalized = fallback
        if normalized[0].isdigit():
            normalized = f"tool_{normalized}"
        return normalized

    @staticmethod
    def _normalize_http_method(method: int | str) -> str:
        mapping = {
            1: "get",
            2: "post",
            3: "put",
            4: "delete",
            "get": "get",
            "post": "post",
            "put": "put",
            "delete": "delete",
            "patch": "patch",
            "head": "head",
            "options": "options",
        }
        return mapping.get(str(method).lower() if isinstance(method, str) else method, "post")

    @staticmethod
    def _normalize_send_method(method: int | None) -> str:
        mapping = {
            0: "none",
            1: "header",
            2: "query",
            3: "body",
        }
        return mapping.get(method, "none")

    @staticmethod
    def _create_vector_store_param(kb_id: str):
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_token = os.getenv("MILVUS_TOKEN") or ""

        # 组合 Milvus URI (格式: http://host:port 或 tcp://host:port)
        # 默认使用 http:// 协议
        milvus_uri = f"http://{milvus_host}:{milvus_port}"

        return {
            "collection_name": f"ds_kb_{kb_id}_chunks",
            "uri": milvus_uri,
            "token": milvus_token
        }

    @staticmethod
    def _compute_agent_cache_key(request: DeepSearchRequest) -> str:
        """
        生成 Agent 缓存键。排除仅影响单次对话内容的字段（message、conversation_id），
        保留与 build_agent_config 相关的全部字段（含 space_id、local_search_config_ids、
        web_search_config_id、LLM 与各开关），保证换知识库/引擎后不会误复用旧 Agent。
        """
        payload = request.model_dump(exclude={"message", "interrupt_feedback"})
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_runtime_api_tool(cls, tool: RuntimeApiToolRequest, index: int) -> dict[str, Any]:
        sanitized_name = cls._sanitize_tool_name(tool.name, fallback=f"runtime_api_tool_{index}")
        if tool.response_params:
            logger.warning(
                "Runtime API tool '%s' provided response_params; they are preserved for compatibility but not "
                "used for response mapping yet. Prefer response_wrapper.",
                sanitized_name,
            )
        return {
            "tool_id": tool.tool_id,
            "name": sanitized_name,
            "description": tool.desc,
            "response_wrapper": tool.response_wrapper,
            "path": tool.path,
            "http_method": cls._normalize_http_method(tool.method),
            "base_url": tool.url,
            "plugin_id": tool.plugin_id,
            "plugin_version": tool.plugin_version or "",
            "request_params": [
                {
                    "name": param.name,
                    "description": param.desc,
                    "param_type": param.type,
                    "required": param.is_required,
                    "send_method": cls._normalize_send_method(param.method),
                    "is_runtime": param.is_runtime,
                    "default_value": param.value,
                    "priority": param.priority or 0,
                }
                for param in tool.request_params
            ],
            "response_params": [
                {
                    "name": param.name,
                    "description": param.desc,
                    "param_type": param.type,
                    "required": param.is_required,
                    "send_method": cls._normalize_send_method(param.method),
                    "is_runtime": param.is_runtime,
                    "default_value": param.value,
                    "priority": param.priority or 0,
                }
                for param in tool.response_params
            ],
            "headers": [
                {
                    "name": header.name,
                    "value": header.value,
                }
                for header in tool.headers
            ],
        }

    @classmethod
    def _build_api_tools_config(cls, tools: list[RuntimeApiToolRequest]) -> dict[str, Any]:
        normalized_tools = [cls._normalize_runtime_api_tool(tool, index) for index, tool in enumerate(tools, start=1)]
        # Shallow copy so planner vs collector config lists are not the same object (avoids accidental shared mutation).
        return {
            "query_understanding_tools": normalized_tools,
            "collector_tools": list(normalized_tools),
        }

    @staticmethod
    def _build_session_cache_scope(space_id: str, conversation_id: str) -> str:
        """构造会话级缓存作用域键。

        Args:
            space_id: 空间 ID。
            conversation_id: 会话 ID。

        Returns:
            str: 形如 ``<space_id>:<conversation_id>`` 的会话作用域键。
        """
        return f"{space_id}:{conversation_id}"

    def _track_cache_key_for_session(self, request: DeepSearchRequest, cache_key: str) -> None:
        """登记会话与缓存键映射关系。

        Args:
            request: DeepSearch 请求对象。
            cache_key: Agent 缓存键。

        Returns:
            None.
        """
        session_scope = self._build_session_cache_scope(request.space_id, request.conversation_id)
        self._session_cache_keys.setdefault(session_scope, set()).add(cache_key)

    def get_or_create_agent(
        self,
        request: DeepSearchRequest,
        db: Session,
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """根据请求获取或创建 DeepSearchAgent 实例。

        当调用方已经构建好 `agent_config` 时，直接复用该配置创建 Agent，
        避免在同一请求中重复查询搜索引擎/知识库配置并产生重复日志。

        Args:
            request: DeepSearch 请求对象。
            db: Session 数据库会话对象。
            agent_config: 可选的预构建 Agent 配置；未提供时由当前方法内部构建。

        Returns:
            Agent 实例。
        """
        cache_key = self._compute_agent_cache_key(request)

        if cache_key in self._agent_cache:
            cached_agent = self._agent_cache[cache_key]
            # 命中老缓存时补齐映射，兼容旧实例或热更新场景下索引缺失。
            self._track_cache_key_for_session(request, cache_key)
            logger.info(
                "Reusing cached agent conversation_id=%s class=%s research_name=%s",
                request.conversation_id,
                cached_agent.__class__.__name__,
                getattr(cached_agent, "research_name", ""),
            )
            return self._agent_cache[cache_key]

        if agent_config is None:
            agent_config = self.build_agent_config(request, db)

        agent = self._agent_factory.create_agent(agent_config)
        logger.info(
            "Created new agent conversation_id=%s class=%s research_name=%s execution_method=%s",
            request.conversation_id,
            agent.__class__.__name__,
            getattr(agent, "research_name", ""),
            request.execution_method,
        )

        self._agent_cache[cache_key] = agent
        self._track_cache_key_for_session(request, cache_key)
        return agent

    async def cleanup_session_cache(self, space_id: str, conversation_id: str):
        """清理会话状态并驱逐会话对应的 Agent 缓存。

        Args:
            space_id: 空间 ID。
            conversation_id: 会话 ID。

        Returns:
            None.
        """
        session_scope = self._build_session_cache_scope(space_id, conversation_id)
        cache_keys = self._session_cache_keys.pop(session_scope, set())
        evicted_count = 0
        for cache_key in cache_keys:
            if self._agent_cache.pop(cache_key, None) is not None:
                evicted_count += 1
        if evicted_count > 0:
            logger.info("Evicted %s agent cache entries for session: %s", evicted_count, session_scope)

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
            "source_tracer_generated_citation_switch": request.source_tracer_generated_citation_switch,
            "source_tracer_infer_switch": request.source_tracer_infer_switch,
            "info_collector_search_method": request.info_collector_search_method,
            "info_collector_webpage_enrich_enable": request.info_collector_webpage_enrich_enable,
            "llm_config": dict(general=llm_config) if "model_name" in llm_config else llm_config,
            "has_template": has_template,
            # 大纲交互配置
            "outline_interaction_enabled": request.outline_interaction_enabled,
            "outline_interaction_max_rounds": request.outline_interaction_max_rounds,
            "web_search_max_qps": request.web_search_max_qps,
            "user_feedback_processor_enable": request.user_feedback_processor_enable,
            "user_feedback_processor_max_interactions": request.user_feedback_processor_max_interactions,
            "stats_info_llm": request.stats_info_llm,
            # vlm迭代生成图配置
            "vlm_chart_generator_enable": request.vlm_chart_generator_enable,
            "vlm_chart_generator_max_iterations": request.vlm_chart_generator_max_iterations,
            # llm_timeout 配置
            "agent_llm_timeouts": request.agent_llm_timeouts,
        }
        if request.web_search_config:
            res["web_search_engine_config"] = self._load_web_search_config(
                space_id, request.web_search_config, db
            )
        if request.local_search_config:
            res["local_search_engine_config"] = self._load_local_search_config(
                space_id, request.local_search_config, db
            )
        if request.tools:
            res["api_tools_config"] = self._build_api_tools_config(request.tools)
        logger.info(
            "Built agent config conversation_id=%s execution_method=%s search_mode=%s "
            "outline_interaction_enabled=%s human_in_the_loop=%s",
            request.conversation_id,
            request.execution_method,
            request.search_mode,
            request.outline_interaction_enabled,
            request.workflow_human_in_the_loop,
        )
        return res

    @staticmethod
    def load_template_content(space_id: str, template_id: int) -> Dict[str, Any]:
        """加载并返回模板正文与元数据。"""
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

    @staticmethod
    def _load_web_search_config(space_id: str, web_search_config: WebSearchConfig, db: Session) -> Dict[str, Any]:
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
                "extension": detail.extension or {},
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
            kb_ids = local_search_config.local_search_config_ids or []
            normalized_ids: list[str] = []
            for kb_id in kb_ids:
                kid = kb_id.strip() if isinstance(kb_id, str) else str(kb_id)
                if kid:
                    normalized_ids.append(kid)

            if not normalized_ids:
                raise SearchEngineConfigException(
                    "local_search_config.local_search_config_ids must contain at least one knowledge base id."
                )

            repo = KnowledgeBaseRepository(db)
            kb_row: dict | None = None
            for kid in normalized_ids:
                get_result = repo.knowledge_base_get(
                    KnowledgeBaseGet(space_id=space_id, kb_id=kid)
                )
                if get_result.code != status.HTTP_200_OK or not get_result.data:
                    raise SearchEngineConfigException(
                        f"知识库 '{kid}' 不属于 space_id={space_id} 或不存在。"
                        "本地检索仅允许使用当前请求空间下的知识库。"
                    )
                kb_data = get_result.data
                # 二次校验：确保返回的知识库确实属于当前 space，防止跨空间访问
                if kb_data.get("space_id") != space_id:
                    raise SearchEngineConfigException(
                        f"知识库 '{kid}' 不属于 space_id={space_id}，禁止跨空间访问。"
                    )
                if kb_row is None:
                    kb_row = kb_data

            if kb_row is None:
                raise SearchEngineConfigException(
                    "未能加载本地检索知识库信息，请检查 local_search_config_ids。"
                )
            cfg = kb_row.get("config") or {}
            embed_src = cfg.get("embed_model_config")
            if not embed_src or not isinstance(embed_src, dict):
                raise SearchEngineConfigException(
                    f"知识库 {normalized_ids[0]} 的存储配置中缺少 embed_model_config。"
                )

            api_key_raw = embed_src.get("api_key") or ""
            if isinstance(api_key_raw, (bytes, bytearray)):
                api_key = bytearray(api_key_raw)
            else:
                api_key = bytearray(str(api_key_raw).encode("utf-8"))

            embed_model_config_dict = {
                "model_name": embed_src.get("model_name") or "",
                "api_key": api_key,
                "base_url": embed_src.get("base_url") or "",
                "max_batch_size": int(embed_src.get("max_batch_size", 1)),
                "timeout": int(embed_src.get("timeout", 60)),
                "max_retries": int(embed_src.get("max_retries", 3)),
            }

            kb_configs = []
            for kid in normalized_ids:
                kb_configs.append({
                    "id": kid,
                    "embed_model_config": embed_model_config_dict,
                    "vector_store": self._create_vector_store_param(kid),
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
