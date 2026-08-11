import asyncio
import contextlib

import pytest

from openjiuwen_deepsearch.algorithm.chart_generation import (
    chart_generator as chart_generator_module,
)


pytestmark = pytest.mark.unit


def _clear_global_chart_semaphore_cache() -> None:
    """重置旧版和新版信号量缓存形态，保证测试彼此隔离。"""
    if hasattr(chart_generator_module, "_global_chart_semaphores"):
        chart_generator_module._global_chart_semaphores.clear()
    if hasattr(chart_generator_module, "_global_chart_semaphore"):
        chart_generator_module._global_chart_semaphore = None


def _run_in_fresh_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _force_global_chart_semaphore_wait_path() -> asyncio.Semaphore:
    semaphore = chart_generator_module._get_global_chart_semaphore()
    release_event = asyncio.Event()
    ready_events = [
        asyncio.Event()
        for _ in range(chart_generator_module.MAX_GLOBAL_CONCURRENT_CHART_TASKS)
    ]

    async def hold_slot(ready_event: asyncio.Event) -> None:
        async with semaphore:
            ready_event.set()
            await release_event.wait()

    holder_tasks = [
        asyncio.create_task(hold_slot(ready_event))
        for ready_event in ready_events
    ]
    await asyncio.gather(*(ready_event.wait() for ready_event in ready_events))

    waiter_task = None
    try:
        waiter_task = asyncio.create_task(semaphore.acquire())
        await asyncio.sleep(0)
        if waiter_task.done():
            waiter_task.result()

        assert not waiter_task.done()
        release_event.set()
        await waiter_task
        semaphore.release()
        await asyncio.gather(*holder_tasks)
        return semaphore
    finally:
        release_event.set()
        if waiter_task is not None and not waiter_task.done():
            waiter_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter_task
        await asyncio.gather(*holder_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_global_chart_semaphore_is_reused_within_running_loop():
    _clear_global_chart_semaphore_cache()
    try:
        first = chart_generator_module._get_global_chart_semaphore()
        second = chart_generator_module._get_global_chart_semaphore()

        assert second is first
    finally:
        _clear_global_chart_semaphore_cache()


def test_global_chart_semaphore_is_not_reused_across_event_loops_after_contention():
    _clear_global_chart_semaphore_cache()
    try:
        first = _run_in_fresh_loop(_force_global_chart_semaphore_wait_path())
        second = _run_in_fresh_loop(_force_global_chart_semaphore_wait_path())

        assert second is not first
    finally:
        _clear_global_chart_semaphore_cache()
