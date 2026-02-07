# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
from typing import List

logger = logging.getLogger(__name__)


def split_into_sentences(text: str) -> List[str]:
    """
    Docstring for split_into_sentences
    
    :param text: Description
    :type text: str
    :return: Description
    :rtype: List[str]
    """

    sentences = []
    current_sentence = ""
    for char in text:
        current_sentence += char
        # Only Chinese
        if char in ['。', '!', '！', '?', '？', ';', '；', '\n']:
            sentences.append(current_sentence.strip())
            current_sentence = ""
    # 处理报告末尾可能没有标点的句子
    if current_sentence.strip():
        sentences.append(current_sentence.strip())
    return sentences


def validate_string_length(text, min_length=0, max_length=None):
    """
    Docstring for validate_string_length
    
    :param text: Description
    :param min_length: Description
    :param max_length: Description
    """
    if text is None:
        return False
    length = len(text)
    if length < min_length:
        return False
    if max_length is not None and length > max_length:
        return False
    return True


def truncate_string(text: str, max_length: int,
                    start: int = 0, suffix: str = "...") -> str:
    """
    Docstring for truncate_string
    
    :param text: Description
    :type text: str
    :param max_length: Description
    :type max_length: int
    :param start: Description
    :type start: int
    :param suffix: Description
    :type suffix: str
    :return: Description
    :rtype: str
    """

    if text is None or max_length <= 0:
        return ""

    try:
        text = str(text).strip()
    except Exception as e:
        logger.error(f"Error converting text {type(text).__name__} to string: {e}")
        return ""

    length = len(text)
    start = max(0, min(start, length))
    end = max(start, min(start + max_length, length))
    sliced = text[start:end]

    if end < length and len(suffix) < len(sliced):
        return sliced[:-len(suffix)] + suffix

    return sliced
