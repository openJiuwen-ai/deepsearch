import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.llm_utils import (
    _resolve_agent_llm_timeout,
    _resolve_node_agent_key,
    _install_usage_only_chunk_parser,
    ainvoke_llm_with_stats,
    add_workflow_llm_usage,
    get_workflow_llm_usage,
    llm_astream,
    pop_workflow_llm_usage,
    save_workflow_llm_usage_to_session,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId


class _DummyModelConfig:
    """模拟 LLM 模型配置对象。"""

    def __init__(self, stream_options=None):
        """初始化模拟配置。

        Args:
            stream_options (dict | None): 预置的流式配置参数。
        """
        self.stream_options = stream_options

    def model_dump(self):
        """导出模拟配置字典。

        Returns:
            包含 stream_options 的配置字典。
        """
        if self.stream_options is None:
            return {}
        return {"stream_options": self.stream_options}


class _DummyModel:
    """模拟 openjiuwen 的 Model 对象。"""

    def __init__(self, stream_options=None):
        """初始化模拟模型。

        Args:
            stream_options (dict | None): 预置的流式配置参数。
        """
        self.model_config = _DummyModelConfig(stream_options=stream_options)


class _FakeResponse:
    """模拟 LLM 响应对象。"""

    def __init__(self, content="ok", usage_metadata=None):
        """初始化模拟响应。

        Args:
            content (str): 模型响应文本内容。
            usage_metadata (dict | None): token usage 元数据。
        """
        self.content = content
        self.usage_metadata = usage_metadata or {}

    def model_dump(self):
        """导出统一响应结构。

        Returns:
            与业务代码兼容的最小响应字典。
        """
        return {"content": self.content, "tool_calls": None}


class _StreamingChunk:
    """模拟可聚合的流式 chunk。"""

    def __init__(self, content: str, usage_metadata=None):
        """初始化流式 chunk。

        Args:
            content: 当前 chunk 的文本内容。
            usage_metadata: 可选的 token 统计信息。
        """
        self.content = content
        self.usage_metadata = usage_metadata or {}

    def __add__(self, other: "_StreamingChunk") -> "_StreamingChunk":
        """模拟 SDK chunk 的可加和行为。

        Args:
            other: 另一个待拼接 chunk。

        Returns:
            _StreamingChunk: 拼接后的新 chunk。
        """
        return _StreamingChunk(self.content + other.content, other.usage_metadata or self.usage_metadata)


class _SlowStreamingModel:
    """模拟可控耗时的流式 LLM。"""

    def __init__(self, delay: float):
        """初始化模拟模型。

        Args:
            delay: 首个 chunk 后额外等待的秒数。
        """
        self.delay = delay

    async def stream(self, **kwargs):
        """按固定节奏输出两个 chunk。

        Args:
            **kwargs: 与真实模型保持兼容的占位参数。

        Yields:
            _StreamingChunk: 模拟产生的流式输出块。
        """
        del kwargs
        yield _StreamingChunk("a")
        await asyncio.sleep(self.delay)
        yield _StreamingChunk("b", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


def test_resolve_node_agent_key_uses_longest_known_prefix():
    """验证节点级匹配会选择最长的已知前缀。

    Returns:
        None.
    """
    assert _resolve_node_agent_key("source_tracer_infer_structured_infer") == NodeId.SOURCE_TRACER_INFER.value
    assert _resolve_node_agent_key("vlm_chart_generatorgenerate_chart_code") == NodeId.VLM_CHART_GENERATOR.value


def test_resolve_agent_llm_timeout_prefers_exact_match_over_node_key():
    """验证超时规则优先精确命中 agent_name。

    Returns:
        None.
    """
    fake_session = SimpleNamespace(
        get_global_state=lambda key: {
            "default": 300,
            "sub_reporter": 600,
            "sub_reporter_classify_doc_infos": 120,
        }
        if key == "config.agent_llm_timeouts"
        else None
    )

    resolved = _resolve_agent_llm_timeout("sub_reporter_classify_doc_infos", fake_session)

    assert resolved.timeout == 120
    assert resolved.matched_by == "agent_name"
    assert resolved.matched_key == "sub_reporter_classify_doc_infos"
    assert resolved.resolved_node_key == "sub_reporter"


def test_resolve_agent_llm_timeout_falls_back_to_node_key_then_default():
    """验证超时规则会先回退到节点级 key，再回退到 default。

    Returns:
        None.
    """
    fake_session = SimpleNamespace(
        get_global_state=lambda key: {
            "default": 300,
            "sub_reporter": 600,
        }
        if key == "config.agent_llm_timeouts"
        else None
    )

    node_level = _resolve_agent_llm_timeout("sub_reporter_outline", fake_session)
    default_level = _resolve_agent_llm_timeout("unknown_agent_name", fake_session)

    assert (node_level.timeout, node_level.matched_by, node_level.matched_key) == (600, "node_key", "sub_reporter")
    assert (default_level.timeout, default_level.matched_by, default_level.matched_key) == (300, "default", "default")


def test_resolve_agent_llm_timeout_preserves_zero_and_clamps_large_rules():
    """验证调用方提供的 0 值被保留（关闭业务层超时），极大值被裁剪到上限。

    Returns:
        None.
    """
    fake_session = SimpleNamespace(
        get_global_state=lambda key: {"default": 999999999, "source_tracer_infer": 0}
        if key == "config.agent_llm_timeouts"
        else None
    )

    zero_resolved = _resolve_agent_llm_timeout("source_tracer_infer_structured_infer", fake_session)
    large_resolved = _resolve_agent_llm_timeout("unknown_agent_name", fake_session)

    # 0 值被保留，表示关闭业务层超时，回退到底层 SDK 控制
    assert zero_resolved.timeout == 0
    assert zero_resolved.matched_by == "node_key"
    # 极大值被裁剪到上限
    assert large_resolved.timeout == 3600
    assert large_resolved.matched_by == "default"


def test_resolve_agent_llm_timeout_disables_feature_without_default():
    """验证缺少 default 时会整体禁用 agent LLM timeout 功能。

    Returns:
        None.
    """
    fake_session = SimpleNamespace(
        get_global_state=lambda key: {"sub_reporter": 120} if key == "config.agent_llm_timeouts" else None
    )

    resolved = _resolve_agent_llm_timeout("sub_reporter", fake_session)

    assert resolved is None


@pytest.mark.asyncio
async def test_ainvoke_allows_default_ai_agent_name():
    """验证未显式传入或传入 None 时保留默认 AI 调用名。

    Returns:
        None.
    """
    llm_obj = {"model": object(), "model_name": "demo-model"}
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
        new=AsyncMock(return_value=_FakeResponse()),
    ) as mock_llm_astream:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
        )
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name=None,
        )

    assert mock_llm_astream.await_args_list[0].kwargs["agent_name"] == "AI"
    assert mock_llm_astream.await_args_list[1].kwargs["agent_name"] == "AI"


