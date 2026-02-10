# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import re
from typing import List, Dict, Any, Optional, Tuple

from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from jiuwen_deepsearch.utils.common_utils.text_utils import split_into_sentences

logger = logging.getLogger(__name__)


def _need_preprocess_search_record(search_record: Dict[str, Any]) -> bool:
    """检查搜索记录中是否存在特定字段，如果不存在则不进行搜索记录处理。

    Args:
        search_record (Dict[str, Any]): 搜索记录字典

    Returns:
        bool: 如果存在需要处理的字段则返回True，否则返回False
    """
    content = search_record.get("search_record", [])
    if content:
        return True

    return False


def preprocess_search_record(search_record: Dict[str, Any], max_content_len: int) -> Dict:
    """预处理搜索记录，仅处理列表类型的搜索记录，其余搜索记录维持原样。

    Args:
        search_record (Dict[str, Any]): 原始搜索记录字典
        max_content_len (int): 单个搜索记录内容的最大长度，超过这个长度的搜索记录会做分片处理

    Returns:
        Dict: 预处理后的搜索记录字典，列表类型的记录已被处理
    """
    if _need_preprocess_search_record(search_record) is False:
        logger.warning("[preprocess_search_record] 搜索记录为空")
        return {}
    preprocessed_record = {}

    for key, value in search_record.items():
        # 处理列表类型的值
        if isinstance(value, list):
            logger.info(f"[preprocess_search_record] 处理列表类型为  {key} 的搜索记录:")
            preprocessed_record[key] = process_search_record_list(
                value, max_content_len)
        else:
            preprocessed_record[key] = value

    return preprocessed_record


def _should_process_item(item: Any, processed_list: List[Dict[str, Any]]) -> bool:
    """检查item是否应该被处理。

    Args:
        item (Any): 要检查的搜索记录项
        processed_list (List[Dict[str, Any]]): 经过预处理的搜索记录列表

    Returns:
        bool: 如果item是字典类型、包含必要字段且不是重复项，则返回True
    """
    if not isinstance(item, dict):
        return False

    if not has_required_fields(item):
        return False

    if is_duplicate_record(item, processed_list):
        return False

    return True


def _process_normal_item(item: Dict[str, Any], processed_list: List[Dict[str, Any]]) -> None:
    """处理内容长度正常的搜索记录项。

    Args:
        item (Dict[str, Any]): 要处理的搜索记录项
        processed_list (List[Dict[str, Any]]): 已处理的搜索记录列表

    Returns:
        None: 无返回值，直接修改processed_list
    """
    item_with_index = item.copy()
    item_with_index["index"] = len(processed_list)
    processed_list.append(item_with_index)


def process_search_record_list(items: List[Any], max_content_len: int) -> List[Dict[str, Any]]:
    """处理搜索记录中的列表项，移除不符合标准的记录，对记录进行去重和内容切分。

    Args:
        items (List[Any]): 原始搜索记录列表
        max_content_len (int): 单个搜索记录内容的最大长度

    Returns:
        List[Dict[str, Any]]: 处理后的搜索记录列表，包含有效且去重的记录
    """
    processed_list = []

    for item in items:
        if not _should_process_item(item, processed_list):
            continue

        # 处理内容过长的情况
        if len(item.get('content', '')) > max_content_len:
            handle_long_content(item, processed_list, max_content_len)
        else:
            _process_normal_item(item, processed_list)

    logger.info(
        f"[process_search_record_list]预处理前搜索记录数量: {len(items)}, 预处理后搜索记录数量： {len(processed_list)}")

    return processed_list


def has_required_fields(item: Dict[str, Any]) -> bool:
    """检查搜索记录项是否包含所有必需字段。

    Args:
        item (Dict[str, Any]): 要检查的搜索记录项

    Returns:
        bool: 如果包含title、url和content字段则返回True，否则返回False
    """
    return 'title' in item and 'url' in item and 'content' in item


def is_duplicate_record(item: Dict[str, Any], processed_list: List[Dict[str, Any]]) -> bool:
    """检查搜索记录项是否在已处理列表中重复（仅比较url、title、content三个字段）。

    Args:
        item (Dict[str, Any]): 要检查的搜索记录项
        processed_list (List[Dict[str, Any]]): 已处理的搜索记录列表

    Returns:
        bool: 如果记录已存在则返回True，否则返回False
    """
    # 仅提取需要比较的字段
    key_fields = ['url', 'title', 'content']
    item_for_compare = {}
    for field in key_fields:
        if field in item:
            item_for_compare[field] = item[field]

    for existing_item in processed_list:
        # 仅提取需要比较的字段
        existing_for_compare = {}
        for field in key_fields:
            if field in existing_item:
                existing_for_compare[field] = existing_item[field]

        if item_for_compare == existing_for_compare:
            return True

    return False


