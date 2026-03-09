# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import cancel_context
from server.core.cancel_bus import publish_remote_cancel, register_cancel_handler
from server.core.database import get_db
from server.deepsearch.core.manager.agent import DeepSearchAgentManager
from server.schemas.deepsearch_run import DeepSearchRequest

logger = logging.getLogger(__name__)
run_router = APIRouter()
agent_manager = DeepSearchAgentManager()

# 以 space_id:conversation_id 为 key 跟踪当前进程内的运行中任务及其取消事件。
_running_tasks: Dict[str, asyncio.Task] = {}
_cancel_events: Dict[str, asyncio.Event] = {}
_cancel_event_timestamps: Dict[str, float] = {}
_running_agents: Dict[str, object] = {}
_QUEUE_DONE = object()
# 按容量清理 _cancel_events：达到最大容量后剔除最久未用的项，避免内存泄漏
_CANCEL_EVENT_MAX_SIZE = 10
_cleanup_lock = asyncio.Lock()
# HITL 时 producer 等待 cancel 或「继续请求」再结束流，key 同 task_key
_resume_requested_events: Dict[str, asyncio.Event] = {}


def _clear_cancel_state(task_key: str) -> None:
    """清理与取消/恢复相关的全局字典项，避免泄漏。与 _cancel_events 同步维护。"""
    _cancel_events.pop(task_key, None)
    _cancel_event_timestamps.pop(task_key, None)
    _resume_requested_events.pop(task_key, None)


def _clear_task_state(task_key: str) -> None:
    """清理任务相关全部全局字典项（含 cancel 与 running），避免泄漏。"""
    _clear_cancel_state(task_key)
    _running_tasks.pop(task_key, None)
    _running_agents.pop(task_key, None)


@dataclass
class StreamContext:
    """流式任务上下文，封装 _produce_stream 所需的参数。"""
    queue: asyncio.Queue
    agent: object
    run_kwargs: dict
    space_id: str
    conversation_id: str
    cancel_event: asyncio.Event
    resume_requested: asyncio.Event


def _build_cancel_message(conversation_id: str) -> str:
    payload = {
        "conversation_id": conversation_id,
        "agent": "system",
        "role": "assistant",
        "content": "CANCELLED",
        "message_type": "system",
        "event": "cancel"
    }
    return json.dumps(payload, ensure_ascii=False)


def _get_controller(agent) -> object | None:
    if agent is None:
        return None
    candidate = agent
    if hasattr(candidate, "agent"):
        candidate = getattr(candidate, "agent", None)
    return getattr(candidate, "controller", None)


async def _cancel_controller_tasks(controller, conversation_id: str):
    if not controller:
        return
    task_queue = getattr(controller, "task_queue", None)
    if task_queue:
        try:
            await task_queue.cancel_running_task(conversation_id)
        except Exception as e:
            logger.warning("Failed to cancel workflow task: %s", str(e))
    handler_lock = getattr(controller, "_handler_lock", None)
    handlers = getattr(controller, "_processing_handlers", None)
    if handler_lock and handlers:
        try:
            async with handler_lock:
                handler_task = handlers.get(conversation_id)
                if handler_task and not handler_task.done():
                    handler_task.cancel()
        except Exception as e:
            logger.warning("Failed to cancel handler task: %s", str(e))


