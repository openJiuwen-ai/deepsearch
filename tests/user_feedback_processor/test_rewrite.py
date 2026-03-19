from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.algorithm.user_feedback_processor.synonym_rewrite import (
    ACTION_TO_PROMPT,
    SYNONYM_REWRITE_ACTIONS,
    SynonymRewriter,
)


SYNONYM_REWRITER_MODULE_PATH = "openjiuwen_deepsearch.algorithm.user_feedback_processor.synonym_rewrite"


class TestCitationHelpers:
    def test_strip_citations_only_within_selected_range(self):
        report = (
            "前缀[[1]](https://a.com)"
            "这是要改写的段落[[2]](https://b.com)结束"
            "[[3]](https://c.com)尾部"
        )
        start = report.index("这是要改写的段落")
        end = start + len("这是要改写的段落[[2]](https://b.com)结束")
        removed_start = report.index("[[2]]")
        removed_end = removed_start + len("[[2]](https://b.com)")

        stripped_text, removed_ranges, removed_inference_ids = SynonymRewriter._strip_citations_in_range(
            report, start, end
        )

        assert removed_ranges == {(removed_start, removed_end)}
        assert removed_inference_ids == []
        assert "[[1]]" in stripped_text
        assert "[[3]]" in stripped_text
        assert "[[2]]" not in stripped_text

    def test_strip_citations_supports_origin_citation_format_without_source_tracer(self):
        report = "前缀这是要改写的段落[citation: 2]结束[citation: 3]尾部"
        start = report.index("这是要改写的段落")
        end = start + len("这是要改写的段落[citation: 2]结束")
        removed_start = report.index("[citation: 2]")
        removed_end = removed_start + len("[citation: 2]")

        stripped_text, removed_ranges, removed_inference_ids = SynonymRewriter._strip_citations_in_range(
            report, start, end
        )

        assert removed_ranges == {(removed_start, removed_end)}
        assert removed_inference_ids == []
        assert "[citation: 2]" not in stripped_text
        assert "[citation: 3]" in stripped_text

    def test_strip_citations_removes_multiple_citations_within_selection(self):
        report = (
            "前缀"
            "需要改写[[2]](https://b.com)的段落[[3]](https://c.com)内容"
            "尾部[[4]](https://d.com)"
        )
        start = report.index("需要改写")
        end = report.index("内容") + len("内容")

        stripped_text, removed_ranges, removed_inference_ids = SynonymRewriter._strip_citations_in_range(
            report, start, end
        )

        assert len(removed_ranges) == 2
        assert removed_inference_ids == []
        assert "[[2]]" not in stripped_text
        assert "[[3]]" not in stripped_text
        assert "[[4]]" in stripped_text

    def test_strip_citations_rewrites_inference_markers_to_plain_text_and_collects_ids(self):
        report = "前缀[结论A](#inference:1)中间[结论B](#inference:3)尾部"
        start = report.index("[结论A]")
        end = report.index("尾部")

        stripped_text, removed_ranges, removed_inference_ids = SynonymRewriter._strip_citations_in_range(
            report, start, end
        )

        assert removed_ranges == set()
        assert removed_inference_ids == [1, 3]
        assert stripped_text == "前缀结论A中间结论B尾部"

    def test_remove_citations_from_messages_updates_messages(self):
        messages = {
            "code": 0,
            "msg": "success",
            "data": [
                {"id": 0, "reference_index": 1, "citation_start_offset": 0, "citation_end_offset": 20},
                {"id": 1, "reference_index": 2, "citation_start_offset": 30, "citation_end_offset": 50},
            ],
        }

        updated_messages = SynonymRewriter._remove_citations_from_messages(
            messages,
            removed_citation_ranges={(30, 50)},
        )

        assert updated_messages["data"] == [
            {"id": 0, "reference_index": 1, "citation_start_offset": 0, "citation_end_offset": 20}
        ]

    def test_remove_citations_from_messages_tolerates_missing_message_data(self):
        messages = {"code": 0, "msg": "success"}

        updated_messages = SynonymRewriter._remove_citations_from_messages(
            messages,
            removed_citation_ranges={(30, 50)},
        )

        assert updated_messages == {"code": 0, "msg": "success"}

    def test_update_citation_offsets_moves_only_trailing_citations(self):
        datas = [
            {"reference_index": 1, "citation_start_offset": 5, "citation_end_offset": 20},
            {"reference_index": 2, "citation_start_offset": 80, "citation_end_offset": 100},
        ]

        result = SynonymRewriter._update_citation_offsets(
            datas,
            original_end_offset=60,
            original_selected_len=30,
            rewritten_len=40,
        )

        assert result[0]["citation_start_offset"] == 5
        assert result[1]["citation_start_offset"] == 90
        assert result[1]["citation_end_offset"] == 110