def _create_content_chunk(
    item: Dict[str, Any], content: str, start: int,
    max_content_len: int, processed_list: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """创建搜索记录的内容分块。

    Args:
        item (Dict[str, Any]): 原始搜索记录项
        content (str): 原始内容文本
        start (int): 分块起始位置
        max_content_len (int): 分块最大长度
        processed_list (List[Dict[str, Any]]): 已处理的搜索记录列表

    Returns:
        Dict[str, Any]: 包含分块内容的搜索记录项
    """
    chunk_content = content[start:start + max_content_len]
    chunk_item = item.copy()
    chunk_item['content'] = chunk_content
    chunk_item['index'] = len(processed_list)
    return chunk_item


def handle_long_content(item: Dict[str, Any], processed_list: List[Dict[str, Any]], max_content_len: int) -> None:
    """处理内容长度超过限制的搜索记录。

    Args:
        item (Dict[str, Any]): 要处理的搜索记录项
        processed_list (List[Dict[str, Any]]): 已处理的搜索记录列表
        max_content_len (int): 单个搜索记录内容的最大长度

    Returns:
        None: 无返回值，直接修改processed_list
    """
    content = item.get('content', '')

    # 循环拆分直到所有部分都不超过max_content_len字符
    start = 0
    chunk_index = 0

    while start < len(content):
        chunk_item = _create_content_chunk(
            item, content, start, max_content_len, processed_list)
        processed_list.append(chunk_item)

        start += max_content_len
        chunk_index += 1


def _remove_reference_section(report_text: str) -> Tuple[str, str]:
    """删除报告文本中的参考文献或Reference部分。

    Args:
        report_text (str): 原始报告文本

    Returns:
        Tuple[str, str]: (删除的参考文献部分, 去除参考文献后的报告内容)
    """
    # 查找所有markdown标题的位置
    headings = [(m.start(), m.end(), m.group())
                for m in re.finditer(r'^#{1,6}\s.+$', report_text, re.MULTILINE)]

    reference_pattern = r'(?:参考文献|参考文章|Reference Articles)'

    last_ref_match = None

    # 从后向前遍历标题，找到最后一个符合条件的参考文献标题
    for start, _, title_text in reversed(headings):
        # 提取标题文字（去掉#号和空格）
        title_content = re.sub(r'^#{1,6}\s*', '', title_text)

        # 检查标题文字是否匹配参考文献模式
        if re.search(reference_pattern, title_content, re.IGNORECASE):
            # 找到这个标题之后的所有内容
            section_pattern = rf'{re.escape(title_text)}\s*\n.*?(?=\n#{1, 6}\s.+$|$)'
            match = re.search(section_pattern, report_text[start:], re.DOTALL)
            if match:
                last_ref_match = (start, start + match.end())
                break

    if last_ref_match:
        removed_section = report_text[last_ref_match[0]:].rstrip()
        remaining_text = report_text[:last_ref_match[0]].rstrip()
        logger.info("[_remove_reference_section] 已删除参考文献或Reference部分")
        return removed_section, remaining_text

    # 如果没有找到参考文献部分，返回原始文本和空字符串
    return "", report_text


def preprocess_report(report: str) -> Tuple[str, str]:
    """预处理报告文本，删除文末的参考文献部分。

    Args:
        report (str): 原始报告文本

    Returns:
        Tuple[str, str]: (删除的参考文献部分, 去除参考文献后的报告内容)
    """
    # research模式 需要删除原文中最后参考文献标题之后的所有内容，避免影响data提取
    removed_section, remaining_text = _remove_reference_section(report)

    logger.info(f"[preprocess_report] 预处理report完毕")
    return removed_section, remaining_text


def _get_citation_chunk(report: str, citation_start: int, citation_pattern: Optional[str] = None) -> str:
    """获取引用标记前的文本块，当chunk小于5时向前合并更多句子。
    在合并过程中如果遇到citation_pattern，立即终止合并。
    如果检测到图表标题，会向前查找整个mermaid代码块。

    Args:
        report (str): 原始报告文本
        citation_start (int): 引用标记的起始位置
        citation_pattern (Optional[str]): 引用标记的正则表达式模式，默认为None

    Returns:
        str: 引用对应的文本块
    """
    # 提取citation前面的文本
    text_before_citation = report[:citation_start]
    
    # 检查是否是图表标题的情况
    # 图表标题格式: <div style="text-align: center;">...**标题[citation:X]**\n\n</div>
    text_after_citation = report[citation_start:]
    chart_title_suffix_pattern = r'\[\s*citation:\s*\d+\s*\]\*\*[\s\n]*</div>'
    if re.match(chart_title_suffix_pattern, text_after_citation, re.DOTALL):
        # 从后向前查找最近的 ```mermaid
        mermaid_start_pattern = r'```mermaid'
        mermaid_matches = list(re.finditer(mermaid_start_pattern, text_before_citation))
        mermaid_start_match = mermaid_matches[-1] if mermaid_matches else None
        
        if mermaid_start_match:
            mermaid_start_pos = mermaid_start_match.start()
            chart_content = report[mermaid_start_pos:citation_start].strip()
            
            if citation_pattern:
                chart_content = re.sub(citation_pattern, '', chart_content).strip()
            
            if LogManager.is_sensitive():
                logger.info("[VIZ_CITATION] Matched chart content")
            else:
                logger.info(f"[VIZ_CITATION] Matched chart content: {chart_content}")
            
            return chart_content
    
    # 不是图表标题则正常匹配
    sentences = split_into_sentences(text_before_citation.strip())

    # 获取最后一个句子作为初始chunk
    chunk = sentences[-1] if sentences else ""
    if citation_pattern:
        chunk = re.sub(citation_pattern, '', chunk).strip()

    # 如果chunk长度小于5，向前获取更多句子合并
    if len(chunk) < 5 and len(sentences) > 1:
        # 从倒数第二个句子开始向前合并，直到chunk长度>=5或没有更多句子
        merged_chunk = chunk
        for i in range(len(sentences) - 2, -1, -1):
            previous_sentence = sentences[i]

            # 检查前一个句子中是否包含citation_pattern
            if citation_pattern and re.search(citation_pattern, previous_sentence):
                # 如果包含citation_pattern，立即终止合并
                break

            # 合并句子
            merged_chunk = previous_sentence + " " + merged_chunk
            if len(merged_chunk) >= 5:
                break
        chunk = merged_chunk

    return chunk


def _process_citation_match(match, report: str, citation_pattern: str, citation_mapping: Dict[int, Dict], position_offset: int):
    """处理匹配到的引用标记，根据映射关系更新引用内容。

    Args:
        match: 匹配到的引用标记对象
        report (str): 原始报告文本
        citation_pattern (str): 引用标记的正则表达式模式
        citation_mapping (Dict[int, Dict]): 引用映射关系字典
        position_offset (int): 引用标记位置偏移量

    Returns:
        tuple: 包含三个元素的元组
            - 更新后的引用内容 (str)
            - 引用块信息 (Dict[str, Any])
            - 位置偏移量变化值 (int)
    """
    citation_num = int(match.group(1))

    # 获取chunk文本（使用替换前的位置，在原始报告中提取）
    chunk = _get_citation_chunk(report, match.start(), citation_pattern)

    # 清理chunk中的citation标记
    cleaned_chunk = re.sub(citation_pattern, '', chunk).strip()

    # 计算替换后的位置（用于记录在最终报告中的位置）
    citation_start_after_replace = match.start() + position_offset

    # 记录chunk信息到列表中
    chunk_info = {
        "citation_num": citation_num,
        "chunk": cleaned_chunk,
        "_sentence_position": citation_start_after_replace,
        "_is_origin_data": True
    }

    # 生成替换文本
    if citation_num in citation_mapping:
        mapping = citation_mapping[citation_num]
        if mapping:
            replacement_text = f"[source_tracer_result][{mapping['title']}]({mapping['url']})"
        else:
            logger.warning(f"{citation_num} 不存在mapping")
            replacement_text = ""
    else:
        logger.warning(f"未找到引用{citation_num}的映射配置")
        replacement_text = ""

    # 计算位置偏移量
    original_text = match.group(0)
    replacement_length_diff = len(replacement_text) - len(original_text)

    return replacement_text, chunk_info, replacement_length_diff


def _replace_citations_with_custom_mapping(report: str, citation_mapping: Dict[int, Dict]) -> Dict[str, Any]:
    """根据映射字典将文本中的引用标记替换为自定义链接格式。
    记录citation前面的一句话，并清理chunk中的citation标记。

    Args:
        report (str): 包含引用标记的原始文本
        citation_mapping (Dict[int, Dict]): 引用映射字典，键为引用编号，值为包含title和url的字典

    Returns:
        Dict[str, Any]: 包含替换结果的字典，包含以下键：
            - "modified_report": 替换后的报告文本
            - "citation_chunks": 包含引用编号和文本块信息的列表
    """
    # 存储每个citation对应的chunk信息列表
    citation_chunks = []

    # 定义citation标记的正则表达式模式
    citation_pattern = r'\[\s*citation:\s*(\d+)\s*\]'

    # 查找所有引用标记并按照位置排序
    matches = list(re.finditer(citation_pattern, report))
    matches.sort(key=lambda m: m.start())  # 按位置升序排列

    # 初始化替换后的报告和位置偏移量
    modified_report = report
    position_offset = 0

    # 按顺序处理每个引用标记
    for match in matches:
        replacement_text, chunk_info, length_diff = _process_citation_match(
            match, report, citation_pattern, citation_mapping, position_offset)

        citation_chunks.append(chunk_info)

        # 更新位置偏移量
        citation_start_after_replace = match.start() + position_offset
        original_text = match.group(0)

        # 替换文本
        modified_report = (modified_report[:citation_start_after_replace] +
                           replacement_text +
                           modified_report[citation_start_after_replace + len(original_text):])

        # 更新位置偏移量
        position_offset += length_diff

    return {
        "modified_report": modified_report,
        "citation_chunks": citation_chunks
    }


def _build_citation_mapping(classified_content: List) -> Dict:
    """构建引用映射字典，按引用编号(index)分组。

    Args:
        classified_content (List): 原报告自带的引用信息

    Returns:
        Dict: 引用映射字典，键为引用编号，值为包含title、url和content的字典
    """
    citation_mapping = {}

    for content in classified_content:
        # 提取基本信息
        index = content.get("index", 0)
        if index == 0:
            continue
        title = content.get("title", "")
        url = content.get("url", "")
        content = content.get("original_content", "")

        # 如果该index已存在，则把content补充在原content后
        if index in citation_mapping:
            citation_mapping[index]["content"] += content
        else:
            # 否则创建新的entry
            citation_mapping[index] = {
                "title": title,
                "url": url,
                "content": content
            }

    return citation_mapping


def _build_datas_from_chunks(citation_chunks: List, citation_mapping: Dict) -> List[Dict]:
    """从chunks列表中构建溯源datas列表。

    Args:
        citation_chunks (List): chunks列表
        citation_mapping (Dict): 引用映射字典

    Returns:
        List[Dict]: 溯源datas列表，包含每个引用的详细信息
    """
    datas = []

    for chunk_info in citation_chunks:
        citation_num = chunk_info.get("citation_num", 0)

        # 检查该citation是否在mapping中
        if citation_num in citation_mapping:
            mapping = citation_mapping[citation_num]
            title = mapping.get("title", "")
            url = mapping.get("url", "")
            content = mapping.get("content", "")

            # 从chunk_info中获取chunk和位置信息
            chunk = chunk_info.get("chunk", "")
            sentence_position = chunk_info.get("_sentence_position", 0)

            data = {
                "url": url,
                "title": title,
                "content": content,
                "chunk": chunk,
                "_sentence_position": sentence_position,
                "_is_origin_data": True
            }

            datas.append(data)
        else:
            logger.warning(
                f"[_build_datas_from_chunks]未找到引用{citation_num}的映射配置")

    logger.info(f'[_build_datas_from_chunks] 生成的datas列表包含{len(datas)}个引用信息')

    return datas


def generate_origin_report_data(report: str, classified_content: List) -> Dict:
    """从原始报告中提取文中引用的信息。

    Args:
        report (str): 去除了文末参考文献的报告文本
        classified_content (List): 原报告自带的引用信息

    Returns:
        Dict: 包含以下成员字段的字典：
            - "origin_report_data": 原始报告中文内引用的详细信息列表
            - "modified_report": 经过替换后的报告文本，引用格式为 [title](url)
    """

    # 构建引用映射
    citation_mapping = _build_citation_mapping(classified_content)

    # 获取替换后的报告和chunk信息
    replacement_result = _replace_citations_with_custom_mapping(
        report, citation_mapping)

    modified_report = replacement_result["modified_report"]
    citation_chunks = replacement_result["citation_chunks"]

    if not LogManager.is_sensitive():
        logger.info(
            f'[generate_origin_report_data] citation_mapping: {citation_mapping}')
        logger.info(
            f'[generate_origin_report_data] 替换引用信息后的report: {modified_report}')
        logger.info(
            f'[generate_origin_report_data] 提取的citation chunks: {citation_chunks}')

    # 直接使用citation_chunks列表中的数据生成datas列表
    datas = _build_datas_from_chunks(citation_chunks, citation_mapping)

    return {
        "origin_report_data": datas,
        "modified_report": modified_report
    }
