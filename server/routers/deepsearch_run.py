# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from server.core.database import get_db
from server.deepsearch.core.manager.agent import DeepSearchAgentManager
from server.schemas.deepsearch_run import DeepSearchRequest

logger = logging.getLogger(__name__)
run_router = APIRouter()
agent_manager = DeepSearchAgentManager()


async def _wrapped_agent_run(agent, run_kwargs, space_id: str, conversation_id: str):
    """
    包装 agent.run()，异常时兜底触发会话清理。
    """
    try:
        async for chunk in agent.run(**run_kwargs):
            yield chunk
    except asyncio.CancelledError:
        logger.info("Agent streaming cancelled by client, cleaning up session: %s", conversation_id)
        await agent_manager.cleanup_session_cache(space_id, conversation_id)
        raise
    except Exception as e:
        logger.error("Error during agent streaming: %s", str(e))
        await agent_manager.cleanup_session_cache(space_id, conversation_id)
        raise


@run_router.post("/")
async def run(
        request: DeepSearchRequest,
        db: Session = Depends(get_db)
):
    """
    进行深度研究

    Args:
        request: 包含深度研究请求参数的对象。
        db: 数据库会话对象Session

    Returns:
        StreamingResponse: 返回流式响应，包含深度研究的结果。
    """
    try:
        api_key = request.llm_config.get("api_key", "")
        if isinstance(api_key, str):
            request.llm_config["api_key"] = bytearray(api_key, encoding="utf-8")
        request = DeepSearchRequest.model_validate(request)
        agent_config = agent_manager.build_agent_config(request, db)
        agent = agent_manager.get_or_create_agent(request, db)
        template_id = request.template_id
        template_content = ""
        if isinstance(template_id, int) and template_id > 0:
            template_content = agent_manager.load_template_content(
                request.space_id,
                template_id
            )

        run_kwargs = {
            "message": request.message,
            "conversation_id": request.conversation_id,
            "report_template": template_content,
            "agent_config": agent_config,
        }

        response_stream = _wrapped_agent_run(
            agent=agent,
            run_kwargs=run_kwargs,
            space_id=request.space_id,
            conversation_id=request.conversation_id
        )
        return EventSourceResponse(response_stream, media_type="text/event-stream")

    except Exception as e:
        logger.error("Error during DeepSearch run: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))
