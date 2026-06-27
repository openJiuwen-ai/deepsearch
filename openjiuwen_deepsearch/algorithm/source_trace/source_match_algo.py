# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
溯源匹配算法模块：事实与引用内容的精确/模糊匹配。

提供三种核心函数：
  - exact_match():    精确子串匹配（覆盖率≥50% 视为 exact）
  - fuzzy_match():    基于 SequenceMatcher 的句子级模糊匹配
  - classify_and_match(): 综合分类与匹配策略（exact → fuzzy → no_match）

设计原则：
  - 与防篡改用途的 find_matches()（citation_verify_research.py）完全解耦，不复用其逻辑
  - 复用 split_into_sentences（text_utils.py）进行分句，与 content_analyzer.py 保持一致
  - 使用句子级比较而非字符级滑动窗口
"""

import difflib
import logging
import re
from typing import List, Tuple

from openjiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences

logger = logging.getLogger(__name__)

# 句子尾部标点，用于匹配前剥离
_TRAILING_PUNCT = re.compile(r'[\s，。？！；：、,.;:!?]+$')



def _strip_trailing_punct(text: str) -> str:
    """剥离句子末尾的标点与空白字符，用于匹配前的标准化处理。"""
    return _TRAILING_PUNCT.sub('', text)


def _extract_citation_fragment(citation_content: str, stripped_key: str) -> str:
    """
    从 citation_content 中提取与 stripped_key 匹配的实际文本片段。

    当 fact 句子剥离标点后的 key 存在于 citation_content 中时，
    返回 citation_content 中从匹配位置开始到 key 结束的原文，
    而不是返回 fact 句子本身（fact 可能与 citation 有标点差异）。

    例如：citation="事实", fact="事实。" → key="事实" → 返回 "事实"（citation原文）

    Args:
        citation_content: 引用来源文本
        stripped_key: 剥离标点后的 fact 句子

    Returns:
        citation_content 中与 stripped_key 匹配的原文片段；
        如果未找到则返回空字符串。
    """
    pos = citation_content.find(stripped_key)
    if pos == -1:
        return ""
    # 从 citation_content 中提取 key 对应的原文片段
    return citation_content[pos:pos + len(stripped_key)]


def exact_match(citation_content: str, fact: str) -> Tuple[List[str], float]:
    """
    精确匹配：在 citation_content 中查找 fact 的各个句子（子串精确匹配）。

    匹配步骤：
      1. 将 fact 按中文标点切分为句子列表
      2. 对每个句子剥离末尾标点后，在 citation_content 中进行子串搜索
      3. 收集命中的 citation 原文片段作为 marked_citation_content
         （而非 fact 句子原文，确保返回的是 citation 中实际存在的文本）
      4. 计算覆盖率并映射到分数：score = 0.85 + coverage_rate × 0.15

    计分规则：
      - 无匹配时 score = 0.85（coverage_rate = 0）
      - 全覆盖时 score = 1.0（coverage_rate = 1.0）
      - score 恒 ≥ 0.85

    Boundary cases:
      - citation_content 或 fact 为空 → ([], 0.0)
      - fact 分句后无可匹配句子 → ([], 0.0)

    Args:
        citation_content: 引用来源文本（待搜索文本）
        fact: 待验证的事实文本

    Returns:
        Tuple[List[str], float]: (marked_citation_content, score)
    """
    if not citation_content or not fact:
        return ([], 0.0)

    fact_sentences = split_into_sentences(fact)
    if not fact_sentences:
        return ([], 0.0)

    total_fact_chars = sum(len(s) for s in fact_sentences)
    if total_fact_chars == 0:
        return ([], 0.0)

    matched_chars = 0
    matched_fragments: List[str] = []

    for sentence in fact_sentences:
        key = _strip_trailing_punct(sentence)
        if not key:
            continue
        # 精确子串匹配：剥离标点后的 key 存在于 citation_content 中
        if key in citation_content:
            # 返回 citation 中实际存在的文本片段，而非 fact 句子原文
            # 例如 citation="事实", fact="事实。" → 返回 "事实"
            citation_fragment = _extract_citation_fragment(citation_content, key)
            if citation_fragment:
                matched_fragments.append(citation_fragment)
                matched_chars += len(sentence)

    coverage_rate = matched_chars / total_fact_chars
    score = 0.85 + coverage_rate * 0.15
    return (matched_fragments, score)


def _find_best_fuzzy_fragment(
    sentence_key: str,
    citation_sentences: List[str],
    threshold: float
) -> str:
    """
    在引用句子列表中查找与 sentence_key 最相似的片段（句子级比较）。

    Args:
        sentence_key: 待匹配的事实句子（已剥离末尾标点）
        citation_sentences: 引用内容切分后的句子列表
        threshold: 相似度阈值（SequenceMatcher.ratio()）

    Returns:
        str: 最佳匹配句子原文（含标点）；未达阈值时返回空字符串。
    """
    best_ratio = 0.0
    best_sentence = ""

    for cit_sent in citation_sentences:
        cit_key = _strip_trailing_punct(cit_sent)
        if not cit_key:
            continue
        ratio = difflib.SequenceMatcher(None, sentence_key, cit_key).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_sentence = cit_sent

    if best_ratio >= threshold and best_sentence:
        return best_sentence
    return ""


def fuzzy_match(
    citation_content: str,
    fact: str,
    threshold: float = 0.8
) -> Tuple[List[str], float]:
    """
    句子级模糊匹配：在引用内容中查找与 fact 各句相似的片段。

    匹配步骤：
      1. 将 fact 和 citation_content 各自按中文标点分句
      2. 对 fact 的每个句子：先尝试精确子串匹配（剥离标点后 key in citation_content），
         否则使用 SequenceMatcher 进行句子级模糊匹配（非字符级滑动窗口）
      3. 收集命中的引用原文片段作为 marked_citation_content
      4. 按覆盖率计算得分：score = coverage_rate × 0.7，结果限制在 [0.3, 0.7]

    计分规则：
      - coverage_rate 为 fact 中被匹配的字符占比
      - 任何命中均 ≥ 0.3，全覆盖 = 0.7
      - 无命中时 score = 0.0

    Boundary cases:
      - citation_content 或 fact 为空 → ([], 0.0)
      - fact 分句后无可匹配内容 → ([], 0.0)

    Args:
        citation_content: 引用来源文本
        fact: 待验证的事实文本
        threshold: 模糊匹配相似度阈值（SequenceMatcher.ratio() ≥ threshold 视为命中）

    Returns:
        Tuple[List[str], float]: (matched_citation_fragments, score)
    """
    if not citation_content or not fact:
        return ([], 0.0)

    fact_sentences = split_into_sentences(fact)
    if not fact_sentences:
        return ([], 0.0)

    citation_sentences = split_into_sentences(citation_content)
    total_fact_chars = sum(len(s) for s in fact_sentences)
    if total_fact_chars == 0:
        return ([], 0.0)

    matched_chars = 0
    matched_fragments: List[str] = []

    for sentence in fact_sentences:
        key = _strip_trailing_punct(sentence)
        if not key:
            continue

        # 优先精确匹配（剥离标点后子串搜索）
        if key in citation_content:
            matched_fragments.append(sentence)
            matched_chars += len(sentence)
            continue

        # 句子级模糊匹配（与 citation 各句比较 SequenceMatcher.ratio()）
        best_fragment = _find_best_fuzzy_fragment(
            key, citation_sentences, threshold
        )
        if best_fragment:
            matched_fragments.append(best_fragment)
            matched_chars += len(sentence)  # 以 fact 句子长度计入覆盖率

    if not matched_fragments:
        return ([], 0.0)

    coverage_rate = matched_chars / total_fact_chars
    score = coverage_rate * 0.7
    # 有命中时下限 0.3，上限 0.7
    score = max(score, 0.3)
    score = min(score, 0.7)
    return (matched_fragments, score)


def classify_and_match(
    citation_content: str,
    fact: str
) -> Tuple[str, List[str], float]:
    """
    综合分类与匹配：依次尝试精确匹配与模糊匹配，返回匹配类型和结果。

    策略：
      1. 先调用 exact_match，覆盖率 ≥ 0.5 → 返回 ("exact", ..., score≥0.85)
      2. 否则调用 fuzzy_match，有任何命中 → 返回 ("fuzzy", ..., score∈[0.3,0.7])
      3. 均无命中 → 返回 ("no_match", [], 0.0)

    Boundary cases:
      - citation_content 或 fact 为空 → ("no_match", [], 0.0)

    Args:
        citation_content: 引用来源文本
        fact: 待验证的事实文本

    Returns:
        Tuple[str, List[str], float]: (match_type, marked_citation_content, score)
            - match_type: "exact" | "fuzzy" | "no_match"
            - score: exact ≥ 0.85；fuzzy ∈ [0.3, 0.7]；no_match = 0.0
    """
    if not citation_content or not fact:
        return ("no_match", [], 0.0)

    # Step 1: 精确匹配判定（覆盖率 ≥ 0.85 视为 exact，确保快速路径要求接近完整覆盖）
    exact_matched, exact_score = exact_match(citation_content, fact)
    if exact_matched:
        total_fact_chars = sum(len(s) for s in split_into_sentences(fact))
        matched_chars = sum(len(s) for s in exact_matched)
        coverage = matched_chars / total_fact_chars if total_fact_chars > 0 else 0.0
        if coverage >= 0.85:
            logger.debug(
                "[MATCH ALGO]: classify_and_match exact - coverage=%.2f, score=%.3f, matched=%d",
                coverage, exact_score, len(exact_matched),
            )
            return ("exact", exact_matched, exact_score)

    # Step 2: 模糊匹配判定（有任何命中即为 fuzzy）
    fuzzy_matched, fuzzy_score = fuzzy_match(citation_content, fact)
    if fuzzy_matched:
        logger.debug(
            "[MATCH ALGO]: classify_and_match fuzzy - score=%.3f, matched=%d",
            fuzzy_score, len(fuzzy_matched),
        )
        return ("fuzzy", fuzzy_matched, fuzzy_score)

    # Step 3: 无命中
    logger.debug("[MATCH ALGO]: classify_and_match no_match")
    return ("no_match", [], 0.0)