class TestSynonymRewriter:
    @pytest.fixture
    def synonym_rewriter(self):
        return SynonymRewriter(llm_model_name="mock_model")

    def test_prompt_names_match_registered_actions(self, synonym_rewriter):
        assert SYNONYM_REWRITE_ACTIONS == frozenset(ACTION_TO_PROMPT.keys())
        assert synonym_rewriter.get_prompt_name("expand") == "synonym_rewrite_expand"
        assert synonym_rewriter.get_prompt_name("polish") == "synonym_rewrite_polish"
        assert synonym_rewriter.get_prompt_name("shorten") == "synonym_rewrite_shorten"

    def test_invalid_prompt_name_raises(self, synonym_rewriter):
        with pytest.raises(CustomValueException) as exc_info:
            synonym_rewriter.get_prompt_name("unknown_action")

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_INVALID_ACTION.code

    @pytest.mark.asyncio
    async def test_generate_synonym_rewrite_text_calls_llm_wrapper(self, synonym_rewriter):
        with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.apply_system_prompt") as mock_prompt:
            mock_prompt.return_value = [{"role": "system", "content": "prompt"}]
            with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.get_llm_instance", return_value=object()):
                with patch(
                    f"{SYNONYM_REWRITER_MODULE_PATH}.ainvoke_llm_with_stats",
                    new_callable=AsyncMock,
                ) as mock_ainvoke:
                    mock_ainvoke.return_value = {"content": "这是改写后的文本内容"}

                    result = await synonym_rewriter._generate_synonym_rewrite_text(
                        action="expand",
                        original_text="这是原文",
                        language="zh-CN",
                        user_instruction="请详细展开",
                    )

        assert result == "这是改写后的文本内容"
        mock_prompt.assert_called_once()
        mock_ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_synonym_rewrite_text_wraps_unknown_exception(self, synonym_rewriter):
        with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.apply_system_prompt", return_value=[{"role": "system", "content": "prompt"}]):
            with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.get_llm_instance", return_value=object()):
                with patch(
                    f"{SYNONYM_REWRITER_MODULE_PATH}.ainvoke_llm_with_stats",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("boom"),
                ):
                    with pytest.raises(CustomValueException) as exc_info:
                        await synonym_rewriter._generate_synonym_rewrite_text(
                            action="expand",
                            original_text="这是原文",
                            language="zh-CN",
                            user_instruction="",
                        )

        assert exc_info.value.error_code == StatusCode.USER_FEEDBACK_PROCESSOR_REWRITE_ERROR.code