async def _handle_remote_cancel(space_id: str, conversation_id: str):
    """
    处理来自 Redis 的远程取消指令，仅作用于本进程内已有的运行任务。
    即使 task 已完成，如果 cancel_event 存在，也要设置它，以便后续请求能检测到取消状态。

    会话生命周期约定（简化）：
    - RUNNING：正常流式执行中；
    - WAITING_USER_INPUT：工作流等待用户输入（不中断连接时不清理 checkpointer）；
    - CANCELLED：用户/系统显式取消，会话资源应被释放；
    - COMPLETED：正常 ALL END，随后在工作流内部或上层进行会话释放。
    """
    task_key = f"{space_id}:{conversation_id}"
    cancel_event = _cancel_events.get(task_key)
    task = _running_tasks.get(task_key)
    cached_agent = _running_agents.get(task_key)

    if not cancel_event and not task and not cached_agent:
        # 本进程并无对应任务，忽略
        logger.debug("No local state found for remote cancel %s, ignoring", task_key)
        return

    # 设置 cancel_event（即使 task 已完成，也要设置，以便后续请求能检测到取消状态）
    if cancel_event:
        cancel_event.set()
        logger.debug("Set cancel_event for task %s (from remote cancel)", task_key)

    # 取消正在运行的任务
    if task and not task.done():
        task.cancel()
        logger.debug("Cancelled running task for %s (from remote cancel)", task_key)

    # 取消 agent 的 controller tasks
    if cached_agent:
        controller = _get_controller(cached_agent)
        await _cancel_controller_tasks(controller, conversation_id)
        logger.debug("Cancelled controller tasks for %s (from remote cancel)", task_key)

    # 远程取消视为会话生命周期 TERMINATED，可安全请求释放会话级资源（幂等）。
    try:
        await agent_manager.cleanup_session_cache(space_id, conversation_id)
    except Exception as e:
        logger.warning(
            "Failed to cleanup session cache for remote cancel %s:%s, error: %s",
            space_id,
            conversation_id,
            str(e),
        )

    _clear_task_state(task_key)
    logger.info(
        "Handled remote cancel in current worker for %s:%s (has_cancel_event=%s, task_done=%s, has_agent=%s)",
        space_id,
        conversation_id,
        cancel_event is not None,
        task.done() if task else None,
        cached_agent is not None,
    )


async def _wrapped_agent_run(agent, run_kwargs, space_id: str, conversation_id: str,
                             cancel_event: asyncio.Event):
    """
    包装 agent.run()，在流式输出过程中注入取消检查与会话清理逻辑。

    职责：
    - 检测 cancel_event 状态，在取消时立即停止流并发送取消消息；
    - 跟踪 waiting_user_input 事件（用于 HITL 场景的状态保留）；
    - 在异常/取消时触发 controller 级别的任务停止；
    - 在非取消类异常时尝试清理 checkpointer 会话级资源。

    注意：
    - 会话级资源（checkpointer）的最终释放由取消路径（_handle_cancel_request /
      _handle_remote_cancel）或工作流自身控制，此处仅负责 controller 级别的停止。
    - 在 INPUT_REQUIRED 场景下，不会在此处触发 checkpointer 清理，以支持后续恢复。
    """
    token = cancel_context.set(cancel_event)
    agent_gen = agent.run(**run_kwargs)
    try:
        async for chunk in agent_gen:
            if cancel_event.is_set():
                # 真正的取消：通过 cancel API 或 cancel_event 被设置
                await _cancel_controller_tasks(
                    _get_controller(agent),
                    conversation_id
                )
                yield _build_cancel_message(conversation_id)
                break

            if isinstance(chunk, str):
                try:
                    data = json.loads(chunk)
                    if data.get("event") == "waiting_user_input":
                        logger.debug("Detected waiting_user_input event for session %s", conversation_id)
                except (json.JSONDecodeError, KeyError, AttributeError) as parse_err:
                    logger.debug(
                        "Failed to parse chunk as JSON for waiting_user_input check in _wrapped_agent_run: %s",
                        str(parse_err),
                    )

            yield chunk
    except asyncio.CancelledError:
        # 协程被取消，先停止 controller 任务，然后重新抛出以保持取消语义
        await _cancel_controller_tasks(_get_controller(agent), conversation_id)
        logger.debug("Agent run was cancelled for %s:%s", space_id, conversation_id)
        raise
    except Exception as e:
        logger.error("Error during agent streaming for %s:%s: %s", space_id, conversation_id, str(e))
        # 非取消类异常视为会话异常结束，尝试清理会话级资源（不影响 checkpointer 幂等性）
        try:
            await agent_manager.cleanup_session_cache(space_id, conversation_id)
        except Exception as cleanup_err:
            logger.warning(
                "Failed to cleanup session cache after agent error %s:%s, error: %s",
                space_id,
                conversation_id,
                str(cleanup_err),
            )
    finally:
        try:
            await agent_gen.aclose()
        except asyncio.CancelledError:
            # CancelledError 需要重新抛出以保持取消语义，让上层正确处理协程取消
            logger.debug("Agent generator close was cancelled for %s:%s", space_id, conversation_id)
            raise
        except Exception as e:
            logger.warning("Failed to close agent generator: %s", str(e))
        cancel_context.reset(token)