@pytest.mark.asyncio
async def test_ainvoke_defaults_blank_agent_name_and_allows_declared_agent_names():
    """验证空白 agent_name 归一为默认值，AgentLlmName 中定义的调用名可通过校验。

    Returns:
        None.
    """
    llm_obj = {"model": object(), "model_name": "demo-model"}
    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
        new=AsyncMock(return_value=_FakeResponse()),
    ) as mock_llm_astream:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name="  ",
        )
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name=AgentLlmName.ENTRY.value,
        )

    assert mock_llm_astream.await_args_list[0].kwargs["agent_name"] == "AI"
    assert mock_llm_astream.await_args_list[1].kwargs["agent_name"] == AgentLlmName.ENTRY.value


@pytest.mark.asyncio
async def test_ainvoke_rejects_undeclared_agent_name():
    """验证未在 AgentLlmName 中定义的 agent_name 会被拒绝。

    Returns:
        None.
    """
    llm_obj = {"model": object(), "model_name": "demo-model"}

    with pytest.raises(CustomValueException) as exc_info:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name="not_declared_agent",
        )

    assert exc_info.value.error_code == StatusCode.PARAM_CHECK_ERROR_COMMON_INVALID.code
    assert "agent_name" in exc_info.value.message


@pytest.mark.asyncio
async def test_llm_astream_raises_custom_timeout_when_wall_clock_limit_is_hit():
    """验证命中 wall-clock timeout 时会抛出专用业务异常。

    Returns:
        None.
    """
    fake_session = SimpleNamespace(
        get_global_state=lambda key: {"default": 1} if key == "config.agent_llm_timeouts" else None,
        write_custom_stream=AsyncMock(),
    )

    from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context

    token = session_context.set(fake_session)
    try:
        with pytest.raises(CustomValueException) as exc_info:
            await llm_astream(
                llm=_SlowStreamingModel(delay=1.2),
                messages=[{"role": "user", "content": "hello"}],
                model_name="demo-model",
                agent_name="sub_reporter",
            )
    finally:
        session_context.reset(token)

    assert exc_info.value.error_code == StatusCode.LLM_WALL_CLOCK_TIMEOUT.code
    assert "agent sub_reporter" in exc_info.value.message
    assert "matched_by=default" in exc_info.value.message


