# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""只读报告进度采集，不参与任务取消或 checkpoint 管理。"""

import asyncio
import inspect
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Literal

from openjiuwen_deepsearch.framework.openjiuwen.agent.progress_summary import ProcessingSection, ProgressSummary
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import cancel_context, report_progress_context

logger = logging.getLogger(__name__)
ProgressStatus = Literal["running", "waiting_user_input", "cancelling"]
# 仅供采集观察使用，子任务继承完整业务标识；不修改采集输入、输出或 checkpoint。
_collector_record_identity: ContextVar[tuple[str, str, str] | None] = ContextVar(
    "collector_record_identity", default=None,
)


@contextmanager
def collector_progress_scope(section_id: str, plan_id: str, step_id: str) -> Iterator[None]:
    """为作用域内创建的采集任务提供完整业务标识。

    Args:
        section_id: 章节标识。
        plan_id: 完整计划标识。
        step_id: 完整步骤标识。

    Yields:
        无返回值；作用域退出后恢复调用方上下文，已创建任务保留各自的标识。
    """
    token = _collector_record_identity.set((section_id, plan_id, step_id))
    try:
        yield
    finally:
        _collector_record_identity.reset(token)


@dataclass(frozen=True)
class ProgressSnapshot:
    """对外返回的不可变进度数据。

    Attributes:
        conversation_id: 报告会话标识。
        status: 执行、等待用户输入或取消收尾状态。
        node_id: 最近进入的主图节点，尚未进入时为 None。
        updated_at: 最近节点或状态变化的执行进程本地时间，ISO 8601 格式并保留时区偏移量。
        collected_info_count: 复用业务记录汇总的检索结果条目数，可能跨章节重复。
        total_sections: 当前大纲章节数，尚未生成大纲时为 None。
        completed_sections: 已产出有效内容的成功章节数。
        processing_sections: 最多五个处理中的章节元数据。
    """

    conversation_id: str
    status: ProgressStatus
    node_id: str | None
    updated_at: str
    collected_info_count: int = 0
    total_sections: int | None = None
    completed_sections: int = 0
    processing_sections: tuple[ProcessingSection, ...] = ()


class ResearchProgress:
    """保存一轮执行的观察数据，不提供停止或取消执行的方法。

    Attributes:
        conversation_id: 本轮报告的会话标识。
        workflow_id: 本轮主图标识，用于排除章节子图节点。
    """

    def __init__(self, conversation_id: str, workflow_id: str | None = None):
        """创建本轮进度，初始节点为空。

        Args:
            conversation_id: 会话标识。
            workflow_id: 主图标识，HTTP 登记后由 SDK 绑定。
        """
        self.conversation_id = conversation_id
        self.workflow_id = workflow_id
        self._lock = threading.RLock()
        self._status: ProgressStatus = "running"
        self._node_id: str | None = None
        # 与业务日志的本地时区一致，同时保留偏移量以支持跨 worker 比较时间。
        self._updated_at = datetime.now(timezone.utc).astimezone().isoformat()
        self._finished = False
        self.terminal_seen = False
        self._summary = ProgressSummary()

    def snapshot(self) -> ProgressSnapshot | None:
        """读取活跃进度，结束后返回 None。

        Returns:
            当前不可变快照或 None。
        """
        with self._lock:
            if self._finished:
                return None
            return ProgressSnapshot(
                self.conversation_id, self._status, self._node_id, self._updated_at,
                *self._summary.snapshot(),
            )

    def update_summary(self, operation: str, *args, **kwargs) -> None:
        """在锁内更新轻量汇总，不改变原有节点和状态更新时间。

        Args:
            operation: 内部汇总操作名。
            args: 操作的位置参数。
            kwargs: 操作的关键字参数。
        """
        with self._lock:
            if not self._finished:
                getattr(self._summary, operation)(*args, **kwargs)

    def enter_node(self, node_id: str) -> None:
        """记录主图节点，不改变取消收尾状态。

        Args:
            node_id: 主图中的原始节点名称。
        """
        with self._lock:
            if not self._finished and self._node_id != node_id:
                self._node_id = node_id
                self._updated_at = datetime.now(timezone.utc).astimezone().isoformat()

    def mark_cancelling(self) -> None:
        """观察到原有 cancel 请求后更新状态，不执行取消操作。"""
        with self._lock:
            if not self._finished and self._status != "cancelling":
                self._status = "cancelling"
                self._updated_at = datetime.now(timezone.utc).astimezone().isoformat()

    def observe(self, payload: object) -> None:
        """从原有输出事件更新交互状态，不修改或拦截输出。

        Args:
            payload: SDK 输出的 JSON 字符串。
        """
        if not isinstance(payload, str):
            return
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict):
            return
        event = data.get("event")
        if not isinstance(event, str):
            return
        with self._lock:
            if self._finished:
                return
            if event == "waiting_user_input" and self._status != "cancelling":
                self._status = "waiting_user_input"
                self._updated_at = datetime.now(timezone.utc).astimezone().isoformat()
            if event in {"error", "cancel"} or (
                event == "summary_response" and data.get("content") == "ALL END"
            ):
                self.terminal_seen = True

    def finish(self) -> None:
        """移除本轮可查询状态，不触碰执行任务或 checkpoint。"""
        with self._lock:
            self._finished = True