async def _produce_stream(ctx: StreamContext):
    """
    从 agent.run() 生成流式输出并推送到队列。

    Args:
        ctx: 流式任务上下文，包含队列、agent、运行参数、会话标识和取消事件。
    """
    cancelled_sent = False
    waiting_user_input = False
    try:
        async for chunk in _wrapped_agent_run(
            agent=ctx.agent,
            run_kwargs=ctx.run_kwargs,
            space_id=ctx.space_id,
            conversation_id=ctx.conversation_id,
            cancel_event=ctx.cancel_event,
        ):
            if isinstance(chunk, str):
                # 解析 JSON，稳健检测 cancel / waiting_user_input 事件
                try:
                    data = json.loads(chunk)
                    event = data.get("event")
                    if event == "cancel":
                        cancelled_sent = True
                    elif event == "waiting_user_input":
                        waiting_user_input = True
                        logger.debug(
                            "Detected waiting_user_input in _produce_stream for session %s",
                            ctx.conversation_id,
                        )
                except (json.JSONDecodeError, TypeError, AttributeError):
                    # 非 JSON 或结构异常，忽略，仅作为普通 chunk 透传
                    pass
            await ctx.queue.put(chunk)
        # HITL 时保持流不结束，等待取消或「继续」请求，以便进程 B 取消时能向进程 A 推送 CANCELLED
        if waiting_user_input:
            logger.debug(
                "Waiting for cancel or resume for session %s before closing stream.",
                ctx.conversation_id,
            )
            try:
                await asyncio.wait(
                    [ctx.cancel_event.wait(), ctx.resume_requested.wait()],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                logger.debug("CancelledError in HITL wait for session %s", ctx.conversation_id)
                raise
            if ctx.cancel_event.is_set():
                try:
                    await ctx.queue.put(_build_cancel_message(ctx.conversation_id))
                except Exception as put_err:
                    logger.debug(
                        "Failed to put cancel message after HITL wait for %s: %s",
                        ctx.conversation_id,
                        str(put_err),
                    )
    except asyncio.CancelledError:
        # 由上层（如取消接口或 HTTP 断连）驱动的协程取消，这里仅尽量补发一次取消消息，
        # 不再反向设置 cancel_event，避免将「连接中断」误认为业务层面的会话取消。
        if not cancelled_sent:
            try:
                await asyncio.shield(ctx.queue.put(_build_cancel_message(ctx.conversation_id)))
            except Exception as put_err:
                # 队列可能已关闭或消费者已断开，这是尽力而为的操作，失败不影响取消流程
                logger.debug(
                    "Failed to put cancel message to queue during CancelledError for %s: %s",
                    ctx.conversation_id,
                    str(put_err),
                )
        raise
    finally:
        try:
            # 无论是否等待用户输入，都需要向队列推送完成标记，
            # 以便 _consumer 能够退出循环并执行 finally 清理本地状态。
            await asyncio.shield(ctx.queue.put(_QUEUE_DONE))
            if waiting_user_input:
                logger.debug(
                    "QUEUE_DONE sent for session %s (after cancel/resume or HITL wait).",
                    ctx.conversation_id,
                )
        except Exception as queue_err:
            # 队列可能已关闭或消费者已断开，这是尽力而为的操作，失败不影响清理流程
            logger.debug(
                "Failed to put QUEUE_DONE to queue in _produce_stream finally for %s: %s",
                ctx.conversation_id,
                str(queue_err),
            )


# 注册跨进程取消回调，使当前进程在收到 Redis 取消指令时，
# 能够基于本地 _running_tasks/_cancel_events/_running_agents 执行真实取消。
register_cancel_handler(_handle_remote_cancel)


async def _handle_cancel_request(request: DeepSearchRequest) -> dict:
    """
    处理中断反馈为 cancel 的取消请求。

    兼容 in_memory / persistence / redis 三种模式：
    - 优先尝试取消本进程内的任务（设置 cancel_event、取消 task/controller）；
    - 如无本地活动任务，则在 redis 模式下通过取消总线进行跨进程通知。

    返回值说明：
    - status="cancelling": 本进程内存在活动任务，或存在 HITL 挂起状态（仅 cancel_event），
      已触发取消并清理会话资源；
    - status="forwarded": 本进程无活动任务，已通过 Redis 总线转发到其他 worker/实例；
    - status="no_active_task_or_forward_failed": 本进程无活动任务，且未能转发
      （通常发生在非 redis 模式，或 Redis 发布失败时）。
    """
    task_key = f"{request.space_id}:{request.conversation_id}"
    cancel_event = _cancel_events.get(task_key)
    task = _running_tasks.get(task_key)
    cached_agent = _running_agents.get(task_key)

    # 本地状态判定：
    # - has_local_state: 当前进程是否记录过该会话（包括仅有 cancel_event 的挂起会话）；
    # - has_active_local_task: 当前进程中是否存在正在运行的任务（task 未完成或仍持有 agent/controller）。
    has_local_state = bool(cancel_event or task or cached_agent)
    has_active_local_task = bool(
        (task is not None and not task.done()) or cached_agent
    )

    if cancel_event:
        cancel_event.set()
    if task and not task.done():
        task.cancel()
    if cached_agent:
        controller = _get_controller(cached_agent)
        await _cancel_controller_tasks(controller, request.conversation_id)

    # 没有本地运行中的任务（例如仅在本进程保留了 cancel_event，
    # 实际运行任务在其它 worker / 实例上），通过 Redis 取消总线尝试跨进程取消。
    if not has_active_local_task:
        logger.info(
            "No active local task for %s (has_local_state=%s), "
            "forwarding cancel via Redis bus if available.",
            task_key,
            has_local_state,
        )
        forwarded = await publish_remote_cancel(request.space_id, request.conversation_id)
        if forwarded:
            status = "forwarded"
            # 不调用 cleanup、不 pop cancel_event，由 _handle_remote_cancel 处理
        elif has_local_state:
            status = "cancelling"
            try:
                await agent_manager.cleanup_session_cache(request.space_id, request.conversation_id)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to cleanup session cache for HITL cancel %s, error: %s",
                    task_key,
                    str(cleanup_err),
                )
            # 保留 cancel_event，用户按 Enter 后 _canceled_consumer 会 pop
            _cancel_event_timestamps[task_key] = time.monotonic()
            _maybe_schedule_capacity_cleanup()
        else:
            status = "no_active_task_or_forward_failed"
        if not has_local_state:
            _clear_cancel_state(task_key)
        _running_tasks.pop(task_key, None)
        _running_agents.pop(task_key, None)
        return {
            "status": status,
            "space_id": request.space_id,
            "conversation_id": request.conversation_id,
        }

    logger.info(
        "Cancelling active local task for %s (interrupt_feedback=cancel).",
        task_key,
    )
    # 对于明确的取消请求，视为会话生命周期终止，尝试立即释放会话级状态（幂等）。
    try:
        await agent_manager.cleanup_session_cache(request.space_id, request.conversation_id)
    except Exception as cleanup_err:
        logger.warning(
            "Failed to cleanup session cache for local cancel %s, error: %s",
            task_key,
            str(cleanup_err),
        )
    _clear_task_state(task_key)
    return {
        "status": "cancelling",
        "space_id": request.space_id,
        "conversation_id": request.conversation_id,
    }


