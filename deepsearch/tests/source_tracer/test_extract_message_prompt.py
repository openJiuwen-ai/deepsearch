"""最终引用校验的 LLM 输出提示词契约。"""

from openjiuwen_deepsearch.algorithm.prompts.message_builder import build_prompt_messages


def test_extract_message_prompt_uses_runtime_schema_and_valid_json_example():
    """模板字段必须与 CitationVerifyResearch 的解析字段一致。"""
    messages = build_prompt_messages("extract_message_prompt", {"datas": []})
    prompt = "\n".join(message["content"] for message in messages)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "marked_citation_content" in prompt
    assert "`mark_citation_content`" not in prompt
    assert "// if `score`" not in prompt
