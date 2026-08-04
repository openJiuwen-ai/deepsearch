from openjiuwen_deepsearch.algorithm.chart_generation.utils import remove_mermaid_code_blocks


def test_remove_mermaid_code_blocks_keeps_non_mermaid_markdown_unchanged():
    markdown = "正文前\n\n\n```python\ngraph = {'A': 'B'}\n```\n\n正文后\n"

    assert remove_mermaid_code_blocks(markdown) == markdown


def test_remove_mermaid_code_blocks_removes_fenced_mermaid_and_preserves_text():
    markdown = (
        "正文前\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "  A --> B\n"
        "```\n\n"
        "正文后"
    )

    cleaned = remove_mermaid_code_blocks(markdown)

    assert "```mermaid" not in cleaned
    assert "flowchart TD" not in cleaned
    assert "正文前" in cleaned
    assert "正文后" in cleaned


def test_remove_mermaid_code_blocks_removes_unlabeled_mermaid_block():
    markdown = "正文前\n\n```\nflowchart TD\n  A --> B\n```\n\n正文后"

    cleaned = remove_mermaid_code_blocks(markdown)

    assert "```" not in cleaned
    assert "flowchart TD" not in cleaned
    assert cleaned == "正文前\n\n正文后"


def test_remove_mermaid_code_blocks_removes_indented_mermaid_block():
    markdown = "正文前\n\n    flowchart TD\n      A --> B\n\n正文后"

    cleaned = remove_mermaid_code_blocks(markdown)

    assert "flowchart TD" not in cleaned
    assert cleaned == "正文前\n\n正文后"