async def _cleanup_cancel_events_by_capacity():
    """当 _cancel_events 数量达到上限时，按时间戳剔除最久未用的项并 release checkpointer。
    被淘汰的会话若仍有 producer 在等待（HITL 连接未断），会先取消该任务再清理，避免任务泄漏。"""
    async with _cleanup_lock:
        n = len(_cancel_events)
        if n < _CANCEL_EVENT_MAX_SIZE:
            return
        # 按时间戳升序，先剔除最旧的，使数量低于 _CANCEL_EVENT_MAX_SIZE
        sorted_keys = sorted(
            _cancel_event_timestamps.keys(),
            key=lambda k: _cancel_event_timestamps[k],
        )
        num_evict = n - _CANCEL_EVENT_MAX_SIZE + 1
        to_evict = sorted_keys[:num_evict]
        for k in to_evict:
            task = _running_tasks.get(k)
            if task is not None and not task.done():
                task.cancel()
                logger.debug("Cancelled evicted producer task for %s (capacity limit)", k)
            _clear_task_state(k)
    for task_key in to_evict:
        try:
            parts = task_key.split(":", 1)
            if len(parts) != 2:
                continue
            space_id, conversation_id = parts[0], parts[1]
            await agent_manager.cleanup_session_cache(space_id, conversation_id)
            logger.debug("Cleaned up cancel_event for %s (capacity limit)", task_key)
        except Exception as e:
            logger.warning("Failed to cleanup cancel_event %s: %s", task_key, str(e))


