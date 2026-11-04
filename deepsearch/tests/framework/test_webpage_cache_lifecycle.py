"""首次编辑团队执行范围内的正文缓存生命周期；不访问网络或模型。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen_deepsearch.framework.openjiuwen.agent import editor_team_manager_node as module
from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import WebPageEnrichmentNode
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context, webpage_fetch_cache_context


def session(enrichment=True):
    values = {'config.info_collector_webpage_enrich_enable': enrichment}
    return SimpleNamespace(get_global_state=values.get)


@pytest.fixture(autouse=True)
def restore_session_context():
    token = session_context.set(None)
    yield
    session_context.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('node_class', [module.EditorTeamNode, module.DependencyEditorTeamNode])
async def test_sections_share_raw_cache_and_scope_exit_closes_it(node_class, monkeypatch):
    node = node_class()
    fetch = AsyncMock(return_value={'content': 'x' * 300})
    monkeypatch.setattr(WebPageEnrichmentNode, '_fetch_webpage_before_deadline', fetch)
    caches = []
    async def collect(inputs, current_session, context):
        cache = webpage_fetch_cache_context.get()
        caches.append(cache)
        assert not cache.closed
        # 独立节点/章节任务取得同一正文池，相同URL并发只启动一次抓取。
        results = await node._gather_section_tasks([
            WebPageEnrichmentNode()._fetch_webpage('https://example.com/shared', 1),
            WebPageEnrichmentNode()._fetch_webpage('https://example.com/shared', 1),
        ])
        assert all(result['content'] == 'x' * 300 for result in results)
        return {'next_node': 'reporter'}
    monkeypatch.setattr(node, '_do_invoke', collect)
    outer = webpage_fetch_cache_context.get()
    assert await node.invoke({}, session(), None) == {'next_node': 'reporter'}
    assert fetch.await_count == 1
    assert caches[0].closed and caches[0].snapshot()['cache_entries'] == 0
    assert webpage_fetch_cache_context.get() is outer
    # 再次执行首次采集节点使用新池，不跨暂停或会话保留正文。
    await node.invoke({}, session(), None)
    assert caches[0] is not caches[1] and caches[1].closed
    assert fetch.await_count == 2
    # 执行范围外的直接采集不复用首次研究缓存。
    await WebPageEnrichmentNode()._fetch_webpage('https://example.com/shared', 1)
    await WebPageEnrichmentNode()._fetch_webpage('https://example.com/shared', 1)
    assert fetch.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('exception', [RuntimeError('failed'), asyncio.CancelledError()])
async def test_exception_or_cancellation_closes_cache_and_restores_context(monkeypatch, exception):
    node = module.EditorTeamNode()
    caches = []
    async def collect(*args):
        caches.append(webpage_fetch_cache_context.get())
        raise exception
    monkeypatch.setattr(node, '_do_invoke', collect)
    outer = webpage_fetch_cache_context.get()
    with pytest.raises(type(exception)):
        await node.invoke({}, session(), None)
    assert caches[0].closed
    assert webpage_fetch_cache_context.get() is outer


@pytest.mark.asyncio
async def test_disabled_enrichment_does_not_borrow_outer_cache(monkeypatch):
    node = module.EditorTeamNode()
    async def collect(*args):
        assert webpage_fetch_cache_context.get() is None
        return {}
    monkeypatch.setattr(node, '_do_invoke', collect)
    sentinel = object()
    token = webpage_fetch_cache_context.set(sentinel)
    try:
        await node.invoke({}, session(enrichment=False), None)
        assert webpage_fetch_cache_context.get() is sentinel
    finally:
        webpage_fetch_cache_context.reset(token)


@pytest.mark.asyncio
async def test_concurrent_invocations_have_independent_caches(monkeypatch):
    node = module.EditorTeamNode()
    started = asyncio.Queue()
    release = asyncio.Event()
    async def collect(*args):
        cache = webpage_fetch_cache_context.get()
        await started.put(cache)
        await release.wait()
        assert webpage_fetch_cache_context.get() is cache
        return {}
    monkeypatch.setattr(node, '_do_invoke', collect)
    first = asyncio.create_task(node.invoke({}, session(), None))
    second = asyncio.create_task(node.invoke({}, session(), None))
    cache_a, cache_b = await asyncio.wait_for(started.get(), 1), await asyncio.wait_for(started.get(), 1)
    assert cache_a is not cache_b
    release.set()
    await asyncio.gather(first, second)
    assert cache_a.closed and cache_b.closed
    assert webpage_fetch_cache_context.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_inflight_sections_finish_before_cache_closes(monkeypatch, cancel):
    node = module.EditorTeamNode()
    started = asyncio.Event()
    cache_seen, cleanup = [], []
    async def blocked_section():
        cache = webpage_fetch_cache_context.get()
        cache_seen.append(cache)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.append(cache.closed)
    async def failed_section():
        await started.wait()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError('section failed')
    async def collect(*args):
        return await node._gather_section_tasks([blocked_section(), failed_section()])
    monkeypatch.setattr(node, '_do_invoke', collect)
    run = asyncio.create_task(node.invoke({}, session(), None))
    await started.wait()
    if cancel:
        run.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await run
    assert cleanup == [False]
    assert cache_seen[0].closed


@pytest.mark.asyncio
async def test_scope_cancels_shared_fetch_and_does_not_retain_late_body(monkeypatch):
    node = module.EditorTeamNode()
    started = asyncio.Event()
    caches = []
    async def fetch(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return {'content': 'late ' * 100}
    async def collect(*args):
        cache = webpage_fetch_cache_context.get()
        caches.append(cache)
        deadline = asyncio.get_running_loop().time() + 1
        await node._gather_section_tasks([cache.get_or_fetch('url', deadline, 200, fetch)])
    monkeypatch.setattr(node, '_do_invoke', collect)
    run = asyncio.create_task(node.invoke({}, session(), None))
    await started.wait()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert caches[0].closed and caches[0].snapshot()['cache_entries'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_dependency_last_writing_layer_finishes_before_scope_exit(monkeypatch, cancel):
    node = module.DependencyEditorTeamNode()
    outline = module.Outline(title='test', thought='test', sections=[
        module.Section(id='slow', title='slow', description='slow'),
        module.Section(id='failed', title='failed', description='failed'),
    ])
    monkeypatch.setattr(node, '_pre_handle', lambda *args: {'outline': outline})
    monkeypatch.setattr(node, '_create_section_state_from_state', lambda state, outline, section, *args: {'section_idx': section.id})
    monkeypatch.setattr(module, 'build_dependency_reasoning_workflow', lambda: None)
    monkeypatch.setattr(module, 'build_dependency_writing_workflow', lambda: None)
    monkeypatch.setattr(node, '_run_reasoning_await', AsyncMock(return_value={'plans': []}))
    started = asyncio.Event()
    cleaned, caches, child_tasks = [], [], []

    async def write_section(current_session, workflow, state):
        child_tasks.append(asyncio.current_task())
        cache = webpage_fetch_cache_context.get()
        caches.append(cache)
        if state['section_idx'] == 'slow':
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(cache.closed)
        else:
            await started.wait()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError('writing failed')

    monkeypatch.setattr(node, '_run_section_sub_graph_await', write_section)
    run = asyncio.create_task(node.invoke({}, session(), None))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if cancel:
            run.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await asyncio.wait_for(run, 1)
        assert cleaned == [False]
        assert all(task.done() for task in child_tasks)
        assert all(cache.closed for cache in caches)
    finally:
        run.cancel()
        for task in child_tasks:
            task.cancel()
        await asyncio.gather(run, *child_tasks, return_exceptions=True)
