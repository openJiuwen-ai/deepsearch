"""Shared pytest configuration for the test suite."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_main_thread_event_loop() -> None:
    """Ensure each test starts with a usable default asyncio loop on the main thread.

    openjiuwen graph ``Vertex`` creates ``asyncio.Future`` / ``asyncio.Event`` during
    synchronous workflow setup, which binds to ``asyncio.get_event_loop()``. Any prior
    test or library code may call ``asyncio.run()`` or pytest-asyncio may restore the
    thread loop to ``None``, which breaks later synchronous graph construction.
    """
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)