def _maybe_schedule_capacity_cleanup():
    """若 _cancel_events 已达最大容量，调度一次按容量清理（不阻塞当前请求）。"""
    if len(_cancel_events) < _CANCEL_EVENT_MAX_SIZE:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_cleanup_cancel_events_by_capacity())
    except RuntimeError:
        pass


def _prepare_stream_context(
    request: DeepSearchRequest,
    db: Session,
) -> tuple[DeepSearchRequest, object, dict]:
    """
    规范化请求并构建 Agent / run_kwargs 等上下文信息。

    职责：
    - 转换 llm_config.api_key 为 bytearray 格式；
    - 验证并规范化 DeepSearchRequest；
    - 构建 agent_config 并获取/创建 Agent 实例；
    - 加载报告模板（如 template_id > 0）；
    - 组装 run_kwargs（message, conversation_id, report_template, agent_config）。

    注意：
    - 不处理 cancel_event / HITL 状态，相关逻辑在 _create_streaming_response 中完成。
    - 返回的 request 是经过 model_validate 的规范化对象。
    """
    if "general" in request.llm_config:
        for _, llm_config in request.llm_config.items():
            api_key = llm_config.get("api_key", "")
            if isinstance(api_key, str):
                llm_config["api_key"] = bytearray(api_key, encoding="utf-8")
    else:
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
            template_id,
        )

    run_kwargs = {
        "message": request.message,
        "conversation_id": request.conversation_id,
        "report_template": template_content,
        "agent_config": agent_config,
    }
    return request, agent, run_kwargs