class ReportProgressMixin:
    """为报告 Agent 提供进度索引和只读 SDK 查询。"""

    def _init_report_progress(self) -> None:
        """初始化当前 Agent 的进度索引与锁。"""
        self._progress_runs: dict[str, ResearchProgress] = {}
        self._progress_lock = threading.RLock()

    def register_progress(self, progress: ResearchProgress) -> None:
        """供运行观察器登记当前轮次，锁和索引由 Mixin 统一管理。

        Args:
            progress: 当前执行轮次的进度对象。
        """
        with self._progress_lock:
            self._progress_runs[progress.conversation_id] = progress

    def finish_progress(self, progress: ResearchProgress) -> None:
        """结束指定轮次，并在索引仍属于该轮次时移除记录。

        Args:
            progress: 需要结束的进度对象，可能已被同会话的新轮次替换。
        """
        progress.finish()
        with self._progress_lock:
            # 旧轮次收尾不得移除同会话中已经登记的新轮次。
            if self._progress_runs.get(progress.conversation_id) is progress:
                self._progress_runs.pop(progress.conversation_id, None)

    def get_progress(self, conversation_id: str) -> ProgressSnapshot | None:
        """读取当前 Agent 所属报告的进度。

        Args:
            conversation_id: 报告会话标识。

        Returns:
            活跃进度；尚未开始、已结束或不属于此 Agent 时为 None。
        """
        with self._progress_lock:
            progress = self._progress_runs.get(conversation_id)
            return progress.snapshot() if progress is not None else None


def observe_report_progress(run):
    """为原运行生成器添加观察逻辑，保留参数和输出契约。

    Args:
        run: 报告 Agent 原有的异步生成器方法。

    Returns:
        保留原签名的异步生成器包装方法。
    """
    signature = inspect.signature(run)

    @wraps(run)
    async def observed(self, *args, **kwargs):
        """转发原始输出并维护本轮进度。

        Args:
            self: 报告 Agent。
            args: 原运行方法的位置参数。
            kwargs: 原运行方法的关键字参数。

        Yields:
            原运行方法产生的消息，不修改消息内容和顺序。
        """
        bound = signature.bind(self, *args, **kwargs)
        conversation_id = bound.arguments.get("conversation_id")
        stream = run(self, *args, **kwargs)
        # 参数是否合法仍由原 run 校验，不以进度采集替换原来的错误响应。
        if not isinstance(conversation_id, str) or not conversation_id:
            async for payload in stream:
                yield payload
            return
        inherited = report_progress_context.get()
        progress = inherited if (
            isinstance(inherited, ResearchProgress) and inherited.conversation_id == conversation_id
            and inherited.workflow_id is None
        ) else ResearchProgress(conversation_id)
        progress.workflow_id = self.research_name
        self.register_progress(progress)
        token = report_progress_context.set(progress)
        failed = False
        cancelled = False
        try:
            async for payload in stream:
                progress.observe(payload)
                yield payload
        except asyncio.CancelledError:
            # 等待事件已送达后，资源关闭仍可能被传输断连取消；这不代表业务终结。
            cancelled = True
            raise
        except Exception:
            failed = True
            raise
        finally:
            closed = False
            try:
                await stream.aclose()
                closed = True
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception:
                failed = True
                raise
            finally:
                snapshot = progress.snapshot()
                cancel_event = cancel_context.get()
                keep_waiting = (
                    snapshot is not None and snapshot.status == "waiting_user_input"
                    and not failed and (closed or cancelled) and not progress.terminal_seen
                    and not (cancel_event is not None and cancel_event.is_set())
                )
                if not keep_waiting:
                    self.finish_progress(progress)
                report_progress_context.reset(token)

    return observed


