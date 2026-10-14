# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""deepresearch 优雅停止控制（路线 A）。

设计契约：
- 停止不走 asyncio task.cancel()：CancelledError 会让 agent-core 的
  post_workflow_execute 走「清除 checkpoint」分支（graph.py 外层
  except asyncio.CancelledError），导致执行进度全丢、续跑回退到上一个
  交互确认点。
- 停止表现为普通 Exception（StopRequested）传播：
  LLM 调用边界/节点边界 raise → pregel run_step 的 _save_state_on_error
  → graph._invoke except Exception → post_workflow_execute 的异常保存分支
  → checkpoint 落盘 → DeepresearchAgent.run 的 except 分支识别 StopRequested
  → 跳过 release_session/_release_checkpointer_session（保留 checkpoint）
  → emit stopped 终态流后正常退出进程。
- 恢复：同 conversation_id 再次 agent.run(interrupt_feedback="accepted")
  → workflow_controller 包装 InteractiveInput → pre_workflow_execute recover
  → pregel init() 命中 _is_resume（pending_node/channel_values 非空）
  → 被停止的节点（pending_node）重新调度执行，已完成节点不重跑。

停止标志是进程内全局单例：本模块只部署在 deepresearch 子进程内，
主 harness 进程的中断机制（permission_interrupt_cancel）零感知、零影响。
"""
import asyncio
import threading


# 停止标记:父进程(harness)据此识别 checkpoint 中的停止快照。该值同时
# 序列化进 PendingNode.exception(见 StopRequested.__reduce__),跨进程稳定。
STOP_REQUESTED_MARKER = "deepresearch task stopped by user"


class StopRequested(Exception):
    """用户请求停止 deepresearch 任务。

    必须是普通 Exception（而非 BaseException / asyncio.CancelledError），
    以确保 agent-core 走「异常保存 checkpoint」分支而非「取消清除」分支。
    """

    def __reduce__(self):
        # 父进程(jiuwenswarm venv)读 checkpointer.db 反序列化快照时可能
        # 未安装 openjiuwen_deepsearch 包:降级为携带停止标记的内置
        # RuntimeError,保证父进程 probe 始终能读到 pending_node 并识别
        # 停止快照。pregel 恢复只按 pending_node 节点名重调度,异常对象
        # 本身不参与续跑逻辑,降级无副作用。
        return (RuntimeError, (STOP_REQUESTED_MARKER,))


_stop_event = threading.Event()


def request_stop() -> None:
    """标记停止请求（幂等）。可从任意线程/协程调用。"""
    _stop_event.set()


def is_stop_requested() -> bool:
    return _stop_event.is_set()


def clear_stop() -> None:
    """清除停止标记。仅供测试复位使用。

    生产入口禁止调用（修复13）：runner 是单次运行进程，标志出厂即清；
    workflow.run() 入口若调用会擦除 boot 窗口（引导+checkpoint_restore）
    内到达的停止帧，导致停止信号丢失、30s 超时硬杀。见 workflow.py
    入口处的明令注释。
    """
    _stop_event.clear()


def raise_if_stop_requested() -> None:
    """在 LLM 调用/节点执行边界检查停止请求，命中则抛 StopRequested。"""
    if _stop_event.is_set():
        raise StopRequested(STOP_REQUESTED_MARKER)


async def gather_with_stop(*coros):
    """asyncio.gather 的停止/异常感知版本。

    与原生 gather 的差异：任一协程抛出异常时，原生 gather 立即向上抛出，
    其余协程成为孤儿任务继续在后台运行（可能在 checkpoint 落盘后继续写
    session 状态，污染恢复快照）。本实现先取消并等待全部剩余协程退出，
    再重抛首个异常。

    取消注入的 CancelledError 只作用于子图协程内部：子图
    CompiledGraph._invoke 以 is_main=False 执行，CancelledError 直接重抛、
    不触发任何 checkpoint 清除；主图快照由上层 run_step 的
    _save_state_on_error 统一保存。
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    if not tasks:
        return []
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def raise_if_stop_requested_in_results(results) -> None:
    """gather(return_exceptions=True) 结果分流的前置检查：命中 StopRequested
    立即重抛。

    return_exceptions=True 会把停止异常压平成结果列表中的普通项；若先落入
    通用 Exception 分流（置空内容/continue/降级返回），停止被吞成局部失败
    或假完成，图引擎误判正常完成而清快照，续跑只能从头重跑。任何
    return_exceptions=True 的 gather 在消费结果前必须先过本检查；无法整体
    改用 gather_with_stop（需要保留其余协程结果的分流语义）的场景同理。
    """
    for result in results:
        if isinstance(result, StopRequested):
            raise result


# 停止轮询间隔（秒）：gather_results_with_stop 等待期检查停止标志的间隔。
# 与 llm_utils._STOP_POLL_INTERVAL 口径一致；llm_utils 依赖本模块，反向
# import 会循环，故各自定义。
_GATHER_STOP_POLL_INTERVAL = 2.0


async def gather_results_with_stop(*coros):
    """asyncio.gather(return_exceptions=True) 的停止感知版本。

    保留 return_exceptions=True 的分流语义：单个协程的普通异常不中断
    其余协程，仍压平为结果列表项返回，交由调用方分流处理。

    停止差异：任一子协程命中停止（StopRequested 由全局停止标志驱动，
    置位后所有子协程在下一个检查点都会抛出）时，本协程在轮询间隔内
    发现停止标志，立即取消并等待全部剩余子协程退出后抛 StopRequested
    ——不等待慢协程（如长网络抓取）自然结束，也不让取消后的孤儿任务
    在 checkpoint 落盘后继续写 session 状态污染恢复快照。

    调用方仍应在消费结果前调用 raise_if_stop_requested_in_results 兜底
    （gather 与停止几乎同时完成的竞速窗口内，停止可能只出现在结果列表
    而未被本层轮询命中）。

    CancelledError 注入只作用于子协程内部，主图快照由上层 run_step 的
    _save_state_on_error 统一保存（与 gather_with_stop 同契约）。
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    if not tasks:
        return []
    gathered = asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True))
    try:
        while True:
            done, _ = await asyncio.wait({gathered}, timeout=_GATHER_STOP_POLL_INTERVAL)
            if done:
                break
            raise_if_stop_requested()
        return gathered.result()
    except BaseException:
        if not gathered.done():
            # 取消 gather 会级联取消未完成的子任务。
            gathered.cancel()
        # 等待全部子任务退出（含已完成项，await 幂等），防孤儿任务在
        # checkpoint 落盘后继续写 session 状态。
        await asyncio.gather(*tasks, return_exceptions=True)
        # 主动 cancel 的 _GatheringFuture 以 set_exception(CancelledError)
        # 终态（CPython GatheringFuture 语义），必须显式取回终态，否则
        # event loop 报 "exception was never retrieved" 噪音 ERROR。
        try:
            gathered.result()
        except BaseException:
            pass
        raise
