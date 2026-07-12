import os

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
async def test_llm_sub_section_outline_preserves_user_specified_second_level_titles():
    llm_config = _llm_config_from_env()
    token = llm_context.set({llm_config.model_name: create_llm_obj(llm_config)})
    try:
        reporter = Reporter(llm_config.model_name)
        result = await reporter._generate_sub_section_outline(
            {
                "language": "en",
                "section_idx": "2",
                "has_template": False,
                "report_type": "professional",
                "paragraph_style": "detailed",
                "report_task": (
                    "Write a policy evaluation report. Part Two should be organized by five categories: "
                    "1. Program Design Flaws 2. Elite Capture 3. Targeting Errors "
                    "4. Invisible Barriers 5. Self-Exclusion."
                ),
                "origin_query": (
                    "Write a policy evaluation report. Part Two should be organized by five categories: "
                    "1. Program Design Flaws 2. Elite Capture 3. Targeting Errors "
                    "4. Invisible Barriers 5. Self-Exclusion."
                ),
                "current_outline": (
                    "1 Problem Context\n"
                    "2 Part Two\n"
                    "3 Reform Options"
                ),
                "section_task": "2 Part Two",
                "section_description": (
                    "For this chapter, preserve these exact subsection titles in this order: "
                    "Program Design Flaws; Elite Capture; Targeting Errors; Invisible Barriers; Self-Exclusion. "
                    "Mention any bullets or data requirements within the relevant subsection scope rather than "
                    "creating extra headings."
                ),
                "sub_section_core_content": [
                    {
                        "title": "Synthetic evidence note",
                        "key_passages": [
                            "Program design flaws, elite capture, targeting errors, invisible barriers, "
                            "and self-exclusion are the five required categories."
                        ],
                    }
                ],
            }
        )
    finally:
        llm_context.reset(token)

    assert result["rs_success"] is True
    outline = result["sub_section_outline"]
    expected_titles = [
        "2.1 Program Design Flaws",
        "2.2 Elite Capture",
        "2.3 Targeting Errors",
        "2.4 Invisible Barriers",
        "2.5 Self-Exclusion",
    ]
    for title in expected_titles:
        assert title in outline
    assert "Background" not in outline
    assert "Summary" not in outline
