from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import strip_markup_in_range
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
            "前缀[checked_citation:0][[1]](https://a.com)"
            "这是要改写的段落[checked_citation:1][[2]](https://b.com)结束"
            "[checked_citation:2][[3]](https://c.com)尾部"
        )
        start = report.index("这是要改写的段落")
        end = start + len("这是要改写的段落[checked_citation:1][[2]](https://b.com)结束")
        removed_start = report.index("[checked_citation:1]")
        removed_end = removed_start + len("[checked_citation:1][[2]](https://b.com)")

        stripped_text, removed_ranges, removed_inference_ids = strip_markup_in_range(report, start, end)

        assert removed_ranges == {(removed_start, removed_end)}
        assert removed_inference_ids == []
        assert "[checked_citation:0][[1]]" in stripped_text
        assert "[checked_citation:2][[3]]" in stripped_text
        assert "[checked_citation:1][[2]]" not in stripped_text

    def test_strip_citations_keeps_plain_citation_format_when_trace_source_is_disabled(self):
        report = "前缀这是要改写的段落[citation: 2]结束[citation: 3]尾部"
        start = report.index("这是要改写的段落")
        end = start + len("这是要改写的段落[citation: 2]结束")
        removed_start = report.index("[citation: 2]")
        removed_end = removed_start + len("[citation: 2]")

        stripped_text, removed_ranges, removed_inference_ids = strip_markup_in_range(report, start, end)

        assert removed_ranges == {(removed_start, removed_end)}
        assert removed_inference_ids == []
        assert "[citation: 2]" not in stripped_text
        assert "[citation: 3]" in stripped_text

    def test_strip_citations_removes_multiple_citations_within_selection(self):
        report = (
            "前缀"
            "需要改写[checked_citation:1][[2]](https://b.com)的段落[checked_citation:2][[3]](https://c.com)内容"
            "尾部[checked_citation:3][[4]](https://d.com)"
        )
        start = report.index("需要改写")
        end = report.index("内容") + len("内容")

        stripped_text, removed_ranges, removed_inference_ids = strip_markup_in_range(report, start, end)

        assert len(removed_ranges) == 2
        assert removed_inference_ids == []
        assert "[checked_citation:1][[2]]" not in stripped_text
        assert "[checked_citation:2][[3]]" not in stripped_text
        assert "[checked_citation:3][[4]]" in stripped_text

    def test_strip_citations_rewrites_inference_markers_to_plain_text_and_collects_ids(self):
        report = "前缀[结论A](#inference:1)中间[结论B](#inference:3)尾部"
        start = report.index("[结论A]")
        end = report.index("尾部")

        stripped_text, removed_ranges, removed_inference_ids = strip_markup_in_range(report, start, end)

        assert removed_ranges == set()
        assert removed_inference_ids == [1, 3]
        assert stripped_text == "前缀结论A中间结论B尾部"

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
                language="zh-CN",
            )

        assert result["rewritten_text"] == "改写后的文本"
        assert result["new_report"] == "改写后的文本后续内容"

    @pytest.mark.asyncio
    async def test_synonym_rewrite_strips_plain_citation_format_without_source_tracer_metadata(self, synonym_rewriter):
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
                language="zh-CN",
            )

        mock_generate.assert_awaited_once_with(
            action="expand",
            original_text="需要改写的段落",
            language="zh-CN",
            user_instruction="",
        )
        assert result["new_report"] == "前缀改写后的段落尾部"

    @pytest.mark.asyncio
    async def test_full_rewrite_expand_flow_keeps_citation_messages_intact(self):
        report = (
            "引言部分。[checked_citation:0][[1]](https://a.com)"
            "这是需要扩写的段落内容。[checked_citation:1][[2]](https://b.com)结论部分。"
        )
        selected = "这是需要扩写的段落内容。[checked_citation:1][[2]](https://b.com)"
        start = report.index("这是需要扩写的段落内容。")
        end = start + len(selected)
        selected_citation = "[checked_citation:1][[2]](https://b.com)"
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
                        language="zh-CN",
                    )

        assert "引言部分" in result["new_report"]
        assert "结论部分" in result["new_report"]
        assert "[checked_citation:0][[1]]" in result["new_report"]
        assert "[checked_citation:1][[2]]" not in result["new_report"]
        assert result["rewritten_text"] == "这是经过详细扩写后的段落内容，补充了技术细节和实现方案。"
        assert result["rewritten_start_offset"] == start
        assert result["rewritten_end_offset"] == start + len(result["rewritten_text"])

    @pytest.mark.asyncio
    async def test_full_rewrite_expand_flow_keeps_multiple_citation_messages_intact(self):
        report = (
            "前言"
            "需要扩写[checked_citation:0][[2]](https://b.com)的段落[checked_citation:1][[3]](https://c.com)内容"
            "尾注[checked_citation:2][[4]](https://d.com)"
        )
        selected = "需要扩写[checked_citation:0][[2]](https://b.com)的段落[checked_citation:1][[3]](https://c.com)内容"
        start = report.index("需要扩写")
        end = start + len(selected)
        citation_marker_2 = "[checked_citation:0][[2]](https://b.com)"
        citation_marker_3 = "[checked_citation:1][[3]](https://c.com)"
        citation_marker_4 = "[checked_citation:2][[4]](https://d.com)"

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
                        language="zh-CN",
                    )

        assert citation_marker_2 not in result["new_report"]
        assert citation_marker_3 not in result["new_report"]
        assert citation_marker_4 in result["new_report"]

    @pytest.mark.asyncio
    async def test_synonym_rewrite_preserves_trailing_citation_offsets(self, synonym_rewriter):
        trailing_citation = "[checked_citation:0][[4]](https://d.com)"
        report = f"前言需要精简的段落内容尾注{trailing_citation}"
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
                language="zh-CN",
            )

        assert result["new_report"] == f"前言精简后尾注{trailing_citation}"

    @pytest.mark.asyncio
    async def test_synonym_rewrite_keeps_unselected_duplicate_reference_instances(self, synonym_rewriter):
        report = (
            "first shared citation [checked_citation:0][[1]](https://shared.com) needs rewrite. "
            "later there is another shared citation [checked_citation:1][[1]](https://shared.com) that should remain."
        )
        selected = "first shared citation [checked_citation:0][[1]](https://shared.com) needs rewrite."
        start = report.index(selected)
        end = start + len(selected)
        first_citation = "[checked_citation:0][[1]](https://shared.com)"
        second_citation = "[checked_citation:1][[1]](https://shared.com)"
        first_citation_start = report.index(first_citation)
        second_citation_start = report.index(second_citation)
        citation_len = len(first_citation)
        rewritten_text = "rewritten first segment."

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
                language="en-US",
            )

        assert second_citation in result["new_report"]

    @pytest.mark.asyncio
    async def test_synonym_rewrite_preserves_inference_messages(self, synonym_rewriter):
        """验证同义改写会保留未选中 inference 元数据与 citation 元数据。"""
        trailing_citation = "[checked_citation:0][[4]](https://d.com)"
        report = (
            "前缀[已选结论](#inference:1)中段"
            "[保留结论](#inference:10)"
            f"尾注{trailing_citation}"
        )
        selected = "[已选结论](#inference:1)中段"
        start = report.index(selected)
        end = start + len(selected)
        trailing_citation_start = report.index(trailing_citation)
        trailing_citation_end = trailing_citation_start + len(trailing_citation)
        infer_messages = [
            {"id": 1, "content": "已选结论"},
            {"id": 10, "content": "保留结论"},
        ]

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
                language="zh-CN",
            )

        assert "[已选结论](#inference:1)" not in result["new_report"]
        assert "[保留结论](#inference:10)" in result["new_report"]

    @pytest.mark.asyncio
    async def test_synonym_rewrite_preserves_metadata_after_checked_citation_cleanup(self, synonym_rewriter):
        """验证清理 checked citation 后，元数据仍按约定原样透传。"""
        report = "前缀[checked_citation:0][[1]](https://a.com)选中内容[保留结论](#inference:3)后缀"
        start_offset = report.index("选中内容")
        end_offset = report.index("后缀")
        citation_messages = {"code": 0, "msg": "success", "data": [{"id": 0, "reference_index": 1}]}
        with patch.object(
            synonym_rewriter,
            "_generate_synonym_rewrite_text",
            new_callable=AsyncMock,
            return_value="新的选中内容",
        ):
            result = await synonym_rewriter.synonym_rewrite(
                feedback={
                    "action": "expand",
                    "selected_text": report[start_offset:end_offset],
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                },
                report_content=report,
                language="zh-CN",
            )

        assert result["new_report"] == "前缀[checked_citation:0][[1]](https://a.com)新的选中内容后缀"
