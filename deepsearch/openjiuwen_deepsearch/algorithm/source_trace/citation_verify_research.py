# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
import logging
from typing import Callable, Optional
from dataclasses import dataclass

import re
import difflib
from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import lookup_source, save_mapping
from openjiuwen_deepsearch.algorithm.source_trace.source_match_algo import classify_and_match
from openjiuwen_deepsearch.utils.common_utils.url_utils import extract_domain_from_url
from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import _extract_registered_domain

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import llm_context
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)
MAX_LLM_RETRY_TIMES = 3


@dataclass
class BatchContext:
    """批次处理上下文，封装批次处理所需的状态和功能函数"""
    batch_state: dict
    process_func: Callable
    error_result_func: Callable
    semaphore: asyncio.Semaphore
    log_prefix: str = ""


class CitationVerifyResearch:
    def __init__(self, llm_model):
        self.datas = []
        self.concurrent_limit = Config().service_config.source_tracer_citation_verify_max_concurrency_num
        self.verify_batch_size = Config().service_config.source_tracer_citation_verify_batch_size
        self.llm_model = llm_model

    @staticmethod
    def find_matches(text: str, fragments: list, threshold: int) -> list:
        """在文本中查找片段的最佳匹配位置

        先使用精确匹配，如果不匹配再进行模糊匹配，返回所有匹配位置的起止序列

        Args:
            text (str): 原始文本，需要在其中查找匹配的片段
            fragments (list): 要查找的文本片段列表
            threshold (int): 模糊匹配的相似度阈值，超过该阈值的匹配才会被返回

        Returns:
            list: 匹配位置的起止序列列表，每个元素是一个元组(start_pos, end_pos)
        """
        tagged_positions = []

        for fragment in fragments:
            # 首先尝试精确匹配
            exact_match_pos = text.find(fragment)
            if exact_match_pos != -1:
                # 找到精确匹配，直接记录位置
                start_pos = exact_match_pos
                end_pos = exact_match_pos + len(fragment)
                tagged_positions.append((start_pos, end_pos))
                continue

            # 如果没有精确匹配，进行模糊匹配
            best_match_ratio = 0
            best_match_pos = None
            best_match_length = 0

            # 滑动窗口查找最佳匹配位置
            fragment_len = len(fragment)
            for i in range(len(text) - fragment_len + 1):
                text_segment = text[i:i + fragment_len]
                # 计算相似度
                similarity = difflib.SequenceMatcher(
                    None, fragment, text_segment).ratio() * 100

                if similarity > best_match_ratio:
                    best_match_ratio = similarity
                    best_match_pos = i
                    best_match_length = len(text_segment)

            # 如果最佳匹配超过阈值，记录位置
            if best_match_ratio >= threshold and best_match_pos is not None:
                start_pos = best_match_pos
                end_pos = best_match_pos + best_match_length
                tagged_positions.append((start_pos, end_pos))

        return tagged_positions

    @staticmethod
    def reorder_batch_results(batches: list, results: list, batch_size: int, data_len: int) -> list:
        """重新排序批次处理结果

        将分批处理的结果按照原始数据顺序重新排列，确保结果顺序与输入数据顺序一致

        Args:
            batches (list): 批次数据列表，每个元素是一个元组(batch_idx, batch_data)
            results (list): 批次处理结果列表，与batches对应
            batch_size (int): 每个批次的大小
            data_len (int): 原始数据的总长度

        Returns:
            list: 按原始顺序重新排列后的结果列表
        """
        reordered_results = [None] * data_len
        for (batch_idx, _), batch_result in zip(batches, results):
            if batch_result:
                start = batch_idx * batch_size
                end = min(start + len(batch_result), data_len)
                reordered_results[start:end] = batch_result[:end - start]

        logger.info(f"[CITATION VERIFY]: 重排序校验结果处理完成，校验结果数量{len(reordered_results)}")
        return reordered_results

    @staticmethod
    def validate_llm_response_structure(result: dict) -> tuple:
        """验证LLM响应的结构是否完整
        检查LLM返回的结果字典是否包含所有必需的字段

        Args:
            result (dict): LLM响应结果

        Returns:
            tuple:(bool, str)
                - 第一个元素表示结果是否结构合法（True/False）
                - 如果合法，第二个元素为"valid"；否则为错误信息字符串
        """
        # 检查必须字段
        required_fields = ["source", "marked_citation_content", "score"]
        for field in required_fields:
            if field not in result:
                return False, f"missing required field: {field}"

        return True, "valid"

    async def run(self, datas: list) -> list:
        """执行溯源验证的主函数

        对输入的引用数据进行完整的溯源验证流程，包括提取引用来源、标记引用内容、计算置信度等

        Args:
            datas (list): 待验证的引用数据列表，每个元素是包含引用信息的字典

        Returns:
            list: 验证后的引用数据列表，每个元素包含验证结果信息
        """
        logger.info("[CITATION VERIFY]: CitationVerify running...")
        self.datas = datas
        # 提取引用的来源、发布时间、标记content中引用的内容、计算置信度score
        await self.get_source_date_mark_score()
        return self.datas

    async def process_batch(self, batch_idx: int, batch: list, context: BatchContext) -> None:
        """通用批次处理函数
        处理单个数据批次，控制并发数，记录处理状态，并处理可能的错误

        Args:
            batch_idx (int): 批次索引
            batch (list): 当前批次的数据列表
            context (BatchContext): 批次处理上下文，包含状态、函数、信号量和日志前缀等信息

        Returns:
            None
        """
        # 从上下文中提取变量，提高可读性
        state = context.batch_state
        semaphore = context.semaphore
        log_prefix = context.log_prefix
        process_func = context.process_func
        error_result_func = context.error_result_func

        # 等待获取信号量
        await semaphore.acquire()
        state["started_count"] += 1
        state["running_tasks"].add(batch_idx)

        logger.info(
            f"[{log_prefix}]: 开始处理批次 {batch_idx + 1}/{len(state['results'])}，批次大小:{len(batch)}")

        try:
            result = await process_func(batch)
            state["results"][batch_idx] = result
            state["completed_count"] += 1
            logger.info(f"[{log_prefix}]: 批次 {batch_idx + 1} 处理完成")
        except Exception as e:
            if LogManager.is_sensitive():
                logger.warning(f"[{log_prefix}]: 批次 {batch_idx + 1} 处理失败")
            else:
                logger.warning(f"[{log_prefix}]: 批次 {batch_idx + 1} 处理失败: {e}")
            state["results"][batch_idx] = error_result_func(batch)
            state["completed_count"] += 1
        finally:
            state["running_tasks"].remove(batch_idx)
            semaphore.release()

    def prepare_batch_processing(self, data: list, batch_size: int, log_prefix: str) -> tuple:
        """准备批次处理所需的数据结构
        将输入数据分成多个批次，并初始化批次处理状态

        Args:
            data (list): 待处理的数据列表
            batch_size (int): 每个批次的大小
            log_prefix (str): 日志前缀，用于标识不同的处理流程
        
        Returns:
            tuple: (batches, batch_state)
                - batches: 分批处理的数据列表，每个元素是一个元组(batch_idx, batch_data)
                - batch_state: 批次状态字典，包含results、running_tasks、completed_count、started_count等字段
        """
        data_len = len(data)
        logger.info(f"[{log_prefix}] 总数据量:{data_len}，批大小:{batch_size}， 最大并发数:{self.concurrent_limit}")

        # 对数据进行分批处理
        batches = [
            (i // batch_size, data[i:i + batch_size])
            for i in range(0, data_len, batch_size)
        ]
        logger.info(f"[{log_prefix}] 总批次数量: {len(batches)}")

        # 创建批次处理所需数据
        results = [None] * len(batches)
        running_tasks = set()

        # 创建批次状态字典，封装所有必要的状态信息
        batch_state = {
            "results": results,
            "running_tasks": running_tasks,
            "completed_count": 0,
            "started_count": 0
        }

        return batches, batch_state

    async def execute_batch_tasks(self, batches: list, batch_state: dict,
                                  process_func: Callable, error_func: Callable,
                                  log_prefix: str) -> None:
        """执行所有批次任务
        创建并执行所有批次处理任务，控制并发数

        Args:
            batches (list): 批次数据列表，每个元素是一个元组(batch_idx, batch_data)
            batch_state (dict): 批次状态字典
            process_func (Callable): 用于处理批次数据的函数
            error_func (Callable): 处理错误情况的函数
            log_prefix (str): 日志前缀，用于标识不同的处理流程

        Returns:
            None
        """
        # 创建共享的信号量控制并发数
        semaphore = asyncio.Semaphore(self.concurrent_limit)

        # 创建批次处理上下文
        context = BatchContext(
            batch_state=batch_state,
            process_func=process_func,
            error_result_func=error_func,
            semaphore=semaphore,
            log_prefix=log_prefix
        )

        # 创建并执行所有任务
        tasks = []
        for batch_idx, (_, batch) in enumerate(batches):
            task = asyncio.create_task(
                self.process_batch(batch_idx, batch, context))
            tasks.append(task)

        await asyncio.gather(*tasks)
        logger.info(f"[CITATION VERIFY]: {log_prefix} {len(batches)} 批次处理完成")

    async def process_batches_with_concurrency(self, data: list, batch_size: int,
                                               process_func: Callable, error_func: Callable,
                                               log_prefix: str = "") -> list:
        """并发处理数据批次
        将数据分成多个批次，并发处理每个批次，最终返回合并后的结果

        Args:
            data (list): 待处理的数据列表
            batch_size (int): 每个批次的大小
            process_func (Callable): 用于处理批次数据的函数
            error_func (Callable): 处理错误情况的函数
            log_prefix (str, optional): 日志前缀，用于标识不同的处理流程

        Returns:
            list: 处理后的结果列表，顺序与输入数据一致
        """
        # 1、准备阶段
        batches, batch_state = self.prepare_batch_processing(data, batch_size, log_prefix)

        # 2、执行阶段
        await self.execute_batch_tasks(batches, batch_state, process_func, error_func, log_prefix)

        # 3、结果整理阶段
        return self.reorder_batch_results(batches, batch_state["results"], batch_size, len(data))

    @staticmethod
    def is_chart_chunk(chunk: str) -> bool:
        """判断chunk是否是图表（包含图表标题div）

        Args:
            chunk (str): 要检查的chunk文本

        Returns:
            bool: 如果chunk包含图表标题div则返回True，否则返回False
        """
        if not chunk:
            return False
        chart_title_pattern = r'<div\s+style="text-align:\s*center;">'
        return bool(re.search(chart_title_pattern, chunk))

    async def prepare_handle_data(self) -> tuple:
        """预处理引用数据
        过滤有效引用，提取域名信息，查询映射表，进行匹配分类标记，构建适合后续处理的数据结构

        Returns:
            tuple: (handle_datas, handle_index)
                - handle_datas: 预处理后的数据列表，每个元素包含domain、citation_content、fact、is_chart及内部标记字段
                - handle_index: 原始数据索引列表，用于后续结果映射
        """
        handle_datas = []
        handle_index = []

        for index, data in enumerate(self.datas):
            data["valid"] = True
            chunk = data.get("chunk", "")
            
            # 检查是否是图表chunk
            is_chart = self.is_chart_chunk(chunk)
            if is_chart:
                data["is_chart"] = True  # 标记为图表
                if LogManager.is_sensitive():
                    logger.info(f"[VIZ_CITATION]: Chart chunk detected, index: {index}")
                else:
                    logger.info(
                        f"[VIZ_CITATION]: Chart chunk detected, index: {index}, "
                        f"chunk: {chunk}"
                    )
            
            # 检查vlm图表标记
            is_vlm_chart = data.get("is_vlm_chart", False)
            
            handle_index.append(index)
            url = data.get("url", "")
            if url.startswith("http"):  # 仅处理网页引用
                domain = extract_domain_from_url(url)
            else:
                domain = ""
            
            # 查询域名→来源映射表
            source_name, was_cached = await lookup_source(domain)
            
            citation_content = data.get("content", "")
            
            # 确保 registered_domain 使用提取后的注册域名，而非原始域名
            # 原始 domain 可能是 news.example.co.uk，但映射表的 key 是 example.co.uk
            registered_domain = _extract_registered_domain(domain) or domain
            
            # 匹配分类标记：图表引用走独立路径，跳过算法匹配
            if is_chart or is_vlm_chart:
                match_type = "chart"
                algo_marked_citation_content = []
                algo_score = 0.0
            else:
                match_type, algo_marked_citation_content, algo_score = (
                    classify_and_match(citation_content, chunk)
                )
            
            handle_datas.append(
                {"domain": domain,
                 "citation_content": citation_content,
                 "fact": chunk,
                 "domain_resolved": was_cached,
                 "resolved_source": source_name,
                 "registered_domain": registered_domain,
                 "match_type": match_type,
                 "algo_marked_citation_content": algo_marked_citation_content,
                 "algo_score": algo_score}
            )
        
        return handle_datas, handle_index

    def find_matching_content(self, marked_item: str, handle_data: dict) -> Optional[str]:
        """为标记项查找匹配的原始内容
        清理标记项并在原始数据中查找最相似的内容片段

        Args:
            marked_item (str): 要查找匹配的标记项文本
            handle_data (dict): 原始数据，包含citation_content等字段

        Returns:
            Optional[str]: 找到的匹配原始内容，如果没有找到则返回None
        """
        # 清理marked_item末尾的标点和空格
        cleaned_marked_item = re.sub(r'[\s，。！？；：、,.;:!?]+$', '', marked_item)

        citation_content = handle_data.get("citation_content", "")
        # 模糊匹配
        matches = self.find_matches(citation_content, [cleaned_marked_item], threshold=80)

        if matches:
            start_pos, end_pos = matches[0]
            # 从原始的citation_content中提取匹配的内容
            actual_matched_text = citation_content[start_pos:end_pos]
            return actual_matched_text

        return None

    def correct_marked_citation_content(self, result: dict, handle_data: dict) -> tuple:
        """使用模糊匹配修正标记的引用内容
        对LLM返回的标记引用内容进行模糊匹配，确保标记内容与原始数据一致

        Args:
            result (dict): LLM返回的单个结果
            handle_data (dict): 原始数据，包含citation_content等字段

        Returns:
            tuple: (bool, str or dict)
                - 第一个元素表示是否修正成功（True/False）
                - 第二个元素为修正后的结果字典（如果成功）或错误信息字符串（如果失败）
        """
        marked_content = result.get("marked_citation_content", [])
        if not marked_content:
            return True, result  # 无内容可匹配，视为成功

        corrected_result = result.copy()
        corrected_result["marked_citation_content"] = []

        found_any_match = False

        for marked_item in marked_content:
            # 查找匹配的原始内容
            actual_matched_text = self.find_matching_content(marked_item, handle_data)
            if actual_matched_text is not None:
                # 找到匹配内容
                corrected_result["marked_citation_content"].append(actual_matched_text)
                found_any_match = True
                if LogManager.is_sensitive():
                    logger.info(f"[CITATION VERIFY]: fuzzy match succeeded")
                else:
                    logger.info(
                        f"[CITATION VERIFY]: fuzzy match succeeded '{marked_item}' -> '{actual_matched_text}'")
            else:
                # 没有找到匹配，不保留原始值
                if LogManager.is_sensitive():
                    logger.warning(f"[CITATION VERIFY]: fuzzy match failed")
                else:
                    logger.warning(f"[CITATION VERIFY]: fuzzy match failed '{marked_item}'")

        if not found_any_match:
            error_msg = "No fuzzy matches found for any marked content"
            if LogManager.is_sensitive():
                return False, error_msg
            return False, f"{error_msg}: {marked_content}"

        return True, corrected_result

    def validate_and_correct_llm_response(self, result: dict, handle_data: dict) -> tuple:
        """验证并修正LLM响应
        检查LLM返回结果的结构是否合法，并使用模糊匹配修正标记的引用内容

        Args:
            result (dict): LLM返回的单个结果
            handle_data (dict): 原始数据，包含citation_content等字段

        Returns:
            tuple: (bool, str or dict)
                - 第一个元素表示是否验证和修正成功（True/False）
                - 第二个元素为修正后的结果字典（如果成功）或错误信息字符串（如果失败）
        """
        # 首先验证结构
        is_valid, error_msg = self.validate_llm_response_structure(result)
        if not is_valid:
            return False, error_msg

        # 然后修正标记的内容
        is_corrected, processed_result = self.correct_marked_citation_content(result, handle_data)

        return is_corrected, processed_result

    async def extract_messages_batch(self, handle_datas: list) -> list:
        """调用LLM提取引用信息
        批量调用LLM模型，从引用内容中提取来源、日期、标记引用内容和置信度分数

        Args:
            handle_datas (list): 预处理后的数据列表，包含domain、citation_content、fact字段

        Returns:
            list: 提取的引用信息列表，每个元素包含source、marked_citation_content、score字段
        """
        agent_input = dict(datas=handle_datas)
        user_prompt = apply_system_prompt("extract_message_prompt", agent_input)

        # extract source, date, mark citation content and score
        retries = 0
        while retries < MAX_LLM_RETRY_TIMES:
            try:
                response = await self.call_model(user_prompt)
                result = json.loads(response.replace("```json", "").replace("```", ""))

                if len(handle_datas) != len(result):
                    error_msg = f"[CITATION VERIFY]: LLM提取结果数量错误,"
                    error_msg += f"提取结果数量{len(result)}, 处理数量{len(handle_datas)}"
                    raise CustomValueException(StatusCode.CITATION_VERIFIER_DATA_LEN_ERROR.code,
                                               StatusCode.CITATION_VERIFIER_DATA_LEN_ERROR.errmsg.
                                               format(e=error_msg))

                corrected_results = []
                all_valid = True
                error_messages = []
                for i, r in enumerate(result):
                    is_valid, processed_result = self.validate_and_correct_llm_response(r, handle_datas[i])
                    if is_valid:
                        corrected_results.append(processed_result)
                    else:
                        all_valid = False
                        if isinstance(processed_result, str):
                            error_messages.append(processed_result)

                if not all_valid:
                    error_msg = ";".join(
                        error_messages) if error_messages else "citation verify llm response validation failed"
                    raise CustomValueException(
                        StatusCode.CITATION_VERIFIER_LLM_RESPONSE_ERROR.code,
                        StatusCode.CITATION_VERIFIER_LLM_RESPONSE_ERROR.errmsg.format(e=error_msg)
                        )
                return corrected_results
            except CustomValueException as e:
                retries += 1
                logger.warning(f'[CITATION VERIFY] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                               f'extract_source_date_mark_score error {e}')
            except Exception as e:
                retries += 1
                if LogManager.is_sensitive():
                    logger.warning(f'[CITATION VERIFY] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                                   f'extract_source_date_mark_score error')
                else:
                    logger.warning(f'[CITATION VERIFY] retry: {retries}/{MAX_LLM_RETRY_TIMES}, '
                                   f'extract_source_date_mark_score error {e}')

        logger.error(f'[CITATION VERIFY] retry {MAX_LLM_RETRY_TIMES} times, extract_source_date_mark_score error')
        return [{"extract_failed_reason": "LLM retry times exceeded"} for _ in handle_datas]

    def update_citation_data(self, handle_index: list, ordered_results: list, handle_datas: list) -> None:
        """更新引用数据

        将提取的引用信息更新到原始数据中，包括来源、置信度分数和标记的引用内容

        Args:
            handle_index (list): 原始数据索引列表，用于结果映射
            ordered_results (list): 按原始顺序排列的提取结果列表
            handle_datas (list): 原始数据列表，用于填充未解析出的引用来源
        Returns:
            None
        """
        if len(ordered_results) != len(handle_index):
            error_msg = f"[CITATION VERIFY]: LLM排序结果数量错误,"
            error_msg += f"排序结果数量{len(ordered_results)}, 处理索引数量{len(handle_index)}"
            raise CustomValueException(StatusCode.CITATION_VERIFIER_DATA_LEN_ERROR.code,
                                       StatusCode.CITATION_VERIFIER_DATA_LEN_ERROR.errmsg.
                                       format(e=error_msg))

        for idx, ordered_result in zip(handle_index, ordered_results):
            is_chart = handle_datas[idx].get("is_chart", False) or self.datas[idx].get("is_chart", False)
            
            # 图表数据处理
            if is_chart:
                self.datas[idx]["source"] = ordered_result.get(
                    "source", "unknown source")
                if "unknown" in self.datas[idx]["source"] or "extract_failed_reason" in ordered_result:
                    self.datas[idx]["source"] = handle_datas[idx]["domain"]
                marked_content = ordered_result.get("marked_citation_content", [])
                if marked_content:
                    content = self.datas[idx].get("content", "")
                    self.datas[idx]["content"] = self.fuzzy_find_and_tag(content, marked_content)
                score = ordered_result.get("score", 0)
                self.datas[idx]["score"] = max(score, 0.85)
                if LogManager.is_sensitive():
                    logger.info(f"[VIZ_CITATION] Chart data processed and updated, index: {idx}")
                else:
                    logger.info(
                        f"[VIZ_CITATION] Chart data processed and updated, index: {idx}, "
                        f"ordered_result: {ordered_result}, data: {self.datas[idx]}"
                    )
                continue
            
            # 非图表数据处理
            if "extract_failed_reason" in ordered_result:
                self.datas[idx]["valid"] = False
                self.datas[idx]["invalid_reason"] = ordered_result["extract_failed_reason"]
                continue
            self.datas[idx]["source"] = ordered_result.get(
                "source", "unknown source")
            if "unknown" in self.datas[idx]["source"]:
                self.datas[idx]["source"] = handle_datas[idx]["domain"]
            if self.datas[idx].get("is_vlm_chart", False):
                # vlm迭代生成图的图表溯源分数使用vlm模型的图表打分，如果没有经历vlm迭代优化，则使用溯源模块的打分
                self.datas[idx]["score"] = max(self.datas[idx]["score"], 
                                               ordered_result.get("score", 0))
            else:
                self.datas[idx]["score"] = ordered_result.get("score", 0)
            if self.datas[idx]["score"] < 0.85:
                self.datas[idx]["valid"] = False
                self.datas[idx]["invalid_reason"] = "score lower than threshold"
                continue
            if not ordered_result.get("marked_citation_content", []):
                if self.datas[idx].get("is_vlm_chart", False):
                    # vlm图表由于是用llm生成的图表描述去匹配高亮引用的，如果匹配不到也要显示图表的溯源
                    continue
                self.datas[idx]["valid"] = False
                self.datas[idx]["invalid_reason"] = "marked citation content empty"
                continue
            content = self.datas[idx].get("content", "")
            self.datas[idx]["content"] = self.fuzzy_find_and_tag(
                content, ordered_result["marked_citation_content"])

    async def get_source_date_mark_score(self) -> None:
        """获取引用数据的详细信息

        批量获取引用数据的来源、日期、标记的引用内容和置信度分数。
        实现前置分流逻辑：exact match + domain resolved → 算法快速路径；
        其他 → LLM 路径。

        Returns:
            None
        """
        logger.info("[CITATION VERIFY]: get source, date, mark citation content and score of url.")

        handle_datas, handle_index = await self.prepare_handle_data()
        if not handle_datas:
            return
        logger.info("[CITATION VERIFY]: prepare handle data success.")

        # 前置分流：快速路径 vs LLM 路径
        fast_path_indices = []   # handle_datas 中的索引，走算法快速路径
        llm_path_indices = []    # handle_datas 中的索引，走 LLM 路径

        # 分流统计计数器
        match_type_counts = {"exact": 0, "fuzzy": 0, "no_match": 0, "chart": 0}
        domain_resolved_true = 0
        domain_resolved_false = 0

        for i, hd in enumerate(handle_datas):
            mt = hd.get("match_type", "no_match")
            match_type_counts[mt] = match_type_counts.get(mt, 0) + 1
            if hd.get("domain_resolved", False):
                domain_resolved_true += 1
            else:
                domain_resolved_false += 1

            if hd.get("domain_resolved", False) and mt == "exact":
                fast_path_indices.append(i)
            else:
                llm_path_indices.append(i)

        logger.info(
            f"[CITATION VERIFY]: routing stats - total={len(handle_datas)}, "
            f"match_types={match_type_counts}, "
            f"domain_resolved={domain_resolved_true}, domain_unresolved={domain_resolved_false}, "
            f"fast_path={len(fast_path_indices)}, llm_path={len(llm_path_indices)}"
        )

        # 快速路径：算法直接输出结果
        fast_path_results = []
        for i in fast_path_indices:
            hd = handle_datas[i]
            fast_path_results.append({
                "source": hd["resolved_source"],
                "marked_citation_content": hd["algo_marked_citation_content"],
                "score": hd["algo_score"],
            })

        # LLM 路径：批次处理（剥离内部标记字段，避免泄漏到LLM prompt）
        _internal_keys_for_llm_strip = {"domain_resolved", "resolved_source", "registered_domain",
                                        "match_type", "algo_marked_citation_content", "algo_score"}
        llm_path_datas = [
            {k: v for k, v in handle_datas[i].items() if k not in _internal_keys_for_llm_strip}
            for i in llm_path_indices
        ]
        llm_ordered_results = []
        if llm_path_datas:
            llm_ordered_results = await self.process_batches_with_concurrency(
                data=llm_path_datas,
                batch_size=self.verify_batch_size,
                process_func=self.extract_messages_batch,
                error_func=lambda b: [{} for _ in b],
                log_prefix="get_source_date_mark_score_llm_path"
            )
            logger.info("[CITATION VERIFY]: LLM path process batches success.")

        # 合并结果：按原始 handle_datas 顺序对齐
        merged_results = [None] * len(handle_datas)
        for idx_in_fast, original_idx in enumerate(fast_path_indices):
            merged_results[original_idx] = fast_path_results[idx_in_fast]
        for idx_in_llm, original_idx in enumerate(llm_path_indices):
            merged_results[original_idx] = llm_ordered_results[idx_in_llm]

        # 回写映射表：domain_resolved=False 且 source 有效
        await self._save_new_mappings(handle_datas, merged_results)

        # 清除内部标记字段，防止泄漏到最终datas
        _internal_keys = {"domain_resolved", "resolved_source", "registered_domain",
                         "match_type", "algo_marked_citation_content", "algo_score"}
        for hd in handle_datas:
            for key in _internal_keys:
                hd.pop(key, None)

        # 更新引用数据
        self.update_citation_data(handle_index, merged_results, handle_datas)
        logger.info("[CITATION VERIFY]: update citation data success.")

    async def _save_new_mappings(self, handle_datas: list, results: list) -> None:
        """回写映射表：将LLM推断出的 domain→source 映射保存

        仅对 domain_resolved=False 且 source 有效的条目执行 save_mapping。
        同一 registered_domain 只执行一次 save_mapping（去重）。
        "unknown source" 不保存映射。

        Args:
            handle_datas: 预处理后的数据列表（含内部标记字段）
            results: 合并后的结果列表（含 source 字段）
        """
        saved_domains = set()
        skipped_unknown = 0
        skipped_dedup = 0
        skipped_empty_domain = 0
        for i, hd in enumerate(handle_datas):
            if not hd.get("domain_resolved", False):
                registered_domain = hd.get("registered_domain", "")
                result = results[i] if i < len(results) else {}
                source = result.get("source", "") if result else ""

                # "unknown source" 不保存映射（含 "unknown" 关键词均不保存）
                if not source or "unknown" in source.lower():
                    skipped_unknown += 1
                    continue

                # 去重：同一 registered_domain 只执行一次
                if registered_domain in saved_domains:
                    skipped_dedup += 1
                    continue

                # 空 domain 不保存
                if not registered_domain:
                    skipped_empty_domain += 1
                    continue

                saved_domains.add(registered_domain)
                await save_mapping(registered_domain, source)

        logger.info(
            f"[CITATION VERIFY]: mapping save stats - saved={len(saved_domains)}, "
            f"skipped_unknown={skipped_unknown}, skipped_dedup={skipped_dedup}, "
            f"skipped_empty_domain={skipped_empty_domain}"
        )

    def fuzzy_find_and_tag(
        self,
        text: str,
        fragments: list,
        tag_template: str = "<mark>{}</mark>",
        threshold: int = 90
    ) -> str:
        """
        使用精确匹配优先，模糊匹配为辅的策略匹配文本片段并插入标签

        Args:
            text (str): 原始文本
            fragments (list): 要匹配的文本片段列表
            tag_template (str, optional): 标签模版（默认高亮标记）.
            threshold (int, optional): 模糊匹配的相似度阈值.

        Returns:
            str: 插入标签后的文本
        """
        # 查找所有匹配的位置
        tagged_positions = self.find_matches(text, fragments, threshold)

        # 预处理，按字符分割文本（适配中文）
        text_chars = list(text)

        # 按位置从后往前插入标签（避免偏移问题）
        for start, end in sorted(tagged_positions, reverse=True):
            tagged = tag_template.format(text[start:end])
            text_chars[start:end] = [tagged]

        return ''.join(text_chars)

    async def call_model(self, user_prompt: list) -> str:
        """调用LLM模型处理请求
        调用指定的LLM模型处理用户提示，并返回标准化的JSON格式输出

        Args:
            user_prompt (list): 用户提示消息

        Returns:
            str: 标准化的JSON格式输出字符串
        """
        llm = llm_context.get().get(self.llm_model)
        response = await ainvoke_llm_with_stats(llm, user_prompt,
                                                agent_name=AgentLlmName.SOURCE_TRACER_EXTRACT_MESSAGES.value)
        if not isinstance(response, dict):
            if LogManager.is_sensitive():
                logger.warning(f'[CITATION VERIFY] LLM return non-dict type: {type(response)}')
            else:
                logger.warning(f'[CITATION VERIFY] LLM return non-dict type: {type(response)}. {response}')
            return "[]"

        content = response.get("content", "")
        try:
            data = json.loads(content)
            if isinstance(data, list) and all(isinstance(i, dict) for i in data):
                return normalize_json_output(content)
            if LogManager.is_sensitive():
                logger.warning(f'[CITATION VERIFY] LLM return content type error {type(content)}')
            else:
                logger.warning(f'[CITATION VERIFY] LLM return content type error {type(content)}. {content}')
            return "[]"
        except Exception:
            if LogManager.is_sensitive():
                logger.warning(f'[CITATION VERIFY] LLM return content is not json.')
            else:
                logger.warning(f'[CITATION VERIFY] LLM return content is not json. {content}')
            return "[]"