class TestRewriteFlow:
    @pytest.fixture
    def synonym_rewriter(self):
        return SynonymRewriter(llm_model_name="mock_model")

    @pytest.mark.asyncio
    async def test_synonym_rewrite_uses_generated_text(self, synonym_rewriter):
        with patch.object(synonym_rewriter, "_generate_synonym_rewrite_text", new_callable=AsyncMock) as mock_generate:
            mock_generate.return_value = "改写后的文本"

            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "expand",
                    "selected_text": "原文",
                    "start_offset": 0,
                    "end_offset": 2,
                    "user_instruction": "",
                },
                report_content="原文后续内容",
                citation_messages={},
                language="zh-CN",
            )

        assert result["rewritten_text"] == "改写后的文本"
        assert result["new_report"] == "改写后的文本后续内容"
        assert result["updated_messages"] == {}
        assert result["updated_infer_messages"] == []

    @pytest.mark.asyncio
    async def test_synonym_rewrite_handles_origin_citation_format_without_citation_messages(self, synonym_rewriter):
        report = "前缀需要改写[citation: 2]的段落尾部"
        selected = "需要改写[citation: 2]的段落"
        start = report.index("需要改写")
        end = start + len(selected)

        with patch.object(synonym_rewriter, "_generate_synonym_rewrite_text", new_callable=AsyncMock) as mock_generate:
            mock_generate.return_value = "改写后的段落"

            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "expand",
                    "selected_text": selected,
                    "start_offset": start,
                    "end_offset": end,
                    "user_instruction": "",
                },
                report_content=report,
                citation_messages={},
                language="zh-CN",
            )

        mock_generate.assert_awaited_once_with(
            action="expand",
            original_text="需要改写的段落",
            language="zh-CN",
            user_instruction="",
        )
        assert result["new_report"] == "前缀改写后的段落尾部"
        assert result["updated_messages"] == {}
        assert result["updated_infer_messages"] == []

    @pytest.mark.asyncio
    async def test_full_rewrite_expand_flow_removes_selected_citation_and_updates_report(self):
        report = "引言部分。[[1]](https://a.com)这是需要扩写的段落内容。[[2]](https://b.com)结论部分。"
        selected = "这是需要扩写的段落内容。[[2]](https://b.com)"
        start = report.index("这是需要扩写的段落内容。")
        end = start + len(selected)
        feedback = {
            "action": "expand",
            "selected_text": selected,
            "start_offset": start,
            "end_offset": end,
            "user_instruction": "补充技术细节",
        }

        with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.apply_system_prompt", return_value=[{"role": "system", "content": "prompt"}]):
            with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.get_llm_instance", return_value=object()):
                with patch(
                    f"{SYNONYM_REWRITER_MODULE_PATH}.ainvoke_llm_with_stats",
                    new_callable=AsyncMock,
                ) as mock_ainvoke:
                    mock_ainvoke.return_value = {"content": "这是经过详细扩写后的段落内容，补充了技术细节和实现方案。"}

                    synonym_rewriter = SynonymRewriter(llm_model_name="mock")
                    result = await synonym_rewriter.synonym_rewrite(
                        feedback=feedback,
                        report_content=report,
                        citation_messages={
                            "code": 0,
                            "msg": "success",
                            "data": [
                                {
                                    "id": 0,
                                    "reference_index": 2,
                                    "citation_start_offset": end - len("[[2]](https://b.com)"),
                                    "citation_end_offset": end,
                                }
                            ],
                        },
                        language="zh-CN",
                    )

        assert "引言部分" in result["new_report"]
        assert "结论部分" in result["new_report"]
        assert "[[1]]" in result["new_report"]
        assert "[[2]]" not in result["new_report"]
        assert result["rewritten_text"] == "这是经过详细扩写后的段落内容，补充了技术细节和实现方案。"
        assert result["start_offset"] == start
        assert result["new_end_offset"] == start + len(result["rewritten_text"])
        assert result["updated_messages"]["data"] == []

    @pytest.mark.asyncio
    async def test_full_rewrite_expand_flow_removes_multiple_selected_citations_and_shifts_trailing_offsets(self):
        report = (
            "前言"
            "需要扩写[[2]](https://b.com)的段落[[3]](https://c.com)内容"
            "尾注[[4]](https://d.com)"
        )
        selected = "需要扩写[[2]](https://b.com)的段落[[3]](https://c.com)内容"
        start = report.index("需要扩写")
        end = start + len(selected)

        with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.apply_system_prompt", return_value=[{"role": "system", "content": "prompt"}]):
            with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.get_llm_instance", return_value=object()):
                with patch(
                    f"{SYNONYM_REWRITER_MODULE_PATH}.ainvoke_llm_with_stats",
                    new_callable=AsyncMock,
                ) as mock_ainvoke:
                    mock_ainvoke.return_value = {"content": "扩写后的段落内容"}

                    synonym_rewriter = SynonymRewriter(llm_model_name="mock")
                    result = await synonym_rewriter.synonym_rewrite(
                        feedback={
                            "action": "expand",
                            "selected_text": selected,
                            "start_offset": start,
                            "end_offset": end,
                            "user_instruction": "",
                        },
                        report_content=report,
                        citation_messages={
                            "code": 0,
                            "msg": "success",
                            "data": [
                                {
                                    "id": 0,
                                    "reference_index": 2,
                                    "citation_start_offset": report.index("[[2]]"),
                                    "citation_end_offset": report.index("[[2]]") + len("[[2]](https://b.com)"),
                                },
                                {
                                    "id": 1,
                                    "reference_index": 3,
                                    "citation_start_offset": report.index("[[3]]"),
                                    "citation_end_offset": report.index("[[3]]") + len("[[3]](https://c.com)"),
                                },
                                {
                                    "id": 2,
                                    "reference_index": 4,
                                    "citation_start_offset": report.index("[[4]]"),
                                    "citation_end_offset": report.index("[[4]]") + len("[[4]](https://d.com)"),
                                },
                            ],
                        },
                        language="zh-CN",
                    )

        assert "[[2]]" not in result["new_report"]
        assert "[[3]]" not in result["new_report"]
        assert "[[4]]" in result["new_report"]
        assert [item["reference_index"] for item in result["updated_messages"]["data"]] == [4]
        assert result["updated_messages"]["data"][0]["id"] == 0
        assert result["updated_messages"]["data"][0]["citation_start_offset"] < report.index("[[4]]")

    @pytest.mark.asyncio
    async def test_synonym_rewrite_with_shorten_shifts_trailing_citation_backward(self, synonym_rewriter):
        report = "前言需要精简的段落内容尾注[[4]](https://d.com)"
        selected = "需要精简的段落内容"
        start = report.index(selected)
        end = start + len(selected)

        with patch.object(synonym_rewriter, "_generate_synonym_rewrite_text", new_callable=AsyncMock) as mock_generate:
            mock_generate.return_value = "精简后"

            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "shorten",
                    "selected_text": selected,
                    "start_offset": start,
                    "end_offset": end,
                    "user_instruction": "",
                },
                report_content=report,
                citation_messages={
                    "code": 0,
                    "msg": "success",
                    "data": [
                        {
                            "id": 0,
                            "reference_index": 4,
                            "citation_start_offset": report.index("[[4]]"),
                            "citation_end_offset": report.index("[[4]]") + len("[[4]](https://d.com)"),
                        }
                    ],
                },
                language="zh-CN",
            )

        assert result["new_report"] == "前言精简后尾注[[4]](https://d.com)"
        assert result["updated_messages"]["data"][0]["citation_start_offset"] < report.index("[[4]]")

    @pytest.mark.asyncio
    async def test_synonym_rewrite_keeps_unselected_duplicate_reference_instances(self, synonym_rewriter):
        report = (
            "first shared citation [[1]](https://shared.com) needs rewrite. "
            "later there is another shared citation [[1]](https://shared.com) that should remain."
        )
        selected = "first shared citation [[1]](https://shared.com) needs rewrite."
        start = report.index(selected)
        end = start + len(selected)
        first_citation_start = report.index("[[1]]")
        second_citation_start = report.rindex("[[1]]")
        citation_len = len("[[1]](https://shared.com)")
        rewritten_text = "rewritten first segment."
        expected_delta = len(rewritten_text) - len(selected)

        with patch.object(synonym_rewriter, "_generate_synonym_rewrite_text", new_callable=AsyncMock) as mock_generate:
            mock_generate.return_value = rewritten_text

            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "expand",
                    "selected_text": selected,
                    "start_offset": start,
                    "end_offset": end,
                    "user_instruction": "",
                },
                report_content=report,
                citation_messages={
                    "code": 0,
                    "msg": "success",
                    "data": [
                        {
                            "id": 0,
                            "reference_index": 1,
                            "citation_start_offset": first_citation_start,
                            "citation_end_offset": first_citation_start + citation_len,
                        },
                        {
                            "id": 1,
                            "reference_index": 1,
                            "citation_start_offset": second_citation_start,
                            "citation_end_offset": second_citation_start + citation_len,
                        },
                    ],
                },
                language="en-US",
            )

        assert "[[1]](https://shared.com)" in result["new_report"]
        assert result["updated_messages"]["data"] == [
            {
                "id": 0,
                "reference_index": 1,
                "citation_start_offset": second_citation_start + expected_delta,
                "citation_end_offset": second_citation_start + citation_len + expected_delta,
            }
        ]

    @pytest.mark.asyncio
    async def test_synonym_rewrite_removes_selected_inference_messages_and_remaps_remaining_ids(self, synonym_rewriter):
        report = (
            "前缀[已选结论](#inference:1)中段"
            "[保留结论](#inference:10)"
            "尾注[[4]](https://d.com)"
        )
        selected = "[已选结论](#inference:1)中段"
        start = report.index(selected)
        end = start + len(selected)
        trailing_citation_start = report.index("[[4]]")
        trailing_citation_end = trailing_citation_start + len("[[4]](https://d.com)")

        with patch.object(synonym_rewriter, "_generate_synonym_rewrite_text", new_callable=AsyncMock) as mock_generate:
            mock_generate.return_value = "改写后"

            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "expand",
                    "selected_text": selected,
                    "start_offset": start,
                    "end_offset": end,
                    "user_instruction": "",
                },
                report_content=report,
                citation_messages={
                    "code": 0,
                    "msg": "success",
                    "data": [
                        {
                            "id": 0,
                            "reference_index": 4,
                            "citation_start_offset": trailing_citation_start,
                            "citation_end_offset": trailing_citation_end,
                        }
                    ],
                },
                infer_messages=[
                    {"id": 1, "content": "已选结论"},
                    {"id": 10, "content": "保留结论"},
                ],
                language="zh-CN",
            )

        assert "[已选结论](#inference:1)" not in result["new_report"]
        assert "[保留结论](#inference:0)" in result["new_report"]
        assert result["updated_infer_messages"] == [{"id": 0, "content": "保留结论"}]
        assert result["updated_messages"]["data"][0]["citation_start_offset"] < trailing_citation_start

    @pytest.mark.asyncio
    async def test_synonym_rewrite_remaps_inference_10_to_9_and_shifts_trailing_citation_offsets(self, synonym_rewriter):
        report = (
            "前缀[已选结论](#inference:1)中段"
            "[保留结论](#inference:10)"
            "尾注[[4]](https://d.com)"
        )
        selected = "[已选结论](#inference:1)中段"
        start = report.index(selected)
        end = start + len(selected)
        original_citation_start = report.index("[[4]]")
        original_citation_end = original_citation_start + len("[[4]](https://d.com)")

        with patch.object(synonym_rewriter, "_generate_synonym_rewrite_text", new_callable=AsyncMock) as mock_generate:
            mock_generate.return_value = "改写后"

            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "expand",
                    "selected_text": selected,
                    "start_offset": start,
                    "end_offset": end,
                    "user_instruction": "",
                },
                report_content=report,
                citation_messages={
                    "code": 0,
                    "msg": "success",
                    "data": [
                        {
                            "id": 0,
                            "reference_index": 4,
                            "citation_start_offset": original_citation_start,
                            "citation_end_offset": original_citation_end,
                        }
                    ],
                },
                infer_messages=[{"id": i, "content": f"结论{i}"} for i in range(11)],
                language="zh-CN",
            )

        # 删除 id=1 后，原 id=10 会重排为 9，文本长度缩短 1 个字符，
        # 因此其后的 citation offset 除了改写区间带来的平移外，还应再额外左移 1。
        base_delta = len("改写后") - len(selected)
        assert "[保留结论](#inference:9)" in result["new_report"]
        assert result["updated_infer_messages"][9]["id"] == 9
        assert result["updated_messages"]["data"][0]["citation_start_offset"] == original_citation_start + base_delta - 1
        assert result["updated_messages"]["data"][0]["citation_end_offset"] == original_citation_end + base_delta - 1
