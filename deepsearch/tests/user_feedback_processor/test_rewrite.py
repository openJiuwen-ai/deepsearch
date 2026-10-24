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
        with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.build_prompt_messages") as mock_prompt:
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
        with patch(f"{SYNONYM_REWRITER_MODULE_PATH}.build_prompt_messages", return_value=[{"role": "system", "content": "prompt"}]):
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