def _create_streaming_response(
    request: DeepSearchRequest,
    agent: object,
    run_kwargs: dict,
) -> EventSourceResponse:
    """
    基于已有上下文创建 SSE 流式响应（包含 HITL / 取消状态处理）。

    职责：
    - 检查 existing_cancel_event 状态，如已取消则直接返回取消消息流；
    - 复用或创建 cancel_event，注册到全局 _cancel_events 字典；
    - 创建 producer_task（_produce_stream）和 consumer（_consumer）协程；
    - 维护 _running_tasks / _running_agents 字典，并在流结束时清理；
    - 在 waiting_user_input 场景下保留 cancel_event 以便后续请求复用。

    注意：
    - 假定 Agent 和 run_kwargs 已由 _prepare_stream_context 构建完成，不负责配置/模板加载。
    - 会话级资源（checkpointer）的清理由取消路径或工作流自身控制，此处仅管理本地状态字典。
    """
    task_key = f"{request.space_id}:{request.conversation_id}"

    # 检查是否已存在 cancel_event（例如 HITL 场景下的后续轮次）。
    existing_cancel_event = _cancel_events.get(task_key)
    logger.debug("Found existing cancel_event for %s: %r", task_key, existing_cancel_event)
    if existing_cancel_event and existing_cancel_event.is_set():
        # 任务已被取消，直接返回取消消息（防止前端继续拉取已终止会话）。
        logger.info(
            "Task %s was already cancelled (cancel_event.is_set=True), returning cancel message",
            task_key,
        )

        async def _canceled_consumer():
            try:
                yield _build_cancel_message(request.conversation_id)
            finally:
                _cancel_events.pop(task_key, None)
                _cancel_event_timestamps.pop(task_key, None)
                _resume_requested_events.pop(task_key, None)

        return EventSourceResponse(_canceled_consumer(), media_type="text/event-stream")

    # 复用现有的 cancel_event 或创建新的。若为「继续」请求（已有未 set 的 cancel_event），唤醒旧流结束。
    cancel_event = existing_cancel_event if existing_cancel_event else asyncio.Event()
    old_resume = _resume_requested_events.pop(task_key, None)
    if old_resume is not None:
        old_resume.set()
        logger.debug("Set resume_requested for task %s (continue request)", task_key)
    resume_requested = asyncio.Event()
    _resume_requested_events[task_key] = resume_requested

    queue: asyncio.Queue = asyncio.Queue()
    stream_ctx = StreamContext(
        queue=queue,
        agent=agent,
        run_kwargs=run_kwargs,
        space_id=request.space_id,
        conversation_id=request.conversation_id,
        cancel_event=cancel_event,
        resume_requested=resume_requested,
    )
    producer_task = asyncio.create_task(_produce_stream(stream_ctx))
    _running_tasks[task_key] = producer_task
    _cancel_events[task_key] = cancel_event
    _cancel_event_timestamps[task_key] = time.monotonic()
    _running_agents[task_key] = agent
    _maybe_schedule_capacity_cleanup()

    async def _consumer():
        waiting_user_input = False
        try:
            while True:
                chunk = await queue.get()
                if chunk is _QUEUE_DONE:
                    break
                # 检查是否是 waiting_user_input 事件
                if isinstance(chunk, str):
                    try:
                        data = json.loads(chunk)
                        if data.get("event") == "waiting_user_input":
                            waiting_user_input = True
                            logger.debug(
                                "Detected waiting_user_input in _consumer for session %s",
                                request.conversation_id,
                            )
                    except (json.JSONDecodeError, KeyError, AttributeError) as parse_err:
                        # 非 JSON 格式或结构异常，chunk 将作为普通字符串透传
                        logger.debug(
                            "Failed to parse chunk as JSON for waiting_user_input check in _consumer: %s",
                            str(parse_err),
                        )
                yield chunk
        except asyncio.CancelledError:
            # 消费者协程被取消，先取消 producer_task，然后重新抛出以保持取消语义
            if not producer_task.done():
                producer_task.cancel()
            logger.debug("Consumer was cancelled for session %s", request.conversation_id)
            raise
        finally:
            if not producer_task.done():
                producer_task.cancel()
            # 清理已完成的 producer_task 引用
            _running_tasks.pop(task_key, None)
            # 如果正在等待用户输入，保留 cancel_event 以便后续取消请求能检测到 HITL 状态
            # 但不保留 running_agents，因为后续请求可能在其他进程运行（分布式部署）
            if not waiting_user_input:
                _clear_cancel_state(task_key)
                _running_agents.pop(task_key, None)
                # HTTP 断开或流结束且未到 HITL：无 checkpoint 可恢复，立即 release 避免泄漏
                try:
                    await agent_manager.cleanup_session_cache(request.space_id, request.conversation_id)
                except Exception as cleanup_err:
                    logger.warning(
                        "Failed to cleanup session on stream end %s: %s",
                        task_key,
                        str(cleanup_err),
                    )
            else:
                logger.debug(
                    "Preserving cancel_event for waiting_user_input session %s "
                    "(running_agents cleaned for distributed deployment compatibility)",
                    request.conversation_id,
                )
                # HITL 场景：只保留 cancel_event，清理 running_agents
                # 这样取消请求会通过 Redis 总线转发到实际运行任务的进程
                _running_agents.pop(task_key, None)
                _cancel_event_timestamps[task_key] = time.monotonic()
                _maybe_schedule_capacity_cleanup()

    return EventSourceResponse(_consumer(), media_type="text/event-stream")


async def _start_deepsearch_stream(
    request: DeepSearchRequest,
    db: Session,
) -> EventSourceResponse:
    """
    启动一次新的 DeepSearch 流式任务（包括 HITL 场景）。
    """
    request, agent, run_kwargs = _prepare_stream_context(request, db)
    return _create_streaming_response(request, agent, run_kwargs)


@run_router.post("/")
async def run(
        request: DeepSearchRequest,
        db: Session = Depends(get_db)
):
    """
    进行深度研究入口。

    - interrupt_feedback == \"cancel\" 时，仅处理取消逻辑（返回 JSON）；
    - 否则启动或继续 SSE 流式任务（返回 EventSourceResponse）。
    """
    try:
        if request.interrupt_feedback == "cancel":
            return await _handle_cancel_request(request)
        return await _start_deepsearch_stream(request, db)
    except Exception as e:
        logger.error("Error during DeepSearch run: %s", str(e))
        raise HTTPException(status_code=500, detail=str(e))
