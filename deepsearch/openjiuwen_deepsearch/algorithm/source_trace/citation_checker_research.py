# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass

from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import CitationVerifyResearch
from openjiuwen_deepsearch.algorithm.research_collector.target_paper import (
    normalize_arxiv_id,
    normalize_pmid,
)
from openjiuwen_deepsearch.common.exception import CustomIndexException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.common_utils.url_utils import (
    are_similar_urls,
    validate_url_scheme,
)
from openjiuwen_deepsearch.utils.common_utils.text_utils import (
    escape_markdown_link_text,
)

logger = logging.getLogger(__name__)


def _normalize_citation_title(title: str) -> str:
    """Normalize citation title whitespace for single-line markdown links."""
    return " ".join(str(title or "").split())


def _citation_reference_key(url: str) -> str:
    """Return one stable reference key for equivalent academic URLs."""
    pmid = normalize_pmid(url)
    if pmid:
        return f"pubmed:{pmid}"
    arxiv_id = normalize_arxiv_id(url)
    return f"arxiv:{arxiv_id}" if arxiv_id else url


@dataclass(frozen=True)
class SourceTracerCitationMarker:
    """source_tracer_result 引用标记。

    Args:
        raw_start: 标记在原始文本中的起始偏移。
        raw_end: 标记在原始文本中的结束偏移。
        image_marker: 图片标记；图片引用为 ``!``，文本引用为 None。
        title: Markdown 链接标题。
        paren_url: ``(url)`` 格式 URL。
    """

    raw_start: int
    raw_end: int
    image_marker: str | None
    title: str
    paren_url: str
    string: str = ""


