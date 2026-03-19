# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import re
from collections import OrderedDict

from openjiuwen_deepsearch.algorithm.source_trace.citation_verify_research import CitationVerifyResearch
from openjiuwen_deepsearch.common.exception import CustomIndexException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.common_utils.url_utils import are_similar_urls

logger = logging.getLogger(__name__)


class CitationCheckerResearch:
    def __init__(self, llm_model):
        self.citation_verifier = CitationVerifyResearch(llm_model)
        self.invalid_citation_counts = {}
        # 匹配 [source_tracer_result][title](url) 或 [source_tracer_result][title]<url> 格式的引用
        # 支持title中包含嵌套的[]或()，url中包含嵌套的()、<>或[]
        self.citation_regex = re.compile(r'\[source_tracer_result\](!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))', re.VERBOSE)

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
                "id": idx,
                "reference_index": data.get("reference_index", -1),
                "citation_start_offset": data.get("citation_start_offset", -1),
                "citation_end_offset": data.get("citation_end_offset", -1),
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
            match = info.get("match", None)
            if match is None:
                logger.warning(f"[CITATION CHECKER]: the {index + 1}-th citation in the paragraph has no match.")
                continue
            # 添加前面的非引用内容
            if match.start() > last_pos:
                new_parts.append(para[last_pos:match.start()])
            if info.get("valid", False):
                temp_str = f'{"!" if info.get("is_image", False) else ""}'
                temp_str += f'[source_tracer_result][{info["title"]}]({info["url"]})'
                new_parts.append(temp_str)
            last_pos = match.end()
            datas[index]["match"] = (match.start(), match.end())

        # 添加最后一段非引用内容
        if last_pos < len(para):
            new_parts.append(para[last_pos:])

        return ''.join(new_parts)

    @staticmethod
    def format_text_citation(url, title, references, ref_counter):
        """
        处理文本类型的引用，识别并提取引用信息

        Args:
            url (str): 引用的URL地址
            title (str): 引用的标题
            references (dict): 已处理的引用集合，键为URL，值为包含标题和序号的元组
            ref_counter (int): 当前引用计数器，用于生成新的引用序号

        Returns:
            tuple: 返回处理后的引用文本和更新后的引用计数器
                - 处理后的引用文本 (str): 格式化的引用字符串，如 `[[1]](https://example.com)`
                - 更新后的引用计数器 (int): 递增后的引用计数器值
        """
        # 如果这个引用已经存在，使用已有的序号
        if url in references:
            title, idx = references[url]
            return f"[[{idx}]]({url})", ref_counter, idx

        # 否则添加新引用并递增计数器
        references[url] = (title, ref_counter)
        current_idx = ref_counter
        ref_counter += 1
        return f"[[{current_idx}]]({url})", ref_counter, current_idx

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
        for (url, item) in references.items():
            reference_section += f'[{item[1]}]. [{item[0]}]({url})\n\n'

        return reference_section

    def validate_url_match(self, url, datas, citation_index):
        """
        验证引用URL是否与数据源中的URL匹配

        Args:
            url (str): 待验证的URL字符串
            datas (list): 数据源列表，每个元素是包含引用信息的字典
            citation_index (int): 当前验证的引用在数据源中的索引

        Returns:
            tuple: (验证后的URL, 是否匹配)
                - 验证后的URL (str): 如果URL被纠正，则返回纠正后的URL，否则返回原始URL
                - 是否匹配 (bool): True表示URL匹配或可纠正，False表示URL不匹配
        """
        if not datas[citation_index].get('valid', False):
            return url, False

        datas_url = datas[citation_index].get('url', '')
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

    def _text_between_matches_only_contains_source_citations(self, old_match, new_match):
        """
        判断两个引用之间的文本是否仅由其他 `[source_tracer_result]` 引用及空白构成，从而补足非严格连续的相邻判定。

        Args:
            old_match (re.Match): 之前出现的引用匹配结果
            new_match (re.Match): 当前正在处理的引用匹配结果

        Returns:
            bool: 如果两个 match 位于同一字符串中，且之间只包含 `source_tracer_result` 引用（或空白），返回 True；
                  否则返回 False。
        """
        old_string = getattr(old_match, "string", None)
        new_string = getattr(new_match, "string", None)
        if not old_string or old_string is not new_string:
            return False
        between_text = old_string[old_match.end():new_match.start()]

        pointer = 0
        found = False
        text_len = len(between_text)
        while pointer < text_len:
            match = self.citation_regex.search(between_text, pointer)
            if not match:
                break
            if match.start() > pointer and between_text[pointer:match.start()].strip():
                return False
            found = True
            pointer = match.end()

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
            # 获取新旧引用的match对象
            old_data_index = processed_citation_urls[url]['data_index']
            old_match = datas[old_data_index].get('match')
            new_match = current_data.get('match')

            # 检查match是否存在
            if old_match is not None and new_match is not None:
                # 判断新data的start是否是旧data的end（即是否相邻，相差在2以内都认为是相邻,避免引用之间存在空格）
                is_adjacent = abs(new_match.start() - old_match.end()) <= 2
                if not is_adjacent:
                    # 判断两个相同引用之间是否只是其他的引用
                    is_adjacent = self._text_between_matches_only_contains_source_citations(
                        old_match, new_match)

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

    def validate_and_process_single_citation(self, match, datas, processed_citation_urls, data_index):
        """
        处理单个引用，包括提取URL、验证有效性、检查匹配度和处理重复引用

        Args:
            match (re.Match): 正则表达式匹配到的引用对象
            datas (list): 引用数据列表，每个元素是包含引用信息的字典
            processed_citation_urls (OrderedDict): 已处理的URL集合，用于检测和处理重复引用
            data_index (int): 当前引用在datas列表中的索引位置

        Returns:
            del_indices (list): 需要删除的无效引用索引列表
        """
        # 提取url
        is_image = match.group(1) is not None
        url = match.group(3) or match.group(4)  # group(3)是()格式，group(4)是<>格式
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
        datas[data_index]['match'] = match

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
        matches = list(self.citation_regex.finditer(para))
        if not matches:
            return para, processing_data_index, []

        # 处理引用
        processed_citation_urls = OrderedDict()  # 记录已处理的url及其最佳引用
        cur_para_data_index = processing_data_index  # 记录当前段落开始时的data_index

        del_indices = []
        for match in matches:
            # 处理单个引用
            single_match_del_indices = self.validate_and_process_single_citation(
                match, datas, processed_citation_urls, processing_data_index)
            del_indices.extend(single_match_del_indices)
            processing_data_index += 1

        # 验证引用数量匹配
        if len(matches) != processing_data_index - cur_para_data_index:
            error_msg = "[CITATION CHECKER]: the length of matches is error."
            error_msg += "Not equal to count of citation in the para: \n"
            if not LogManager.is_sensitive():
                error_msg += f"matches: {matches} \n para: {para}"
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
        markdown_text, datas = self.deduplicate_citations(markdown_text, datas)
        if LogManager.is_sensitive():
            logger.info(f"[CITATION CHECKER]: preprocess text and datas success.")
        else:
            logger.info(f"[CITATION CHECKER]: preprocess text and datas success. {markdown_text}")

        return markdown_text, datas

    def replace_inline_citations(self, markdown_text, datas, inline_ref_pattern):
        """
        替换行内引用格式，将[source_tracer_result][title](url)或[source_tracer_result][title]<url>格式的引用转换为标准Markdown引用格式

        Args:
            markdown_text (str): 包含行内引用的Markdown文本
            datas (list): 引用数据列表，每个元素是包含引用信息的字典
            inline_ref_pattern (re.Pattern): 用于匹配行内引用的正则表达式对象

        Returns:
            tuple: 返回处理后的结果
                - transformed_text (str): 处理后的Markdown文本，包含标准格式的引用
                - references (OrderedDict): 按出现顺序排序的参考文献字典，键为URL，值为引用信息
                - datas (list): 更新后的引用数据列表，有效文本引用会新增 citation_start_offset / citation_end_offset 字段
        """
        references = OrderedDict()
        ref_counter = 1
        cur_citation_index = -1

        new_parts = []
        last_pos = 0
        current_new_offset = 0

        for match in inline_ref_pattern.finditer(markdown_text):
            is_image = match.group(1) is not None
            title = match.group(2)
            url = match.group(3) or match.group(4)

            if url is None:
                # 无法识别 URL，原样保留该匹配段
                before_text = markdown_text[last_pos:match.end()]
                new_parts.append(before_text)
                current_new_offset += len(before_text)
                last_pos = match.end()
                continue

            url = url.strip()
            cur_citation_index += 1

            # 追加匹配之前的非引用文本
            before_text = markdown_text[last_pos:match.start()]
            new_parts.append(before_text)
            current_new_offset += len(before_text)

            url, is_valid = self.validate_url_match(url, datas, cur_citation_index)
            if not is_valid:
                # 无效引用，替换为空字符串
                last_pos = match.end()
                continue

            if is_image:
                replacement = f'![[{title}]]({url})'
                new_parts.append(replacement)
                current_new_offset += len(replacement)
            else:
                text_citation, ref_counter, current_idx = self.format_text_citation(
                    url, title, references, ref_counter)
                datas[cur_citation_index]["reference_index"] = current_idx

                # 这里记录的是“替换完成后的新文本坐标”，
                # 后续用户做局部改写时会基于这个 offset 精确删除/平移引用实例。
                citation_start = current_new_offset
                new_parts.append(text_citation)
                current_new_offset += len(text_citation)
                citation_end = current_new_offset

                datas[cur_citation_index]["citation_start_offset"] = citation_start
                datas[cur_citation_index]["citation_end_offset"] = citation_end

            last_pos = match.end()

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

        # 匹配行内引用 [title]<url> 的正则表达式, 两种匹配模式防遗漏：[source_tracer_result][title]<url>, [source_tracer_result][title](url)
        inline_ref_pattern = re.compile(
            r'\[source_tracer_result\](!)?\[(.*?)\](?:<(.*?)>|\((.*?)\))')

        # 执行引用替换
        transformed_text, references, datas = self.replace_inline_citations(
            markdown_text, datas, inline_ref_pattern)
        logger.info('[CITATION CHECKER]: replace inline citations success.')

        # 构建参考文献章节
        reference_section = self.build_reference_section(references)
        logger.info('[CITATION CHECKER]: build reference section success.')

        # 整合最终结果，更新参考文献章节
        result_text = transformed_text + '\n\n' + reference_section
        if not LogManager.is_sensitive():
            logger.info(f"=============== result text =================:\n{result_text}")

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