def record_main_node(session) -> None:
    """采集主图入口，章节子图和未启用观察的任务保持原有行为。

    Args:
        session: 当前节点的框架会话。
    """
    progress = report_progress_context.get()
    if not isinstance(progress, ResearchProgress):
        return
    try:
        if session.get_workflow_id() == progress.workflow_id:
            node_id = session.get_component_id()
            if isinstance(node_id, str):
                progress.enter_node(node_id)
            record_progress_state(session)
    except Exception:
        # 观察失败不能使研究节点失败；不记录会话或模型参数，避免泄漏配置。
        logger.warning("Could not collect report node progress")


def update_progress_summary(operation: str, *args, **kwargs) -> None:
    """尽力更新当前报告汇总，观察失败不影响业务执行。

    Args:
        operation: 内部汇总方法名。
        args: 已有业务记录或章节标识。
        kwargs: 汇总方法的关键字参数。
    """
    progress = report_progress_context.get()
    if isinstance(progress, ResearchProgress):
        try:
            progress.update_summary(operation, *args, **kwargs)
        except Exception:
            logger.warning("Could not collect report summary")


def record_progress_state(session) -> None:
    """采集节点已写回的业务记录，主图入口同时用于恢复既有汇总。

    Args:
        session: 当前节点会话。
    """
    progress = report_progress_context.get()
    if not isinstance(progress, ResearchProgress):
        return
    try:
        if session.get_workflow_id() == progress.workflow_id:
            update_progress_summary("restore_main", session.get_global_state("search_context"))
        else:
            update_progress_summary(
                "record_collector", session.get_global_state("collector_context"),
                identity=_collector_record_identity.get(),
            )
    except Exception:
        logger.warning("Could not read report summary records")


def observe_section_progress(run):
    """观察专业版单章处理，依赖模式研究结束后仍等待写作完成。

    Args:
        run: 原章节子图执行方法。

    Returns:
        原样返回章节结果的观察方法。
    """
    @wraps(run)
    async def observed(self, workflow_session, sub_workflow, input_state, *, reasoning_only=False):
        """登记单章开始、成功和异常，不改变任务调度。

        Args:
            self: 原编辑团队节点。
            workflow_session: 主图会话。
            sub_workflow: 原章节子图。
            input_state: 原章节输入。
            reasoning_only: 是否仅执行依赖模式的研究子图。

        Returns:
            原章节子图结果。
        """
        if not isinstance(report_progress_context.get(), ResearchProgress):
            return await run(self, workflow_session, sub_workflow, input_state)
        section_id = str(input_state.get("section_idx", ""))
        # 并行模式沿用业务已有的章节重编号，任务实际启动时再同步最终大纲。
        update_progress_summary("set_outline", input_state.get("current_outline"))
        update_progress_summary("start_section", section_id)
        finished = False
        try:
            result = await run(self, workflow_session, sub_workflow, input_state)
            if not reasoning_only:
                content = result.get("sub_report_content") if isinstance(result, dict) else None
                text = getattr(content, "sub_report_content_text", "")
                if isinstance(content, dict):
                    text = content.get("sub_report_content_text", "")
                success = isinstance(text, str) and bool(text.strip())
                update_progress_summary("finish_section", section_id, success)
            finished = True
            return result
        finally:
            if not finished:
                update_progress_summary("finish_section", section_id, False)

    return observed