@pytest.mark.asyncio
async def test_llm_astream_skips_wall_clock_timeout_when_rule_is_zero():
    """验证命中值为 0 的规则时会跳过业务层 wall-clock timeout，由底层 SDK 控制。

    Returns:
        None.
    """
    fake_session = SimpleNamespace(
        get_global_state=lambda key: {"default": 1, "sub_reporter": 0} if key == "config.agent_llm_timeouts" else None,
        write_custom_stream=AsyncMock(),
    )

    from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context

    token = session_context.set(fake_session)
    try:
        response = await llm_astream(
            llm=_SlowStreamingModel(delay=0.05),
            messages=[{"role": "user", "content": "hello"}],
            model_name="demo-model",
            agent_name="sub_reporter_outline",
        )
    finally:
        session_context.reset(token)

    # 0 值表示关闭业务层超时，直接返回结果
    assert response.content == "ab"


@pytest.mark.asyncio
async def test_ainvoke_enables_include_usage_when_stats_llm_enabled():
    """验证开启 stats_info_llm 时会注入 include_usage。"""
    llm_obj = {"model": _DummyModel(), "model_name": "demo-model"}
    fake_config = SimpleNamespace(agent_config=SimpleNamespace(stats_info_llm=True))

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.Config",
        return_value=fake_config,
    ), patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
        new=AsyncMock(return_value=_FakeResponse(usage_metadata={"input_tokens": 1, "output_tokens": 2})),
    ) as mock_llm_astream:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name="entry",
        )

    called_kwargs = mock_llm_astream.await_args.kwargs
    assert called_kwargs["stream_options"]["include_usage"] is True


@pytest.mark.asyncio
async def test_ainvoke_merges_existing_stream_options_when_stats_enabled():
    """验证 include_usage 注入时不会覆盖已有 stream_options。"""
    llm_obj = {
        "model": _DummyModel(stream_options={"existing_key": "existing_value", "include_usage": False}),
        "model_name": "demo-model",
    }
    fake_config = SimpleNamespace(agent_config=SimpleNamespace(stats_info_llm=True))

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.Config",
        return_value=fake_config,
    ), patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
        new=AsyncMock(return_value=_FakeResponse(usage_metadata={"input_tokens": 1, "output_tokens": 2})),
    ) as mock_llm_astream:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name="entry",
        )

    stream_options = mock_llm_astream.await_args.kwargs["stream_options"]
    assert stream_options["existing_key"] == "existing_value"
    assert stream_options["include_usage"] is True


@pytest.mark.asyncio
async def test_ainvoke_stats_total_tokens_use_total_tokens_field():
    """验证统计中的 total_tokens 使用正确字段。"""
    llm_obj = {"model": _DummyModel(), "model_name": "demo-model"}
    fake_config = SimpleNamespace(agent_config=SimpleNamespace(stats_info_llm=True))

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.Config",
        return_value=fake_config,
    ), patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
        new=AsyncMock(
            return_value=_FakeResponse(
                usage_metadata={
                    "input_tokens": 11,
                    "output_tokens": 22,
                    "total_tokens": 44,
                    "total_latency": 12345,
                }
            )
        ),
    ), patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.metrics_logger.info",
    ) as mock_metrics_info:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name="entry",
        )

    assert mock_metrics_info.call_count == 1
    logged_line = mock_metrics_info.call_args.args[0]
    assert "'total_tokens': 44" in logged_line


@pytest.mark.asyncio
async def test_ainvoke_does_not_force_include_usage_when_stats_disabled():
    """验证关闭 stats_info_llm 时不会强制注入 include_usage。"""
    llm_obj = {
        "model": _DummyModel(stream_options={"existing_key": "existing_value"}),
        "model_name": "demo-model",
    }
    fake_config = SimpleNamespace(agent_config=SimpleNamespace(stats_info_llm=False))

    with patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.Config",
        return_value=fake_config,
    ), patch(
        "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
        new=AsyncMock(return_value=_FakeResponse()),
    ) as mock_llm_astream:
        await ainvoke_llm_with_stats(
            llm=llm_obj,
            messages=[{"role": "user", "content": "hello"}],
            agent_name="entry",
        )

    called_kwargs = mock_llm_astream.await_args.kwargs
    assert called_kwargs["stream_options"] is None


