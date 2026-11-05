# -*- coding: UTF-8 -*-
"""The extraction mode changes conditional user instructions while the system stays stable."""
from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages


def test_passages_extractor_keeps_static_schema_for_both_time_modes():
    enabled = build_prompt_messages("passages_extractor", {"extract_content_time": True})
    disabled = build_prompt_messages("passages_extractor", {"extract_content_time": False})
    assert enabled[0] == disabled[0]
    assert "Content Time Extraction" not in enabled[0]["content"]
    assert '"content_time":' not in enabled[0]["content"]
    assert "Content Time Extraction" in enabled[-1]["content"]
    assert '"content_time":' in enabled[-1]["content"]
    assert "Content Time Extraction" not in disabled[-1]["content"]
    assert '"content_time":' not in disabled[-1]["content"]
    assert "extract_content_time: True" in enabled[-1]["content"]
    assert "extract_content_time: False" in disabled[-1]["content"]
    assert "CURRENT TIME" not in "\n".join(m["content"] for m in enabled)
