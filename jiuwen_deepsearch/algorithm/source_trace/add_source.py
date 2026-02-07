# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import re
from typing import List, Dict, Any, Tuple

from jiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


class SourceReferenceProcessor:
    """处理报告文本中的引用信息，负责处理溯源结果和相关数据项"""

    def __init__(self, preprocessed_report: str, search_record: Dict[str, Any] | None = None):
        """
        初始化SourceReferenceProcessor实例。

        Args:
            preprocessed_report (str): 预处理后的报告文本，用于处理和分析引用信息
            search_record (Dict[str, Any]): 搜索记录字典，包含各类来源信息
            
        Attributes:
            preprocessed_report (str): 报告文本
            search_record (Dict[str, Any]): 搜索记录
            all_data_items (List[Dict[str, Any]]): 存储所有子报告溯源生成的引用信息列表
        """
        self.preprocessed_report = preprocessed_report
        self.search_record = search_record or {}
        self.all_data_items = []

    def extract_source_info(self, trace_result: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        从溯源结果中提取来源信息
        
        Args:
            trace_result (Dict): 单个溯源结果，包含句子和匹配的源索引
            
        Returns:
            Tuple[str, List[Dict]]: 提取结果元组，第一个元素引用信息数据，
                                     第二个元素是包含引用data的列表
        """
        sentence = trace_result.get("sentence", "")
        matched_source_indices = trace_result.get("matched_source_indices", [])
        source_type = trace_result.get("source", "")

        if not self._validate_trace_result(sentence, matched_source_indices, source_type):
            return "", []

        source_info = ""
        data_items = []

        source_data = self.search_record.get(source_type, [])
        if not source_data:
            return "", []

        for index in matched_source_indices:
            item_info, item_data = extract_source_item_info(
                source_data, index, sentence)
            if item_info:
                source_info += item_info
            if item_data:
                data_items.append(item_data)

        return source_info, data_items

    def _validate_trace_result(self, sentence: str, matched_source_indices: List[int],
                               source_type: str) -> bool:
        """
        验证溯源结果的有效性，检查句子、引用信息索引列表和引用信息来源类型是否合法
        
        Args:
            sentence (str): 需要添加引用信息的句子
            matched_source_indices (List[int]): 匹配的引用信息索引列表
            source_type (str): 引用信息来源类型
            
        Returns:
            bool: 验证结果，True表示有效，False表示无效
        """
        if not sentence or not matched_source_indices or not source_type:
            return False

        if source_type not in self.search_record or not self.search_record[source_type]:
            return False

        sentence = remove_trailing_spaces_and_punctuation(sentence)
        if self.preprocessed_report.find(sentence) == -1:
            return False

        return True


def remove_trailing_spaces_and_punctuation(text: str) -> str:
    """
    去除句子尾部的空格、标点符号和citation标志
    
    Args:
        text (str): 需要处理的文本字符串
        
    Returns:
        str: 去除尾部空格、标点符号和citation标志后的文本
    """
    if not text or not isinstance(text, str):
        return text

    # 使用正则表达式匹配尾部的空格和标点符号并去除
    # 这里包含了中文常见标点和英文标点以及空格
    result = re.sub(r'[\s，。！？；：、,.;:!?]+$', '', text)
    # 移除尾部的citation标志
    citation_pattern = r'\[\s*citation:\s*(\d+)\s*\]'
    result = re.sub(citation_pattern, "", result)
    # 再次去除可能因去除citation而留下的尾部空格和标点
    result = re.sub(r'[\s，。！？；：、,.;:!?]+$', '', result)

    return result


def _remove_md_references_from_chunk(data_item: Dict[str, Any]):
    """
    去除data_item中chunk字段的MD格式引用内容
    
    Args:
        data_item (Dict[str, Any]): 包含chunk字段的数据项字典
    """
    if "chunk" not in data_item:
        return

    chunk = data_item["chunk"]
    if not isinstance(chunk, str):
        return

    # 使用正则表达式匹配并去除MD格式的引用 [标题](链接)
    cleaned_chunk = re.sub(
        r'\s*\[source_tracer_result\]\[.*?\]\(.*?\)', '', chunk)
    cleaned_chunk = re.sub(r'\s*\[.*?\]\(.*?\)', '', cleaned_chunk)

    # 去除可能的多余空格
    cleaned_chunk = cleaned_chunk.strip()

    # 如果清理后内容不为空，则更新chunk字段
    if cleaned_chunk:
        data_item["chunk"] = cleaned_chunk


def add_source_references(
    preprocessed_report: str,
    source_references: List[Dict[str, Any]]
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    为报告中的句子添加来源引用标记。
    
    该函数处理预处理后的报告文本，将来源引用信息插入到对应的句子后面，
    并返回更新后的报告文本和引用信息列表。

    Args:
        preprocessed_report (str): 预处理过的报告文本
        source_references (List[Dict[str, Any]]): 来源引用信息列表，每个元素包含需要添加引用的句子和引用信息

    Returns:
        Tuple[str, List[Dict[str, Any]]]: 包含两个元素的元组：
            - str: 修改后的报告文本（已添加来源引用标记）
            - List[Dict[str, Any]]: 所有更新后的引用信息列表，已按在报告中的位置排序
    """
    # 处理空输入的边界情况
    if not source_references:
        return preprocessed_report, []

    updated_source_references = []
    valid_sentence_groups = {}
    sentence_positions = {}

    # 预处理引用信息并按句子分组
    for ref_info in source_references:
        # 处理原始数据的特殊情况
        if ref_info.get("_is_origin_data", False):
            updated_source_references.append(ref_info)
            continue

        sentence = ref_info.get("chunk", "")
        if not sentence:
            continue
        sentence = remove_trailing_spaces_and_punctuation(sentence)

        # 缓存句子位置，避免重复查找
        if sentence not in sentence_positions:
            sentence_positions[sentence] = preprocessed_report.find(sentence)

        # 跳过在报告中找不到的句子
        if sentence_positions[sentence] == -1:
            if LogManager.is_sensitive():
                logger.warning(
                    f"[add_source_references] 在报告中找不到句子")
            else:
                logger.warning(
                    f"[add_source_references] 在报告中找不到句子: {sentence}")
            continue

        # 存储句子位置信息
        ref_info["_sentence_position"] = sentence_positions[sentence]

        # 按句子分组
        if sentence not in valid_sentence_groups:
            valid_sentence_groups[sentence] = []
        valid_sentence_groups[sentence].append(ref_info)

    modified_report = preprocessed_report

    # 按句子在报告中的位置倒序排序，避免插入位置偏移
    sorted_sentences = sorted(
        valid_sentence_groups.keys(),
        key=lambda x: (sentence_positions.get(x), -len(x)),
        reverse=True
    )

    # 处理每个句子的引用信息
    for sentence in sorted_sentences:
        ref_infos = valid_sentence_groups[sentence]

        # 合并该句子的所有引用信息
        all_source_info = _merge_source_infos(ref_infos)

        if all_source_info:
            # 插入合并后的引用信息到报告中
            insert_result, modified_report = insert_source_info(
                modified_report, sentence, all_source_info)

            # 如果插入成功，将引用信息添加到结果列表
            if insert_result:
                updated_source_references.extend(ref_infos)

    # 基于引用在报告中的位置进行排序
    updated_source_references.sort(
        key=lambda x: (x.get('_sentence_position', 0), len(x)))

    # 清理所有引用中的Markdown格式引用
    for ref_info in updated_source_references:
        _remove_md_references_from_chunk(ref_info)

    return modified_report, updated_source_references


def _escape_html_special_chars(text: str) -> str:
    """
    转义HTML特殊字符，防止XSS攻击
    
    该函数将HTML中的特殊字符转换为对应的HTML实体编码，
    以防止在Web页面中显示时产生安全问题。

    Args:
        text (str): 需要转义的原始文本

    Returns:
        str: 转义后的文本，其中特殊字符已被替换为HTML实体编码
    """
    if not text:
        return ""

    # 定义HTML特殊字符映射
    escape_map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }

    # 转义特殊字符
    result = ""
    for char in text:
        result += escape_map.get(char, char)

    return result