@pytest.mark.asyncio
async def test_ainvoke_prefers_session_stats_flag_over_global_default():
    """验证会话中的 stats_info_llm 配置优先生效。"""
    llm_obj = {"model": _DummyModel(), "model_name": "demo-model"}
    fake_config = SimpleNamespace(agent_config=SimpleNamespace(stats_info_llm=False))
    fake_session = SimpleNamespace(get_global_state=lambda key: True if key == "config.stats_info_llm" else None)

    from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
    token = session_context.set(fake_session)
    try:
        with patch(
            "openjiuwen_deepsearch.utils.common_utils.llm_utils.Config",
            return_value=fake_config,
        ), patch(
            "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
            new=AsyncMock(return_value=_FakeResponse(usage_metadata={"input_tokens": 1, "output_tokens": 2})),
        ) as mock_llm_astream:
            await ainvoke_llm_with_stats(
                llm=llm_obj,
                messages=[{"role": "user", "content": "hello"}],
                agent_name="entry",
            )
    finally:
        session_context.reset(token)

    called_kwargs = mock_llm_astream.await_args.kwargs
    assert called_kwargs["stream_options"]["include_usage"] is True


def test_workflow_llm_usage_can_accumulate_and_pop():
    """验证 workflow 级 token 统计可以累加并清理。"""
    thread_id = "workflow-usage-case"
    pop_workflow_llm_usage(thread_id)

    add_workflow_llm_usage(thread_id, input_tokens=3, output_tokens=5, total_tokens=8, agent_name="entry")
    add_workflow_llm_usage(thread_id, input_tokens=7, output_tokens=11, total_tokens=18, agent_name="entry")
    add_workflow_llm_usage(thread_id, input_tokens=2, output_tokens=3, total_tokens=5, agent_name="reporter")

    usage = get_workflow_llm_usage(thread_id)
    assert usage == {
        "input_tokens": 12,
        "output_tokens": 19,
        "total_tokens": 31,
        "llm_call_count": 3,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": 10,
                "output_tokens": 16,
                "total_tokens": 26,
                "llm_call_count": 2,
            },
            {
                "agent_name": "reporter",
                "input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": 5,
                "llm_call_count": 1,
            },
        ],
    }

    popped = pop_workflow_llm_usage(thread_id)
    assert popped == usage
    assert get_workflow_llm_usage(thread_id) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,
        "agent_name_token_usage": [],
    }


@pytest.mark.asyncio
async def test_workflow_llm_usage_is_stable_under_coroutine_concurrency():
    """验证协程并发累加场景下 workflow 级 token 统计结果正确。"""
    thread_id = "workflow-usage-concurrency"
    pop_workflow_llm_usage(thread_id)
    workers = 10
    rounds = 100

    async def _worker() -> None:
        """执行单个并发 worker 的累加逻辑。"""
        for _ in range(rounds):
            add_workflow_llm_usage(
                session_id=thread_id,
                input_tokens=1,
                output_tokens=2,
                total_tokens=3,
                agent_name="entry",
            )
            # 主动让出事件循环，模拟 workflow 节点并发调度下的交错调用。
            await asyncio.sleep(0)

    await asyncio.gather(*[_worker() for _ in range(workers)])

    usage = get_workflow_llm_usage(thread_id)
    expected_calls = workers * rounds
    assert usage == {
        "input_tokens": expected_calls,
        "output_tokens": expected_calls * 2,
        "total_tokens": expected_calls * 3,
        "llm_call_count": expected_calls,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": expected_calls,
                "output_tokens": expected_calls * 2,
                "total_tokens": expected_calls * 3,
                "llm_call_count": expected_calls,
            }
        ],
    }
    pop_workflow_llm_usage(thread_id)


