import os
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.report.report import Reporter
from openjiuwen_deepsearch.config.config import LLMConfig
from openjiuwen_deepsearch.llm.llm_wrapper import create_llm_obj
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context


def _required_llm_env() -> dict[str, str]:
    keys = ["LLM_MODEL_NAME", "LLM_MODEL_TYPE", "LLM_BASE_URL", "LLM_API_KEY"]
    values = {key: os.environ.get(key, "") for key in keys}
    missing = [key for key, value in values.items() if not value]
    if missing:
        pytest.skip("Missing LLM experiment env vars: " + ", ".join(missing))
    return values


def _llm_config_from_env() -> LLMConfig:
    env = _required_llm_env()
    return LLMConfig(
        model_name=env["LLM_MODEL_NAME"],
        model_type=env["LLM_MODEL_TYPE"],
        base_url=env["LLM_BASE_URL"],
        api_key=bytearray(env["LLM_API_KEY"], encoding="utf-8"),
        hyper_parameters={
            "temperature": float(os.environ.get("LLM_TEMPERATURE", "0")),
            "top_p": float(os.environ.get("LLM_TOP_P", "0.8")),
        },
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_LLM_EXPERIMENTS") != "1",
    reason="Set RUN_LLM_EXPERIMENTS=1 to run real LLM experiments.",
)
async def test_llm_sub_report_preserves_required_table_and_itemized_fields():
    llm_config = _llm_config_from_env()
    token = llm_context.set({llm_config.model_name: create_llm_obj(llm_config)})
    try:
        reporter = Reporter(llm_config.model_name)
        current_inputs = {
            "language": "en",
            "section_idx": "3",
            "section_task": "3 Program Review",
            "section_description": (
                "Create the required summary table and keep each program separate."
            ),
            "origin_query": (
                "Create a summary table with columns: Country, Program Name, Program Type, "
                "Program Description. For each program, specify who was excluded and the concrete "
                "reason for exclusion."
            ),
            "report_task": "Evaluate social protection program exclusions.",
            "current_outline": "1 Context\n2 Failure Categories\n3 Program Review",
            "sub_section_outline": "3 Program Review\n3.1 Program Summary",
            "current_subsection": "3.1 Program Summary",
            "classified_content": [
                {
                    "index": 1,
                    "doc_time": "2023",
                    "original_content": (
                        "Country: India. Program Name: Program A. Program Type: cash transfer. "
                        "Program Description: provides targeted transfers to low-income households. "
                        "Excluded group: migrant workers. Concrete reason: registration required local documents."
                    ),
                    "scores": {"authority": 9, "relevance": 9, "answerability": 9, "data_density": 9},
                },
                {
                    "index": 2,
                    "doc_time": "2023",
                    "original_content": (
                        "Country: Nepal. Program Name: Program B. Program Type: food support. "
                        "Program Description: provides food assistance to poor households. "
                        "Excluded group: remote households. Concrete reason: distribution points were too far away."
                    ),
                    "scores": {"authority": 9, "relevance": 9, "answerability": 9, "data_density": 9},
                },
            ],
            "sub_section_references": [],
            "sub_report_background_knowledge": [],
            "report_type": "professional",
            "paragraph_style": "concise",
            "visualization_enable": False,
        }

        with patch.object(
            reporter,
            "_generate_sub_report_sidecar",
            new_callable=AsyncMock,
            return_value={"sidecar": None, "summary": "summary", "warning": ""},
        ):
            result = await reporter._write_subsection_reports(current_inputs)
    finally:
        llm_context.reset(token)

    assert result["success"] is True
    content = current_inputs["sub_report_content"]
    assert "# Program Review" in content
    assert "## Program Summary" in content
    assert "| Country | Program Name | Program Type | Program Description |" in content
    assert "Program A" in content
    assert "Program B" in content
    assert "migrant workers" in content
    assert "remote households" in content
    assert "local documents" in content
    assert "too far away" in content
