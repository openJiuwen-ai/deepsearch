import base64
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openjiuwen_deepsearch.algorithm.report_template.template_generator import TemplateGenerator
from openjiuwen_deepsearch.common.exception import CustomValueException


@pytest.fixture
def mock_logger():
    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.logger") as m:
        yield m


@pytest.fixture
def mock_not_sensitive():
    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.LogManager.is_sensitive",
               return_value=False):
        yield


@pytest.fixture
def mock_config():
    class Cfg:
        service_config = type("c", (), {"template_max_generate_retry_num": 2})

    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.Config", return_value=Cfg()):
        yield


@pytest.fixture
def mock_apply_prompt():
    with patch(
            "openjiuwen_deepsearch.algorithm.report_template.template_generator.apply_system_prompt",
            return_value=[{"role": "system", "content": "sys"}]
    ):
        yield


@pytest.fixture
def mock_pdf_convert():
    with patch(
            "openjiuwen_deepsearch.algorithm.report_template.template_generator.TemplateUtils.pdf_base64_to_markdown",
            return_value="md"
    ):
        yield


@pytest.mark.asyncio
async def test_process_step_success(mock_logger, mock_not_sensitive, mock_config, mock_apply_prompt):
    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.ainvoke_llm_with_stats",
               new_callable=AsyncMock, return_value={"content": "OK"}) as mock_llm:
        res = await TemplateGenerator._process_step(
            llm=MagicMock(),
            prompt_name="test",
            max_retries=2,
            file_content="test file content"
        )
        assert res == "OK"
        assert mock_llm.await_count == 1


@pytest.mark.asyncio
async def test_process_step_retry_then_success(mock_logger, mock_not_sensitive, mock_config, mock_apply_prompt):
    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.ainvoke_llm_with_stats",
               new_callable=AsyncMock, side_effect=[{"content": ""}, {"content": "OK"}]) as mock_llm:
        res = await TemplateGenerator._process_step(
            llm=MagicMock(),
            prompt_name="test",
            max_retries=2,
            file_content="test file content"
        )
        assert res == "OK"
        assert mock_llm.await_count == 2


@pytest.mark.asyncio
async def test_process_step_fail_after_retries(mock_logger, mock_not_sensitive, mock_config, mock_apply_prompt):
    async def raise_exc(*args, **kwargs):
        raise RuntimeError("LLM fail")

    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.ainvoke_llm_with_stats",
               new_callable=AsyncMock, side_effect=raise_exc):
        with pytest.raises(CustomValueException):
            await TemplateGenerator._process_step(
                llm=MagicMock(),
                prompt_name="test",
                max_retries=2,
                file_content="test file content"
            )


@pytest.mark.asyncio
async def test_generate_template_success_report(
        mock_logger, mock_not_sensitive, mock_config, mock_pdf_convert, mock_apply_prompt):
    module_path = "openjiuwen_deepsearch.algorithm.report_template.template_generator"

    # Step 1 → structure, Step 2 → semantic
    with patch(f"{module_path}.ainvoke_llm_with_stats", new_callable=AsyncMock,
               side_effect=[{"content": "# structure"}, {"content": "semantic"}]):
        with (
            patch(f"{module_path}.AgentConfig"),
            patch(f"{module_path}.create_llm_obj"),
            patch(f"{module_path}.llm_context"),
            patch(f"{module_path}.TemplateUtils.postprocess_structure", return_value="# processed"),
            patch(f"{module_path}.TemplateUtils.valid_report_suffix", return_value=".pdf")
        ):
            file_stream = base64.b64encode(b"test file stream").decode()
            res = await TemplateGenerator.generate_template(
                file_name="test.pdf",
                file_stream=file_stream,
                is_template=False,
                agent_config={"llm_config": {"model_name": "qwen"}},
            )

            assert res["status"] == "success", f"Failed with: {res.get('error_message')}"
            assert res["template_content"] != ""


@pytest.mark.asyncio
async def test_generate_template_bad_signal(
        mock_logger, mock_not_sensitive, mock_config, mock_pdf_convert, mock_apply_prompt):
    with patch("openjiuwen_deepsearch.algorithm.report_template.template_generator.ainvoke_llm_with_stats",
               new_callable=AsyncMock, side_effect=[
                {"content": "# structure"},
                {"content": "Error found"}
            ]):
        with patch(
                "openjiuwen_deepsearch.algorithm.report_template.template_generator."
                "TemplateUtils.postprocess_structure",
                return_value="# processed"):
            file_stream = base64.b64encode(b"x").decode()

            res = await TemplateGenerator.generate_template(
                file_name="test.pdf",
                file_stream=file_stream,
                is_template=False,
                agent_config={"llm_config": {"model_name": "qwen"}},
            )

            assert res["status"] == "fail"


@pytest.mark.asyncio
async def test_generate_template_is_template(
        mock_logger, mock_not_sensitive, mock_config, mock_pdf_convert):
    module_path = "openjiuwen_deepsearch.algorithm.report_template.template_generator"

    with patch(f"{module_path}.AgentConfig"), patch(f"{module_path}.create_llm_obj"), patch(
            f"{module_path}.llm_context"):
        with patch(f"{module_path}.TemplateUtils.postprocess_structure_keep_content", return_value="KEPT"), \
                patch(f"{module_path}.TemplateUtils.valid_template_suffix", return_value=".pdf"):
            file_stream = base64.b64encode(b"test file stream").decode()

            res = await TemplateGenerator.generate_template(
                file_name="test.pdf",
                file_stream=file_stream,
                is_template=True,
                agent_config={"llm_config": {"model_name": "qwen"}},
            )

            assert res["status"] == "success", f"Failed with: {res.get('error_message')}"
            assert base64.b64decode(res["template_content"]).decode() == "KEPT"
