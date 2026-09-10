"""最终引用校验的 LLM 输出提示词契约。"""

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt


def test_extract_message_prompt_uses_runtime_schema_and_valid_json_example():
    """模板字段必须与 CitationVerifyResearch 的解析字段一致。"""
    prompt = apply_system_prompt("extract_message_prompt", {"datas": []})[0]["content"]

    assert "marked_citation_content" in prompt
    assert "`mark_citation_content`" not in prompt
    assert "// if `score`" not in prompt