class CitationCheckerResearch:
    def __init__(self, llm_model):
        self.citation_verifier = CitationVerifyResearch(llm_model)
        self.invalid_citation_counts = {}
        # 匹配 [source_tracer_result][title](url) 格式的引用；URL 由栈扫描器解析，避免嵌套括号被截断。
        self.citation_regex = re.compile(
            r'(?P<prefix_image>!)?\[source_tracer_result\](?P<suffix_image>!)?\[(?P<title>.*?)\]\(',
            re.VERBOSE | re.DOTALL,
        )

    @staticmethod
    def _iter_source_tracer_citation_markers(
        text: str, prefix_pattern: re.Pattern
    ) -> list[SourceTracerCitationMarker]:
        """解析文本中的 source_tracer_result 引用。

        Args:
            text: 待解析文本。
            prefix_pattern: 匹配到 URL 起始符前缀的正则对象。

        Returns:
            URL 成功闭合的引用标记列表。
        """
        markers = []
        for match in prefix_pattern.finditer(text or ""):
            open_paren_index = match.end() - 1
            parsed_url = extract_markdown_url(text, open_paren_index)
            if parsed_url is None:
                continue
            url, end = parsed_url

            markers.append(
                SourceTracerCitationMarker(
                    raw_start=match.start(),
                    raw_end=end,
                    image_marker="!" if match.group("prefix_image") or match.group("suffix_image") else None,
                    title=match.group("title"),
                    paren_url=url,
                    string=text,
                )
            )
        return markers

    @staticmethod
    def organize_citations_for_frontend(datas):
        """
        保存有效引用信息供前端使用

        Args:
            datas (list): 引用数据列表，每个元素是包含引用信息的字典，需包含valid、is_image等标记字段

        Returns:
            dict: 组织好的前端展示数据，结构如下：
                {
                    'code': 状态码（0表示成功）,
                    'msg': 状态消息,
                    'data': 有效引用信息列表，每个元素包含name、url、title、content、chunk、source、
                           publish_time、score、from、id等字段
                }
        """

        def consolidate_msg(data, idx):
            return {
                "name": "",
                "url": data.get("url", ""),
                "title": data.get("title", ""),
                "content": data.get("content", ""),
                "chunk": data.get("chunk", ""),
                "source": data.get("source", "unknown source"),
                "publish_time": data.get("publish_time", "unknown date"),
                "score": data.get("score", 0),
                "from": "web" if data.get("url", "").startswith("http") else "local",
                "id": data.get("id", idx),
                "reference_index": data.get("reference_index", -1),
            }

        frontend_citations_data = {}
        frontend_citations_data['code'] = 0
        frontend_citations_data['msg'] = "success"
        frontend_citations_data['data'] = []
        idx = 0
        for data in datas:
            # 剔除图片引用, 图片在前端页面不展示浮窗
            if data.get("is_image", False) or not data.get("valid", False):
                continue
            frontend_citations_data['data'].append(consolidate_msg(data, idx))
            idx += 1

        return frontend_citations_data

    @staticmethod
    def rebuild_paragraph_with_valid_citations(para, datas, cur_para_data_index, end_data_index):
        """
        重建段落，只保留有效引用

        Args:
            para (str): 原始段落文本
            datas (list): 引用数据列表，每个元素是包含引用信息的字典
            cur_para_data_index (int): 当前段落的起始引用索引
            end_data_index (int): 当前段落的结束引用索引（不包含）

        Returns:
            str: 重建后的段落文本，只包含有效引用
        """
        # 重建段落，只保留有效引用
        new_parts = []
        last_pos = 0
        for index in range(cur_para_data_index, end_data_index):
            info = datas[index]
            marker = info.get("citation_marker", None)
            if marker is None:
                logger.warning(f"[CITATION CHECKER]: the {index + 1}-th citation in the paragraph has no marker.")
                continue
            # 添加前面的非引用内容
            if marker.raw_start > last_pos:
                new_parts.append(para[last_pos:marker.raw_start])
            if info.get("valid", False):
                temp_str = f'{"!" if info.get("is_image", False) else ""}'
                # 对标题进行Markdown转义
                safe_title = escape_markdown_link_text(info.get("title", ""))
                safe_url = info.get("url", "")
                temp_str += f'[source_tracer_result][{safe_title}]({safe_url})'
                new_parts.append(temp_str)
            last_pos = marker.raw_end
            datas[index]["citation_range"] = (marker.raw_start, marker.raw_end)
            datas[index].pop("citation_marker", None)

        # 添加最后一段非引用内容
        if last_pos < len(para):
            new_parts.append(para[last_pos:])

        return ''.join(new_parts)

    @staticmethod
    def format_text_citation(url, title, references, ref_counter, citation_id):
        """格式化文本型引用并分配稳定 citation id。

        当正文中的多个引用实例指向同一个 URL 时，参考文献序号需要复用；
        但前端用于渲染和交互的 ``checked_citation`` 实例 id 仍需保持逐次递增，
        这样每个正文中的引用锚点都能拥有稳定且唯一的实例标识。

        Args:
            url: 引用的 URL 地址。
            title: 引用标题。
            references: 已处理的引用集合，键为 URL，值为 ``(title, ref_index)``。
            ref_counter: 当前引用序号计数器。
            citation_id: 当前文本引用实例对应的稳定 id。

        Returns:
            tuple[str, int, int]: 格式化后的引用文本、更新后的引用计数器和引用序号。
        """
        # 如果这个引用已经存在，使用已有的序号
        reference_key = _citation_reference_key(url)
        if reference_key in references:
            safe_title, idx, *_ = references[reference_key]
            return f"[checked_citation:{citation_id}][[{idx}]]({url})", ref_counter, idx

        # 对标题进行Markdown转义，防止内容注入
        safe_title = escape_markdown_link_text(title)
        # 否则添加新引用并递增计数器
        if reference_key == url:
            references[reference_key] = (safe_title, ref_counter)
        else:
            references[reference_key] = (safe_title, ref_counter, url)
        current_idx = ref_counter
        ref_counter += 1
        return f"[checked_citation:{citation_id}][[{current_idx}]]({url})", ref_counter, current_idx

    @staticmethod
    def build_reference_section(references):
        """
        构建参考文献部分章节内容

        Args:
            references (dict): 已处理的引用集合，键为URL，值为包含标题和序号的元组

        Returns:
            str: 构建好的参考文献部分内容，每个引用格式为 `[序号]. [标题](URL)`
        """
        reference_section = ""
        for (reference_key, item) in references.items():
            url = item[2] if len(item) > 2 else reference_key
            # item[0] 是已经转义过的标题，item[1] 是序号
            reference_section += f'[{item[1]}]. [{item[0]}]({url})\n\n'

        return reference_section

    def validate_url_match(self, url, datas, citation_index):
        """
        验证引用URL是否与数据源中的URL匹配，同时验证URL scheme安全性

        Args:
            url (str): 待验证的URL字符串
            datas (list): 数据源列表，每个元素是包含引用信息的字典
            citation_index (int): 当前验证的引用在数据源中的索引

        Returns:
            tuple: (验证后的URL, 是否匹配)
                - 验证后的URL (str): 如果URL被纠正，则返回纠正后的URL，否则返回原始URL
                - 是否匹配 (bool): True表示URL匹配或可纠正且scheme安全，False表示URL不匹配或不安全
        """
        if not datas[citation_index].get('valid', False):
            return url, False

        # 首先验证URL scheme安全性
        datas_url = datas[citation_index].get('url', '')
        safe_url, is_safe_scheme = validate_url_scheme(datas_url)
        if not is_safe_scheme:
            # URL scheme不安全，标记为无效引用
            datas[citation_index]['valid'] = False
            invalid_reason = "unsafe url scheme"
            datas[citation_index]["invalid_reason"] = invalid_reason
            self.invalid_citation_counts[invalid_reason] = self.invalid_citation_counts.get(invalid_reason, 0) + 1
            logger.warning(
                f"[CITATION CHECKER]: delete unsafe url scheme citation: "
                f"The {citation_index}-th url has unsafe scheme"
            )
            return "", False

        if datas_url != url:
            if are_similar_urls(url, datas_url):
                url = datas_url
                return url, True

            # 不匹配的url作为错误url直接删除
            datas[citation_index]['valid'] = False
            invalid_reason = "mismatch url"
            datas[citation_index]["invalid_reason"] = invalid_reason
            self.invalid_citation_counts[invalid_reason] = self.invalid_citation_counts.get(invalid_reason, 0) + 1

            warning_log = "[CITATION CHECKER]: delete mismatch url: "
            warning_log += f"The {citation_index}-th url of datas is mismatch: "
            if not LogManager.is_sensitive():
                warning_log += f"expect '{datas_url}', fact '{url}'"
            logger.warning(warning_log)
            return url, False

        return url, True

    def _text_between_markers_only_contains_source_citations(
        self,
        old_marker: SourceTracerCitationMarker,
        new_marker: SourceTracerCitationMarker,
    ):
        """
        判断两个引用之间的文本是否仅由其他 `[source_tracer_result]` 引用及空白构成，从而补足非严格连续的相邻判定。

        Args:
            old_marker: 之前出现的引用标记。
            new_marker: 当前正在处理的引用标记。

        Returns:
            bool: 如果两个标记位于同一字符串中，且之间只包含 `source_tracer_result` 引用（或空白），返回 True；
                  否则返回 False。
        """
        old_string = old_marker.string
        new_string = new_marker.string
        if not old_string or old_string is not new_string:
            return False
        between_text = old_string[old_marker.raw_end:new_marker.raw_start]

        pointer = 0
        found = False
        text_len = len(between_text)
        for marker in self._iter_source_tracer_citation_markers(between_text, self.citation_regex):
            if marker.raw_start > pointer and between_text[pointer:marker.raw_start].strip():
                return False
            found = True
            pointer = marker.raw_end

        if pointer < text_len and between_text[pointer:].strip():
            return False

        return found

    def remove_duplicate_citations(self, url, datas, processed_citation_urls, citation_index):
        """
        处理重复引用，根据位置关系决定去重策略：
        - 相邻重复引用：保留score更高的引用
        - 不相邻重复引用：更新seen_urls记录，保留最新引用

        Args:
            url (str): 引用的URL地址
            datas (list): 数据源列表，每个元素是包含引用信息的字典
            processed_citation_urls (dict): 已处理的URL集合，键为URL，值为包含score和data_index的字典
            citation_index (int): 当前引用在数据源中的索引

        Returns:
            list: 待删除引用索引列表
        """
        del_indices = []
        current_data = datas[citation_index]
        if url in processed_citation_urls:
            # 获取新旧引用标记
            old_data_index = processed_citation_urls[url]['data_index']
            old_marker = datas[old_data_index].get('citation_marker')
            new_marker = current_data.get('citation_marker')

            # 检查引用标记是否存在
            if old_marker is not None and new_marker is not None:
                # 判断新data的起点是否接近旧data的终点（相差在2以内都认为是相邻，避免引用之间存在空格）。
                is_adjacent = abs(new_marker.raw_start - old_marker.raw_end) <= 2
                if not is_adjacent:
                    # 判断两个相同引用之间是否只是其他的引用
                    is_adjacent = self._text_between_markers_only_contains_source_citations(
                        old_marker, new_marker)

                if is_adjacent:
                    # 相邻则保留score更高的引用
                    invalid_reason = "score lower than another citation"
                    if current_data.get('score', 0) > processed_citation_urls[url]['score']:
                        # 保留当前引用，删除之前记录的
                        del_indices.append(processed_citation_urls[url]['data_index'])
                        datas[processed_citation_urls[url]['data_index']]["valid"] = False
                        datas[processed_citation_urls[url]['data_index']
                              ]["invalid_reason"] = invalid_reason
                        self.invalid_citation_counts[invalid_reason] = self.invalid_citation_counts.get(
                            invalid_reason, 0) + 1
                        processed_citation_urls[url] = {
                            'score': current_data.get('score', 0),
                            'data_index': citation_index,  # 对应datas的索引
                        }
                    else:
                        # 保留之前的引用，删除当前
                        datas[citation_index]["valid"] = False
                        datas[citation_index]["invalid_reason"] = invalid_reason
                        self.invalid_citation_counts[invalid_reason] = self.invalid_citation_counts.get(
                            invalid_reason, 0) + 1
                        del_indices.append(citation_index)
                else:
                    # 不相邻则更新seen_urls[url]的data_index和score，不再删除
                    processed_citation_urls[url] = {
                        'score': current_data.get('score', 0),
                        'data_index': citation_index,
                    }
        else:
            processed_citation_urls[url] = {
                'score': current_data.get('score', 0),
                'data_index': citation_index,
            }

        return del_indices

    def validate_and_process_single_citation(
        self,
        marker: SourceTracerCitationMarker,
        datas,
        processed_citation_urls,
        data_index,
    ):
        """
        处理单个引用，包括提取URL、验证有效性、检查匹配度和处理重复引用

        Args:
            marker: 解析出的 source_tracer_result 引用标记。
            datas (list): 引用数据列表，每个元素是包含引用信息的字典
            processed_citation_urls (OrderedDict): 已处理的URL集合，用于检测和处理重复引用
            data_index (int): 当前引用在datas列表中的索引位置

        Returns:
            del_indices (list): 需要删除的无效引用索引列表
        """
        # 提取url
        is_image = marker.image_marker is not None
        url = marker.paren_url
        url = url.strip()

        # 检查data_index是否有效
        if data_index >= len(datas):
            raise CustomIndexException(StatusCode.PARAM_CHECK_ERROR_INDEX_OUT_OF_RANGE.code,
                                       StatusCode.PARAM_CHECK_ERROR_INDEX_OUT_OF_RANGE.errmsg.
                                       format(content_idx=data_index))

        current_data = datas[data_index]
        if not LogManager.is_sensitive():
            logger_msg = f"[CITATION CHECKER]: the {data_index}-th check: "
            logger_msg += f"text citation url: {url}, expected url: {datas[data_index]['url']}"
            logger.info(logger_msg)

        datas[data_index]['is_image'] = is_image
        datas[data_index]['citation_marker'] = marker

        # 如果当前引用无效，直接跳过
        if not current_data['valid']:
            return [data_index]

        # 检查url是否匹配
        url, is_valid = self.validate_url_match(url, datas, data_index)
        if not is_valid:
            return [data_index]

        # 处理有效重复引用
        del_indices = self.remove_duplicate_citations(
            url, datas, processed_citation_urls, data_index)

        return del_indices

    def process_single_paragraph_citations(self, para, datas, processing_data_index):
        """
        处理单个段落中的所有引用，包括验证、处理和重建段落

        Args:
            para (str): 原始段落文本
            datas (list): 引用数据列表，每个元素是包含引用信息的字典
            processing_data_index (int): 当前引用在datas列表中的索引位置

        Returns:
            tuple: 返回处理后的结果
                - processed_para (str): 处理后的段落文本，只包含有效引用
                - processing_data_index (int): 更新后的当前引用索引
                - del_indices (list): 更新后的需要删除的无效引用索引列表
        """
        # 查找所有引用
        markers = self._iter_source_tracer_citation_markers(para, self.citation_regex)
        if not markers:
            return para, processing_data_index, []

        # 处理引用
        processed_citation_urls = OrderedDict()  # 记录已处理的url及其最佳引用
        cur_para_data_index = processing_data_index  # 记录当前段落开始时的data_index

        del_indices = []
        for marker in markers:
            # 处理单个引用
            single_match_del_indices = self.validate_and_process_single_citation(
                marker, datas, processed_citation_urls, processing_data_index)
            del_indices.extend(single_match_del_indices)
            processing_data_index += 1

        # 验证引用数量匹配
        if len(markers) != processing_data_index - cur_para_data_index:
            error_msg = "[CITATION CHECKER]: the length of matches is error."
            error_msg += "Not equal to count of citation in the para: \n"
            if not LogManager.is_sensitive():
                error_msg += f"markers: {markers} \n para: {para}"
            raise CustomValueException(StatusCode.CITATION_CHECKER_DATA_LEN_ERROR.code,
                                       StatusCode.CITATION_CHECKER_DATA_LEN_ERROR.errmsg.
                                       format(e=error_msg))

        # 重建段落
        processed_para = self.rebuild_paragraph_with_valid_citations(
            para, datas, cur_para_data_index, processing_data_index)

        return processed_para, processing_data_index, del_indices

    def process_all_paragraphs_citations(self, paragraphs, datas):
        """
        处理文本分割后的所有段落中的引用，包括验证、处理和清理无效引用

        Args:
            paragraphs (list): 段落列表，每个元素是一个段落文本
            datas (list): 引用数据列表，每个元素是包含引用信息的字典

        Returns:
            tuple: 返回处理后的结果
                - processed_paragraphs (str): 处理后的完整文本，各段落用换行符连接
                - cleaned_datas (list): 清理后的有效引用数据列表，移除了无效引用
        """
        processed_paragraphs = []
        processing_data_index = 0  # 跟踪datas的当前索引
        all_del_indices = []  # 记录要删除的datas索引

        for para in paragraphs:
            # 处理单个段落
            processed_para, processing_data_index, del_indices = self.process_single_paragraph_citations(
                para, datas, processing_data_index)
            all_del_indices.extend(del_indices)
            processed_paragraphs.append(processed_para)

        # 清理datas
        cleaned_datas = [data for i, data in enumerate(datas) if i not in all_del_indices]
        processed_paragraphs = '\n'.join(processed_paragraphs)

        return processed_paragraphs, cleaned_datas

    def deduplicate_citations(self, text, datas):
        """
        去除连续重复的行内引用，保留score更高的引用

        Args:
            text (str): 包含引用标记的输入文本
            datas (list): 引用数据列表，每个元素是包含引用信息的字典

        Returns:
            tuple: 返回处理后的结果
                - processed_paragraphs (str): 处理后的文本，去除了重复引用
                - cleaned_datas (list): 处理后的数据列表，只保留了有效引用
        """

        # 分割文本为段落
        paragraphs = text.split('\n')

        # 处理所有段落
        processed_paragraphs, cleaned_datas = self.process_all_paragraphs_citations(
            paragraphs, datas)

        return processed_paragraphs, cleaned_datas

    def preprocess_text_and_citations(self, text, datas):
        """
        预处理文本和引用数据，去除连续重复和无效引用，仅保留score最高的引用

        Args:
            text (dict): 包含文章内容的字典，必须包含'article'键
            datas (list): 引用数据列表，每个元素是包含引用信息的字典，必须包含'score'字段

        Returns:
            tuple: 返回预处理后的结果
                - markdown_text (str): 处理后的文章文本，去除了重复引用
                - datas (list): 处理后的引用数据列表，只保留了有效引用
        """
        markdown_text = text.get('article', "")
        markdown_text = self.normalize_source_tracer_titles(markdown_text)
        self.normalize_datas_titles(datas)
        markdown_text, datas = self.deduplicate_citations(markdown_text, datas)
        if LogManager.is_sensitive():
            logger.info(f"[CITATION CHECKER]: preprocess text and datas success.")
        else:
            logger.info(f"[CITATION CHECKER]: preprocess text and datas success. {markdown_text}")

        return markdown_text, datas

    def normalize_source_tracer_titles(self, markdown_text):
        """Normalize titles in existing source tracer placeholders before paragraph splitting."""

        def replace_title(marker):
            image_marker = "!" if marker.image_marker else ""
            title = _normalize_citation_title(marker.title)
            return f"[source_tracer_result]{image_marker}[{title}]({marker.paren_url})"

        parts = []
        cursor = 0
        for marker in self._iter_source_tracer_citation_markers(markdown_text, self.citation_regex):
            parts.append(markdown_text[cursor:marker.raw_start])
            parts.append(replace_title(marker))
            cursor = marker.raw_end
        parts.append(markdown_text[cursor:])
        return "".join(parts)

    @staticmethod
    def normalize_datas_titles(datas):
        """Normalize citation titles in datas before any placeholder rebuilding."""
        for data in datas:
            if isinstance(data, dict) and "title" in data:
                data["title"] = _normalize_citation_title(data.get("title", ""))

    def replace_inline_citations(self, markdown_text, datas):
        """将行内溯源标记替换为带稳定 id 的 checked citation。

        该步骤会把 ``[source_tracer_result]`` 形式的内联引用转换为前端可识别的
        ``[checked_citation:<id>][[n]](url)`` 结构，并同步回填 ``datas`` 中的
        稳定实例 id 与参考文献序号。图片引用沿用原有图片渲染格式，不进入参考
        文献编号体系。

        Args:
            markdown_text: 包含行内引用的 Markdown 文本。
            datas: 引用数据列表，每项都是引用信息字典。

        Returns:
            tuple[str, OrderedDict, list]: 转换后的正文、参考文献映射和更新后的引用数据。
        """
        references = OrderedDict()
        ref_counter = 1
        cur_citation_index = -1
        next_citation_id = 0

        new_parts = []
        last_pos = 0

        for marker in self._iter_source_tracer_citation_markers(markdown_text, self.citation_regex):
            is_image = marker.image_marker is not None
            title = marker.title
            url = marker.paren_url

            url = url.strip()
            cur_citation_index += 1

            # 追加匹配之前的非引用文本
            before_text = markdown_text[last_pos:marker.raw_start]
            new_parts.append(before_text)

            url, is_valid = self.validate_url_match(url, datas, cur_citation_index)
            if not is_valid:
                # 无效引用，替换为空字符串
                last_pos = marker.raw_end
                continue

            if is_image:
                # 对图片标题进行Markdown转义
                safe_title = escape_markdown_link_text(title)
                replacement = f'![[{safe_title}]]({url})'
                new_parts.append(replacement)
            else:
                text_citation, ref_counter, current_idx = self.format_text_citation(
                    url, title, references, ref_counter, citation_id=next_citation_id)
                datas[cur_citation_index]["reference_index"] = current_idx
                new_parts.append(text_citation)
                datas[cur_citation_index]["id"] = next_citation_id
                next_citation_id += 1

            last_pos = marker.raw_end

        # 追加剩余文本
        new_parts.append(markdown_text[last_pos:])
        transformed_text = ''.join(new_parts)

        # 检查引用数量是否匹配
        if cur_citation_index + 1 != len(datas):
            warning_log = "[CITATION CHECKER]: the count of datas and citations in report are mismatched, "
            warning_log += f"length of datas: {len(datas)}, "
            warning_log += f"but count of citations in the text: {cur_citation_index + 1}. "
            logger.warning(warning_log)
            datas = datas[:cur_citation_index + 1]

        return transformed_text, references, datas

    def transform_references(self, text, datas):
        """
        转换文本中的引用格式，生成完整的参考文献章节

        Args:
            text (dict): 包含文章内容的字典，必须包含'article'键
            datas (list): 引用数据列表，每个元素是包含引用信息的字典

        Returns:
            tuple: 返回转换后的结果
                - result_text (str): 转换后的完整文本，包含标准格式的行内引用和参考文献章节
                - datas (list): 处理后的引用数据列表
        """
        # 预处理文本和引用数据
        markdown_text, datas = self.preprocess_text_and_citations(text, datas)

        # 执行引用替换
        transformed_text, references, datas = self.replace_inline_citations(markdown_text, datas)
        logger.info('[CITATION CHECKER]: replace inline citations success.')

        # 构建参考文献章节
        reference_section = self.build_reference_section(references)
        logger.info('[CITATION CHECKER]: build reference section success.')

        # 整合最终结果，更新参考文献章节
        result_text = transformed_text + '\n\n' + reference_section
        if not LogManager.is_sensitive():
            logger.info(
                f"=============== result text =================:\n{result_text}",
                extra={"skip_truncation": True},
            )

        return result_text, datas

    def count_verify_failed_citations(self, datas):
        """
        统计验证失败的引用原因分布

        Args:
            datas (list): 引用数据列表，每个元素是包含引用信息的字典

        Returns:
            None
        """
        for data in datas:
            if data.get('valid', False) is False:
                reason = data.get("invalid_reason", "unknown reason")
                self.invalid_citation_counts[reason] = self.invalid_citation_counts.get(reason, 0) + 1

    def log_invalid_citation_reasons(self):
        """
        统计并打印无效引用原因的数量分布

        该函数会遍历实例变量invalid_count_dict，按数量降序排序并输出到日志
        """
        # 按数量降序排序并输出到日志
        if self.invalid_citation_counts:
            logmsg = "[CITATION CHECKER] 无效引用原因统计:\n"
            for reason, count in sorted(self.invalid_citation_counts.items(), key=lambda x: x[1], reverse=True):
                logmsg += f"{reason}: {count}\n"
            logger.info(logmsg)
        else:
            logger.info("[CITATION CHECKER] 无无效引用")

    async def checker(self, text, datas) -> str:
        """
        对文本中的引用进行验证和处理，包括筛选无效引用、转换引用格式和生成参考文献章节

        Args:
            text (dict): 包含文章内容的字典，必须包含'article'键
            datas (list): 引用数据列表，每个元素是包含引用信息的字典

        Returns:
            str: 包含处理后的文本内容和引用信息的JSON字符串
                - checked_trace_source_report_content: 处理后的完整文本，包含标准格式的行内引用和参考文献章节
                - citation_messages: 有效的引用信息列表
        """
        # 筛选无效、不正确的引用
        datas = await self.citation_verifier.run(datas)
        logger.info("[CITATION CHECKER]: CitationVerify finished ==============")
        if not LogManager.is_sensitive():
            logger.info(
                f"validated citation citation message: {json.dumps(datas, ensure_ascii=False, indent=4)}")
        self.count_verify_failed_citations(datas)
        # 过滤报告中的错误行内引用，并生成参考文献章节
        logger.info("[CITATION CHECKER]: start transform references.")
        text, datas = self.transform_references(text, datas)
        logger.info(f"[CITATION CHECKER]: 最终有效引用数量: {len(datas)}.")
        self.log_invalid_citation_reasons()
        # organize the valid citation message for front end
        citation_messages = self.organize_citations_for_frontend(datas)
        citation_checker_result = {"checked_trace_source_report_content": text, "citation_messages": citation_messages}
        citation_checker_result_str = json.dumps(citation_checker_result, ensure_ascii=False)
        logger.info("[CITATION CHECKER]: check finished")
        return citation_checker_result_str
