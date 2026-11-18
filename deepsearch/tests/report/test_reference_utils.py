import pytest

from openjiuwen_deepsearch.algorithm.report.report import (
    Reporter,
    _deduplicate_and_renumber_ref,
    _deduplicate_and_renumber_refs,
    _replace_citations_and_classified_index,
)
from openjiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH


@pytest.mark.parametrize("content, refs, lang, expected", [
    # 中文引用
    ("这是正文", ["参考A", "参考B"], CHINESE,
     "这是正文\n## 参考文章\n[1] 参考A\n[2] 参考B"),

    # 英文引用
    ("This is content", ["Ref A", "Ref B"], ENGLISH,
     "This is content\n## References\n[1] Ref A\n[2] Ref B"),

    # 没有引用
    ("正文内容", [], CHINESE, "正文内容"),

    # 没有正文但有引用（返回空字符串）
    ("", ["Ref A"], ENGLISH, ""),

    # 未知语言 → 默认走英文逻辑
    ("Contenu", ["Réf A"], "fr",
     "Contenu\n## References\n[1] Réf A"),
])
def test_add_references(content, refs, lang, expected):
    result = Reporter.add_references(content, refs, lang)
    assert result == expected


def test_deduplicate_and_renumber_with_ref_empty_input():
    text = ""
    result, mapping = _deduplicate_and_renumber_ref(text)
    assert result == ""
    assert mapping == {}


def test_deduplicate_and_renumber_with_ref_single_reference():
    text = "[1] First reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    assert result == "[1] First reference"
    assert mapping == {"1-1": 1}


def test_deduplicate_and_renumber_with_ref_duplicate_references_same_paragraph():
    text = "[1] First reference\n[2] First reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    # 去重后只保留一个
    assert result == "[1] First reference"
    # 两个 key 都映射到同一个编号
    assert mapping == {"1-1": 1, "1-2": 1}


def test_deduplicate_and_renumber_with_multiple_paragraphs_and_sections():
    text = "[1] First reference\n\n[1] Second reference\n[2] First reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    # 应该有两个不同的引用
    assert "[1] First reference" in result
    assert "[2] Second reference" in result
    # 映射按"空行分隔的引用块"给出段落号，不再随空行数量漂移
    assert mapping["1-1"] == 1  # 第一段第一条
    assert mapping["2-1"] == 2  # 第二段第一条
    assert mapping["2-2"] == 1  # 第二段第二条重复了第一段的内容


def test_deduplicate_and_renumber_refs_paragraph_ids_match_block_order():
    # 引用块内部有空行时，旧实现会把段落号推成 3，与消费端 f"{i + 1}-{index}" 对不上
    blocks = ["[1] A\n\n[2] B", "[1] C"]
    _, mapping = _deduplicate_and_renumber_refs(blocks)
    assert mapping == {"1-1": 1, "1-2": 2, "2-1": 3}


def test_deduplicate_and_renumber_refs_skips_empty_blocks():
    blocks = ["", "[1] A", ""]
    result, mapping = _deduplicate_and_renumber_refs(blocks)
    assert result == "[1] A"
    assert mapping == {"2-1": 1}


def test_deduplicate_and_renumber_with_ignore_lines_without_reference():
    text = "This is not a ref\n[1] Valid reference"
    result, mapping = _deduplicate_and_renumber_ref(text)
    assert result == "[1] Valid reference"
    assert mapping == {"1-1": 1}


@pytest.mark.parametrize("paragraphs, classified_contents, ref_map, expected", [
    # 测试用例1：正常情况
    (
            ["This is a paragraph [citation:1].", "Another paragraph [citation:2]."],
            [
                [{"index": 1, "content": "First citation"}],
                [{"index": 2, "content": "Second citation"}]
            ],
            {"1-1": 10, "2-2": 20},
            (["This is a paragraph [citation:10].", "Another paragraph [citation:20]."], [
                [{"index": 10, "content": "First citation"}],
                [{"index": 20, "content": "Second citation"}]
            ])
    ),

    # 测试用例2：没有引用映射
    (
            ["This is a paragraph [citation:1].", "Another paragraph [citation:2]."],
            [
                [{"index": 1, "content": "First citation"}],
                [{"index": 2, "content": "Second citation"}]
            ],
            {},
            (["This is a paragraph [citation:1].", "Another paragraph [citation:2]."], [
                [{"index": 1, "content": "First citation"}],
                [{"index": 2, "content": "Second citation"}]
            ])
    ),

    # 测试用例3：没有分类内容
    (
            ["This is a paragraph [citation:1].", "Another paragraph [citation:2]."],
            [],
            {"1-1": 10, "2-2": 20},
            (["This is a paragraph [citation:1].", "Another paragraph [citation:2]."], [])
    ),

    # 测试用例4：空段落及分类内容
    (
            [],
            [],
            {"1-1": 10, "2-2": 20},
            ([], [])
    )
])
def test_replace_citations_and_classified_index(paragraphs, classified_contents, ref_map, expected):
    result = _replace_citations_and_classified_index(paragraphs, classified_contents, ref_map)
    assert result == expected


def test_replace_citations_is_not_cascaded():
    # 第二段：1->2（重复引用）、2->3（新引用）。逐条 re.sub 会把 [citation:1]
    # 先换成 [citation:2]，再把刚生成的 [citation:2] 换成 [citation:3]。
    _, ref_map = _deduplicate_and_renumber_refs(["[1] x\n[2] y", "[1] y\n[2] z"])
    assert ref_map == {"1-1": 1, "1-2": 2, "2-1": 2, "2-2": 3}

    paragraphs = ["P0 [citation:1] [citation:2]", "P1 [citation:1] [citation:2]"]
    classified = [
        [{"index": 1, "content": "x"}, {"index": 2, "content": "y"}],
        [{"index": 1, "content": "y"}, {"index": 2, "content": "z"}],
    ]
    updated_paragraphs, updated_classified = _replace_citations_and_classified_index(
        paragraphs, classified, ref_map
    )
    assert updated_paragraphs == [
        "P0 [citation:1] [citation:2]",
        "P1 [citation:2] [citation:3]",
    ]
    assert [[item["index"] for item in c] for c in updated_classified] == [[1, 2], [2, 3]]


def test_replace_citations_uses_paragraph_id_from_block_order():
    # 生产者按段落顺序编号后，第二段的重编号不再因为空行而失效
    _, ref_map = _deduplicate_and_renumber_refs(["[1] a\n\n[2] b", "[1] c"])
    paragraphs = ["P0 [citation:1]", "P1 [citation:1]"]
    classified = [[{"index": 1, "content": "a"}], [{"index": 1, "content": "c"}]]
    updated_paragraphs, updated_classified = _replace_citations_and_classified_index(
        paragraphs, classified, ref_map
    )
    assert updated_paragraphs == ["P0 [citation:1]", "P1 [citation:3]"]
    assert updated_classified[1][0]["index"] == 3