@pytest.mark.asyncio
async def test_ainvoke_can_resume_workflow_usage_from_session_snapshot():
    """验证跨进程恢复时可从 session 快照继续累计。"""
    llm_obj = {"model": _DummyModel(), "model_name": "demo-model"}
    fake_config = SimpleNamespace(agent_config=SimpleNamespace(stats_info_llm=False))
    snapshot_usage = {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "llm_call_count": 4,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "llm_call_count": 4,
            }
        ],
    }

    def _get_global_state(key):
        if key == "config.stats_info_llm":
            return True
        if key == "search_context.final_result.workflow_llm_token_usage":
            return snapshot_usage
        return None

    fake_session = SimpleNamespace(get_global_state=_get_global_state)

    from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import session_context
    from openjiuwen_deepsearch.utils.log_utils.log_common import session_id_ctx
    thread_id = "resume-workflow-usage"

    pop_workflow_llm_usage(thread_id)
    session_token = session_context.set(fake_session)
    thread_token = session_id_ctx.set(thread_id)
    try:
        with patch(
            "openjiuwen_deepsearch.utils.common_utils.llm_utils.Config",
            return_value=fake_config,
        ), patch(
            "openjiuwen_deepsearch.utils.common_utils.llm_utils.llm_astream",
            new=AsyncMock(return_value=_FakeResponse(usage_metadata={"input_tokens": 1, "output_tokens": 2})),
        ):
            await ainvoke_llm_with_stats(
                llm=llm_obj,
                messages=[{"role": "user", "content": "hello"}],
                agent_name="entry",
            )
    finally:
        session_context.reset(session_token)
        session_id_ctx.reset(thread_token)

    usage = get_workflow_llm_usage(thread_id)
    assert usage == {
        "input_tokens": 11,
        "output_tokens": 22,
        "total_tokens": 33,
        "llm_call_count": 5,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": 11,
                "output_tokens": 22,
                "total_tokens": 33,
                "llm_call_count": 5,
            }
        ],
    }
    pop_workflow_llm_usage(thread_id)


def test_save_workflow_llm_usage_to_session_writes_snapshot():
    """验证可将当前 workflow token 累计写入 session。"""
    thread_id = "save-workflow-usage"
    pop_workflow_llm_usage(thread_id)
    add_workflow_llm_usage(thread_id, input_tokens=2, output_tokens=3, total_tokens=5, agent_name="entry")
    captured = {}

    class _FakeSession:
        """模拟 session 对象。"""

        def update_global_state(self, data):
            """记录 update_global_state 入参。"""
            captured.update(data)

    usage = save_workflow_llm_usage_to_session(_FakeSession(), thread_id)
    assert usage == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
        "llm_call_count": 1,
        "agent_name_token_usage": [
            {
                "agent_name": "entry",
                "input_tokens": 2,
                "output_tokens": 3,
                "total_tokens": 5,
                "llm_call_count": 1,
            }
        ],
    }
    assert captured["search_context.final_result.workflow_llm_token_usage"] == usage
    pop_workflow_llm_usage(thread_id)


def test_install_usage_only_chunk_parser_can_recover_usage_chunk():
    """验证单次调用级 parser 补偿能解析 usage-only chunk。"""

    class _FakeClient:
        """模拟底层 client。"""

        def _parse_stream_chunk(self, raw_chunk):
            """模拟原始 parser：usage-only chunk 会被直接忽略。"""
            return None

    fake_model = SimpleNamespace(
        _client=_FakeClient(),
        model_config=SimpleNamespace(model_name="demo-model"),
    )
    restore = _install_usage_only_chunk_parser(fake_model)
    assert callable(restore)

    usage_only_chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=9, total_tokens=16),
    )
    parsed_chunk = fake_model._client._parse_stream_chunk(usage_only_chunk)
    assert parsed_chunk is not None
    assert parsed_chunk.usage_metadata is not None
    assert parsed_chunk.usage_metadata.input_tokens == 7
    assert parsed_chunk.usage_metadata.output_tokens == 9
    assert parsed_chunk.usage_metadata.total_tokens == 16

    restore()


def test_install_usage_only_chunk_parser_is_safe_for_nested_restore():
    """验证同一 client 的嵌套安装/恢复不会提前卸载 parser 补偿。"""

    class _FakeClient:
        """模拟底层 client。"""

        def _parse_stream_chunk(self, raw_chunk):
            """模拟原始 parser：usage-only chunk 会被直接忽略。"""
            return None

    fake_model = SimpleNamespace(
        _client=_FakeClient(),
        model_config=SimpleNamespace(model_name="demo-model"),
    )
    original_parser = fake_model._client._parse_stream_chunk
    usage_only_chunk = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8),
    )

    restore_outer = _install_usage_only_chunk_parser(fake_model)
    restore_inner = _install_usage_only_chunk_parser(fake_model)

    restore_outer()
    parsed_chunk = fake_model._client._parse_stream_chunk(usage_only_chunk)
    assert parsed_chunk is not None
    assert parsed_chunk.usage_metadata.total_tokens == 8

    restore_inner()
    assert getattr(fake_model._client._parse_stream_chunk, "__func__", None) is getattr(original_parser, "__func__", None)