def _merge_source_infos(ref_infos: List[Dict[str, Any]]) -> str:
    """
    合并同一句子的所有引用信息
    该函数接收同一句子的多个引用信息，将它们合并成一个完整的引用信息字符串。
    在合并过程中会对标题和URL进行HTML特殊字符转义处理，并根据标题和URL构建标准格式的引用信息。

    Args:
        ref_infos (List[Dict[str, Any]]): 同一句子的引用信息列表，每个元素包含title和url等信息

    Returns:
        str: 合并后的引用信息字符串，格式为[source_tracer_result][标题](URL)的连续拼接
    """
    all_source_info = ""

    for ref_info in ref_infos:
        origin_title = ref_info.get("title", "")
        title = _escape_html_special_chars(origin_title)
        ref_info["title"] = title
        origin_url = ref_info.get("url", "")
        url = _escape_html_special_chars(origin_url)
        ref_info["url"] = url

        # 根据标题和URL构建引用信息
        if title and url:
            source_info = f"[source_tracer_result][{title}]({url})"
        elif title:
            source_info = f"[source_tracer_result][{title}]({title})"
        else:
            continue

        all_source_info += source_info

    return all_source_info


def generate_source_datas(preprocessed_report: str, search_record: Dict[str, Any],
                          trace_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    根据溯源结果生成来源数据列表。
    该函数处理预处理后的报告文本和搜索记录，结合溯源结果提取相关的来源数据，
    并对其进行排序和清理，最终生成标准化的来源数据列表。

    Args:
        preprocessed_report (str): 预处理过的报告文本
        search_record (Dict[str, Any]): 搜索记录字典，包含各类来源信息
        trace_results (List[Dict[str, Any]]): 溯源结果列表，每条包含句子及匹配的来源索引

    Returns:
        List[Dict[str, Any]]: 收集的来源数据列表
    """
    processor = SourceReferenceProcessor(preprocessed_report, search_record)

    # 处理溯源结果
    for trace_result in trace_results:
        _, data_items = processor.extract_source_info(trace_result)
        if data_items:
            processor.all_data_items.extend(data_items)

    for data_item in processor.all_data_items:
        _remove_md_references_from_chunk(data_item)

    logger.info(f"[generate_source_datas] 共生成 {len(processor.all_data_items)} 条引用信息")
    return processor.all_data_items


def _filter_valid_references(report: str, references: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    过滤和定位有效的引用数据。
    该函数遍历引用信息列表，验证每个引用的有效性并确定其在报告中的位置。
    对于缺少chunk字段或无法在报告中定位的引用将被过滤掉。
    最终返回按位置排序的有效引用列表。

    Args:
        report (str): 报告文本，用于确定chunk的位置
        references (List[Dict[str, Any]]): 引用信息列表

    Returns:
        List[Dict[str, Any]]: 有效的引用信息列表，已按在报告中的位置排序
    """
    valid_datas = []

    for item in references:
        # 检查是否存在chunk字段
        chunk = item.get('chunk')
        if not chunk:
            logger.warning("[_filter_valid_references] 发现缺少chunk字段的引用，已跳过")
            continue

        # 找到chunk在report中的位置
        chunk = remove_trailing_spaces_and_punctuation(chunk)
        position = report.find(chunk)
        if position == -1:
            # 如果是原始数据, 则直接添加，找不到有可能是chunk中间引用格式在chunk中被清理导致
            if item.get("_is_origin_data", False):
                valid_datas.append(item)
                continue
            continue

        # 添加位置信息到item中, 原始数据本身已有位置数据,不用再增加
        if not item.get("_is_origin_data", False):
            item['_sentence_position'] = position
        valid_datas.append(item)

    # 根据位置进行排序
    valid_datas.sort(key=lambda x: (x.get('_sentence_position') or 0, len(x)))

    return valid_datas


def merge_source_datas(
        report: str,
        datas: List[Dict[str, Any]],
        origin_datas: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    合并溯源生成的引用信息列表和原始报告中的引用信息，并根据chunk在report中的位置进行过滤和排序。
    该函数首先合并来自两个来源的引用数据：一个是通过溯源生成的引用信息，
    另一个是原始报告中已有的引用信息。然后使用_filter_valid_references函数
    对合并后的数据进行过滤和排序，确保最终返回的引用列表是有效的且按位置排列。

    Args:
        report (str): 报告文本，用于确定chunk的位置
        datas (List[Dict[str, Any]]): 溯源生成的引用信息列表
        origin_datas (List[Dict[str, Any]]): 原始报告中的引用信息列表

    Returns:
        List[Dict[str, Any]]: 合并并排序后的引用信息列表
    """
    # 合并两个列表
    logger.info(f"[merge_source_datas] 合并处理前,子报告自带{len(origin_datas)}条引用，溯源生成{len(datas)}条引用")
    merged_datas = []
    if datas:
        merged_datas.extend(datas)
    if origin_datas:
        merged_datas.extend(origin_datas)

    # 过滤和排序有效的引用
    valid_datas = _filter_valid_references(report, merged_datas)

    logger.info(f"[merge_source_datas] 合并完成：原始数据 {len(merged_datas)} 条，有效数据 {len(valid_datas)} 条")

    return valid_datas


def insert_source_info(report: str, sentence: str, source_info: str) -> Tuple[bool, str]:
    """
    在报告中的句子后面插入来源信息。
    该函数在给定的报告文本中查找指定句子，并在其后插入来源信息。
    函数会先清理句子中的引用标记，然后尝试在报告中精确定位该句子，
    如果找到则在句子末尾插入来源信息。

    Args:
        report (str): 报告文本
        sentence (str): 需要添加来源信息的句子
        source_info (str): 来源信息

    Returns:
        Tuple[bool, str]: 包含两个元素的元组
            - bool: 是否插入成功
            - str: 更新后的报告文本
    """
    # 检查输入是否有效
    if not sentence or not report:
        return False, report

    # 清理句子中的可能存在的引用标记
    cleaned_sentence = re.sub(
        r'\s*\[source_tracer_result\]\[.*?\]\(.*?\)', '', sentence).strip()

    cleaned_sentence = remove_trailing_spaces_and_punctuation(cleaned_sentence)

    # 直接精确匹配原始句子
    actual_pos = report.find(cleaned_sentence)
    if actual_pos != -1:
        # 找到精确匹配
        end_pos = actual_pos + len(cleaned_sentence)
        return True, report[:end_pos] + source_info + report[end_pos:]

    if not LogManager.is_sensitive():
        logger.warning(f"[insert_source_info] 报告中未找到句子: {cleaned_sentence}")
    return False, report


def extract_source_item_info(
    source_list: List[Dict[str, Any]],
    index: int,
    sentence: str
) -> Tuple[str, Dict[str, Any]]:
    """
    从源数据列表中提取指定索引的项的信息。
    该函数根据给定的索引从源数据列表中提取特定项，并构建包含详细信息的数据字典。
    同时生成Markdown格式的引用链接字符串。函数会验证索引的有效性和源数据项的格式。

    Args:
        source_list (List[Dict[str, Any]]): 源数据列表
        index (int): 要提取的项的索引
        sentence (str): 需要添加引用的句子

    Returns:
        Tuple[str, Dict[str, Any]]: 包含两个元素的元组
            - str: Markdown格式的引用链接字符串
            - Dict[str, Any]: 包含详细信息的数据字典
    """
    # 验证索引范围
    if not 0 <= index < len(source_list):
        logger.warning(f"matched_source_index {index} 超出源数据列表范围")
        return "", {}

    source_item = source_list[index]

    # 验证源数据项格式
    if not isinstance(source_item, dict) or "title" not in source_item:
        return "", {}

    # 清理sentence中的引用内容
    citation_pattern = r'\[citation:\s*(\d+)\]'
    cleaned_chunk = re.sub(citation_pattern, '', sentence).strip()

    # 构建引用数据结构
    data = {
        "name": "",  # 引用名（报告名）
        "url": "",  # 引用链接
        "title": source_item["title"],  # 引用网页标题
        "content": source_item.get("content", ""),  # 引用内容摘要
        "source": "",  # 引用来源
        "publish_time": source_item.get("publish_time", ""),  # 信息发布时间
        "from": "",  # 表明是本地信息或网页信息
        "chunk": cleaned_chunk,  # 报告中需要添加引用的句子
        "score": source_item.get("score", 0.0),  # 引用内容置信度
        "id": "",  # 行内引用唯一标识
    }

    # 构建Markdown格式的引用链接
    if "url" in source_item:
        source_info = f" [{source_item['title']}]({source_item['url']})"
        data["url"] = source_item["url"]
    else:
        source_info = f" [{source_item['title']}]({source_item['title']})"
        data["url"] = source_item["title"]

    # 检查内容字段是否存在
    if not data["content"]:
        return "", {}

    return source_info, data
