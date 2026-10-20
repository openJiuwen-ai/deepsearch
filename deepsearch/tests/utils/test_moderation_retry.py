"""Recovery consumes existing attempts; no live model calls."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import _run_llm_via_ainvoke
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats as invoke
from openjiuwen_deepsearch.utils.common_utils.moderation_mask import RETRY_FEEDBACK_PROMPT
from openjiuwen_deepsearch.utils.common_utils.moderation_content import evidence_spans
from openjiuwen_deepsearch.utils.common_utils.moderation_retry import moderation_retry_scope
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context

_IMPL = "openjiuwen_deepsearch.utils.common_utils.llm_utils._ainvoke_llm_with_stats_impl"
_MASK = "openjiuwen_deepsearch.utils.common_utils.moderation_mask"
_MODEL = "test-moderation-model"
MESSAGES = [
    {"role": "system", "content": "敏感系统"},
    {"role": "user", "content": evidence_spans("敏感 Sex", [(0, len("敏感 Sex"))])},
]


def _error(side="Input"):
    return CustomValueException(StatusCode.LLM_CALL_FAILED.code, f"{side} data may contain inappropriate content")


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setattr(
        "openjiuwen_deepsearch.utils.common_utils.moderation_mask._load_lexicon_bundled",
        lambda: frozenset({"敏感", "sex", "blocked.example"}),
    )
    return {"model_name": _MODEL, "moderation_mask_retry": True}


def _recorder(calls, errors=(), ok=None):
    async def fake(*args, **kwargs):
        calls.append(deepcopy(kwargs.get("messages", args[1] if len(args) > 1 else None)))
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return ok if ok is not None else {"content": "ok"}
    return fake


@moderation_retry_scope
async def _budget(llm, messages=MESSAGES, attempts=3, positional=False):
    last_error = None
    for _ in range(attempts):
        try:
            return await invoke(llm, messages) if positional else await invoke(llm=llm, messages=messages)
        except Exception as error:
            last_error = error
    raise last_error


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.asyncio
async def test_one_shot_does_not_add_retries(llm, enabled):
    llm["moderation_mask_retry"] = enabled
    calls, error = [], _error()
    with patch(_IMPL, new=_recorder(calls, [error])), patch(f"{_MASK}.get_lexicon") as load:
        with pytest.raises(CustomValueException) as caught:
            await invoke(llm=llm, messages=MESSAGES)
    assert caught.value is error
    assert calls == [MESSAGES]
    load.assert_not_called()


@pytest.mark.parametrize("side", ["Input", "Output"])
@pytest.mark.asyncio
async def test_same_side_uses_original_budget_and_exception(llm, side):
    calls, errors = [], [_error(side) for _ in range(3)]
    with patch(_IMPL, new=_recorder(calls, errors)):
        with pytest.raises(CustomValueException) as caught:
            await _budget(llm)
    assert caught.value is errors[-1]
    assert len(calls) == 3 and calls[0] == MESSAGES and calls[1] == calls[2]
    if side == "Input":
        assert calls[1][1]["content"] == "** ***"
        assert calls[1][0] == MESSAGES[0]
    else:
        assert calls[1] == MESSAGES + [{"role": "user", "content": RETRY_FEEDBACK_PROMPT}]


@pytest.mark.parametrize("kind", ["disabled", "timeout", "no_hit", "last_attempt"])
@pytest.mark.asyncio
async def test_original_retry_behavior_and_lazy_preparation(llm, kind):
    calls, messages, attempts = [], MESSAGES, 3
    error = TimeoutError("timeout") if kind == "timeout" else _error()
    if kind == "disabled":
        llm["moderation_mask_retry"] = False
    if kind == "no_hit":
        messages = [{"role": "user", "content": "ordinary research"}]
    if kind == "last_attempt":
        attempts = 1
    with patch(_IMPL, new=_recorder(calls, [error] * attempts)):
        if kind == "no_hit":
            with pytest.raises(type(error)) as caught:
                await _budget(llm, messages, attempts)
        else:
            with patch(f"{_MASK}.get_lexicon") as load:
                with pytest.raises(type(error)) as caught:
                    await _budget(llm, messages, attempts)
                load.assert_not_called()
    assert caught.value is error
    assert calls == [messages] * attempts


@pytest.mark.parametrize("positional", [False, True])
@pytest.mark.asyncio
async def test_next_attempt_changes_a_copy(llm, positional):
    original, calls = deepcopy(MESSAGES), []
    with patch(_IMPL, new=_recorder(calls, [_error()])):
        assert await _budget(llm, original, positional=positional) == {"content": "ok"}
    assert len(calls) == 2 and calls[1][1]["content"] == "** ***"
    assert original == MESSAGES


@pytest.mark.parametrize("sides", [("Input", "Output"), ("Output", "Input")])
@pytest.mark.asyncio
async def test_side_switch_keeps_both_policies(llm, sides):
    calls = []
    with patch(_IMPL, new=_recorder(calls, [_error(s) for s in sides])):
        await _budget(llm)
    assert len(calls) == 3 and len(calls[2]) == 3
    assert calls[2][1]["content"] == "** ***"
    assert calls[2][-1]["content"] == RETRY_FEEDBACK_PROMPT


@pytest.mark.asyncio
async def test_unknown_side_retains_input_fallback(llm):
    calls = []
    error = CustomValueException(StatusCode.LLM_CALL_FAILED.code, "data_inspection_failed")
    with patch(_IMPL, new=_recorder(calls, [error])):
        await _budget(llm)
    assert len(calls) == 2 and calls[1][1]["content"] == "** ***"


@pytest.mark.asyncio
async def test_output_constraint_not_duplicated(llm):
    messages = MESSAGES + [{"role": "user", "content": RETRY_FEEDBACK_PROMPT}]
    calls = []
    with patch(_IMPL, new=_recorder(calls, [_error("Output")] * 2)):
        await _budget(llm, messages)
    assert calls == [messages] * 3


@pytest.mark.asyncio
async def test_rebuilt_validation_feedback_is_preserved(llm):
    calls = []

    @moderation_retry_scope
    async def operation():
        messages = MESSAGES
        for _ in range(3):
            try:
                result = await invoke(llm=llm, messages=messages)
            except CustomValueException:
                continue
            if result["content"] == "ok":
                return result
            messages = MESSAGES + [{"role": "user", "content": "repair JSON 敏感"}]
        pytest.fail("expected success within budget")

    async def fake(**kwargs):
        calls.append(deepcopy(kwargs["messages"]))
        if len(calls) == 1:
            raise _error()
        return {"content": "invalid JSON" if len(calls) == 2 else "ok"}

    with patch(_IMPL, new=fake):
        await operation()
    assert len(calls) == 3 and calls[2][1]["content"] == "** ***"
    assert calls[2][-1]["content"] == "repair JSON 敏感"


@pytest.mark.asyncio
async def test_scope_cleanup_and_independent_operations(llm):
    calls = []
    with patch(_IMPL, new=_recorder(calls, [_error()])):
        await _budget(llm)
        await _budget(llm)
        await invoke(llm=llm, messages=MESSAGES)
    assert len(calls) == 4 and calls[1][1]["content"] == "** ***"
    assert calls[2:] == [MESSAGES, MESSAGES]


@pytest.mark.asyncio
async def test_parallel_operations_are_isolated(llm):
    calls = {"A": [], "B": []}

    async def fake(**kwargs):
        messages = kwargs["messages"]
        label = messages[0]["content"]
        calls[label].append(deepcopy(messages))
        await asyncio.sleep(0)
        if label == "A" and len(calls[label]) == 1:
            raise _error()
        return {"content": "ok"}

    a, b = deepcopy(MESSAGES), deepcopy(MESSAGES)
    a[0]["content"], b[0]["content"] = "A", "B"
    with patch(_IMPL, new=fake):
        await asyncio.gather(_budget(llm, a), _budget(llm, b))
    assert len(calls["A"]) == 2 and calls["A"][1][1]["content"] == "** ***"
    assert calls["B"] == [b]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.asyncio
async def test_nested_scope_and_section_keys_are_isolated(llm, nested):
    calls = []

    @moderation_retry_scope
    async def operation():
        with pytest.raises(CustomValueException):
            await invoke(llm=llm, messages=MESSAGES, moderation_retry_key="A")
        if nested:
            await _budget(llm)
        else:
            await invoke(llm=llm, messages=MESSAGES, moderation_retry_key="B")
        await invoke(llm=llm, messages=MESSAGES, moderation_retry_key="A")

    with patch(_IMPL, new=_recorder(calls, [_error()])):
        await operation()
    assert calls[:2] == [MESSAGES, MESSAGES] and calls[2][1]["content"] == "** ***"


@pytest.mark.asyncio
async def test_unscoped_framework_message_objects_are_not_guessed(llm):
    from openjiuwen.core.foundation.llm.schema.message import SystemMessage, UserMessage

    text = "敏感 Sex https://blocked.example/a https://sussex.ac.uk"
    original = [SystemMessage(content="敏感系统"), UserMessage(content=text)]
    calls = []
    with patch(_IMPL, new=_recorder(calls, [_error()])):
        await _budget(llm, original)
    assert isinstance(calls[1][1], UserMessage)
    assert calls[1][1].content == text
    assert calls[1][0].content == original[0].content and original[1].content == text


@pytest.mark.asyncio
async def test_unscoped_task_keeps_original_three_attempts_without_loading_lexicon(llm):
    messages = [{"role": "user", "content": "敏感 Sex research task"}]
    calls, error = [], _error()
    with patch(_IMPL, new=_recorder(calls, [error] * 3)), patch(f"{_MASK}.get_lexicon") as load:
        with pytest.raises(CustomValueException) as caught:
            await _budget(llm, messages)
    assert caught.value is error and calls == [messages] * 3
    load.assert_not_called()


@pytest.mark.parametrize("side", ["Input", "Output"])
@pytest.mark.asyncio
async def test_real_run_llm_retains_four_attempts_and_backoff(llm, side):
    calls, token = [], llm_context.set({_MODEL: llm})
    try:
        with patch(_IMPL, new=_recorder(calls, [_error(side)] * 4)), patch(
            "openjiuwen_deepsearch.algorithm.search_nodes.llm_utils.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            with pytest.raises(CustomValueException) as caught:
                await _run_llm_via_ainvoke(messages=MESSAGES, config={"model_name": _MODEL})
        assert caught.value.error_code == StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.code
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2, 4]
    finally:
        llm_context.reset(token)
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_real_collector_retains_fallback(llm):
    from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder import GenerateQueryNode

    node, calls = GenerateQueryNode(), []
    node.llm = llm
    module = "openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.graph_builder"
    with patch(f"{module}.max_retries", 3), patch(_IMPL, new=_recorder(calls, [_error()] * 3)):
        result = await node._invoke_llm_with_retry(MESSAGES, 0, "step", 3)
    assert len(calls) == 3 and result.queries == ["step"]


@pytest.mark.asyncio
async def test_real_tenacity_retains_budget_and_original_error(llm):
    from tenacity import RetryError
    from openjiuwen_deepsearch.algorithm.report.report_parts import ReportPartsMixin

    reporter, calls = ReportPartsMixin(), []
    reporter._llm, reporter.gen_report_context = llm, {}
    module = "openjiuwen_deepsearch.algorithm.report.report_parts"
    config = SimpleNamespace(service_config=SimpleNamespace(report_max_generate_retry_num=4))
    errors = [_error("Output") for _ in range(4)]
    with patch(f"{module}.Config", return_value=config), patch(
        f"{module}.build_prompt_messages", return_value=MESSAGES
    ), patch(_IMPL, new=_recorder(calls, errors)):
        with pytest.raises(RetryError) as caught:
            await reporter._generate_with_llm("abstract", "unused", "")
    assert caught.value.last_attempt.exception() is errors[-1] and len(calls) == 4


@pytest.mark.asyncio
async def test_real_planner_retains_failure_result(llm):
    from openjiuwen_deepsearch.algorithm.query_understanding.planner import Planner, PlannerConfig

    planner, calls = Planner(PlannerConfig(llm=llm, max_retry_num=3)), []
    module = "openjiuwen_deepsearch.algorithm.query_understanding.planner"
    with patch(f"{module}.build_prompt_messages", return_value=MESSAGES), patch(
        _IMPL, new=_recorder(calls, [_error()] * 3)
    ):
        result = await planner.generate_plan({"section_idx": 1, "max_step_num": 3})
    assert len(calls) == 3 and result.plan_success is False and result.plan is None
    assert result.response_messages == [] and result.error_msg


@pytest.mark.asyncio
async def test_real_sidecar_retains_full_body_fallback(llm):
    from openjiuwen_deepsearch.algorithm.report.report_parts import ReportPartsMixin

    reporter, calls = ReportPartsMixin(), []
    reporter._llm = llm
    module = "openjiuwen_deepsearch.algorithm.report.report_parts"
    with patch(f"{module}.build_prompt_messages", return_value=MESSAGES), patch(
        _IMPL, new=_recorder(calls, [_error("Output")] * 3)
    ):
        result = await reporter._generate_sub_report_sidecar(
            {"section_idx": 1, "sub_report_content": "chapter body", "max_generate_retry_num": 3}
        )
    assert len(calls) == 3 and result["sidecar"] is None
    assert result["summary"] == "chapter body" and result["warning"]


@pytest.mark.asyncio
async def test_one_shot_webpage_selection_retains_fallback(llm):
    from openjiuwen_deepsearch.framework.openjiuwen.agent.collector_graph.webpage_enrichment import WebPageEnrichmentNode

    node, calls = WebPageEnrichmentNode(), []
    node.llm = llm
    state = {"step_title": "test", "candidates": [
        {"url": "https://example.com", "title": "敏感标题", "snippet": "text"}
    ]}
    with patch(_IMPL, new=_recorder(calls, [_error()])):
        assert await node._select_candidate_indexes(state) == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_recursive_split_inherits_without_sibling_contamination(llm):
    from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import CitationVerifyResearch

    verifier, calls = CitationVerifyResearch(_MODEL), []

    async def extract_once(datas, semaphore):
        text = f"敏感 {datas[0]}:{len(datas)}"
        return await invoke(llm=llm, messages=[{"role": "user", "content": evidence_spans(text, [(0, 2)])}])

    async def fake(**kwargs):
        messages = deepcopy(kwargs["messages"])
        calls.append(messages)
        if len(calls) == 1:
            raise _error()
        if "0:2" in messages[0]["content"]:
            raise _error("Output")
        return [{"ok": True}]

    with patch.object(verifier, "extract_messages_once", new=extract_once), patch(_IMPL, new=fake):
        await verifier.extract_messages_with_split_retry(list(range(4)), 1, "root", asyncio.Semaphore(2))
    assert len(calls) == 5 and all("**" in m[0]["content"] for m in calls[1:])
    right = next(m for m in calls if "2:2" in m[0]["content"])
    assert len(right) == 1
    leaves = [m for m in calls if ":1" in m[0]["content"]]
    assert len(leaves) == 2 and all(m[-1]["content"] == RETRY_FEEDBACK_PROMPT for m in leaves)


@pytest.mark.asyncio
async def test_cancellation_restores_parent_context(llm):
    calls = []

    @moderation_retry_scope
    async def operation():
        with pytest.raises(CustomValueException):
            await invoke(llm=llm, messages=MESSAGES)
        raise asyncio.CancelledError()

    with patch(_IMPL, new=_recorder(calls, [_error()])):
        with pytest.raises(asyncio.CancelledError):
            await operation()
        await invoke(llm=llm, messages=MESSAGES)
    assert calls == [MESSAGES, MESSAGES]


@pytest.mark.asyncio
async def test_sampling_and_output_parameters_are_preserved(llm):
    calls, options = [], []

    @moderation_retry_scope
    async def operation():
        for _ in range(2):
            try:
                return await invoke(
                    llm=llm, messages=MESSAGES, agent_name="test-agent", temperature=0.2,
                    max_tokens=777, need_stream_out=True, tools=[{"type": "function"}],
                    moderation_retry_key="section-1",
                )
            except CustomValueException:
                continue
        pytest.fail("expected second attempt to succeed")

    async def fake(**kwargs):
        calls.append(deepcopy(kwargs.pop("messages")))
        assert "moderation_retry_key" not in kwargs
        options.append(kwargs)
        if len(calls) == 1:
            raise _error("Output")
        return {"content": "ok"}

    with patch(_IMPL, new=fake):
        await operation()
    assert len(calls) == 2 and options[0] == options[1]
    assert calls[1][-1]["content"] == RETRY_FEEDBACK_PROMPT


@pytest.mark.asyncio
async def test_real_evidence_validation_rebuild_keeps_masking(llm):
    from openjiuwen_deepsearch.algorithm.report.report import Reporter

    calls, token = [], llm_context.set({_MODEL: llm})
    try:
        reporter = Reporter(_MODEL)

        def prompt(name, context):
            return deepcopy(MESSAGES) + [{"role": "user", "content": context.get("retry_feedback", "")}]

        async def fake(**kwargs):
            calls.append(deepcopy(kwargs["messages"]))
            if len(calls) == 1:
                raise _error()
            if len(calls) == 2:
                return {"content": "[]"}
            return {"content": '{"rationales": [{"id": "r1", "description": "d", "type": "factual"}]}'}

        with patch("openjiuwen_deepsearch.algorithm.report.evidence.build_prompt_messages", new=prompt), patch(
            _IMPL, new=fake
        ):
            result, error = await reporter._generate_section_rationales({"max_generate_retry_num": 3})
    finally:
        llm_context.reset(token)
    assert result and not error and len(calls) == 3
    assert calls[1][1]["content"] == calls[2][1]["content"] == "** ***"
    assert "not a JSON object" in calls[2][-1]["content"]


@pytest.mark.parametrize("scenario", ["different_sides", "partial_success", "exhausted"])
@pytest.mark.asyncio
async def test_real_html_section_retry_isolated_and_caches_success(llm, scenario):
    from openjiuwen_deepsearch.algorithm.brief_report.html_reporter import generate_brief_html_report

    module = "openjiuwen_deepsearch.algorithm.brief_report.html_reporter"
    calls = {"1": [], "2": []}
    shell = '<html><head></head><body><div id="brief-sections"></div></body></html>'

    def prompt(name, context):
        return [{"role": "system", "content": context["section_id"]}, *deepcopy(MESSAGES[1:])]

    async def fake(*args, **kwargs):
        messages = deepcopy(kwargs.get("messages", args[1] if len(args) > 1 else None))
        section_id = messages[0]["content"]
        calls[section_id].append(messages)
        await asyncio.sleep(0)
        if scenario == "exhausted" or len(calls[section_id]) == 1 and (
            section_id == "1" or scenario == "different_sides"
        ):
            raise _error("Input" if section_id == "1" else "Output")
        return {"content": "<html_section><section><p>ok</p></section></html_section>"}

    config = SimpleNamespace(service_config=SimpleNamespace(report_max_generate_retry_num=3))
    with patch(f"{module}.Config", return_value=config), patch(
        f"{module}._generate_shell", new_callable=AsyncMock, return_value=shell
    ) as generate_shell, patch(f"{module}.build_prompt_messages", new=prompt), patch(
        f"{module}.validate_html_report", return_value=([], [])
    ), patch(_IMPL, new=fake):
        kwargs = {"llm": llm, "markdown": "# Test\n\n## 1 A\n\ntext\n\n## 2 B\n\ntext", "language": "en"}
        if scenario == "exhausted":
            with pytest.raises(ValueError, match="brief html report generation failed"):
                await generate_brief_html_report(**kwargs)
        else:
            assert "ok" in await generate_brief_html_report(**kwargs)
    generate_shell.assert_awaited_once()
    assert len(calls["1"]) == (3 if scenario == "exhausted" else 2)
    assert len(calls["2"]) == (3 if scenario == "exhausted" else 1 if scenario == "partial_success" else 2)
    assert calls["1"][1][1]["content"] == "** ***"
    assert all(m[-1]["content"] != RETRY_FEEDBACK_PROMPT for m in calls["1"])
    assert calls["2"][0][1]["content"] == "敏感 Sex"
    if scenario != "partial_success":
        assert calls["2"][1][1]["content"] == "敏感 Sex"
        assert calls["2"][1][-1]["content"] == RETRY_FEEDBACK_PROMPT
