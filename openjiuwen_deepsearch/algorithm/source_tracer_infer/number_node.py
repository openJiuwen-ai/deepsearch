# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import logging
import difflib
import re
from typing import List, Dict, Tuple

from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import GraphInfo, NumberNodeParam

logger = logging.getLogger(__name__)


class NumberNode:
    def __init__(self):
        pass

    @staticmethod
    def _wr_ratio(str1: str, str2: str) -> float:
        """
        计算两个字符串的加权比率相似度（模拟 rapidfuzz.fuzz.WRatio）
        使用 difflib.SequenceMatcher 计算相似度，返回 0-100 的分数
        """
        return difflib.SequenceMatcher(None, str1, str2).ratio() * 100

    @staticmethod
    def _partial_ratio(str1: str, str2: str) -> float:
        """
        计算部分匹配比率（模拟 rapidfuzz.fuzz.partial_ratio）
        找到较短字符串在较长字符串中的最佳匹配位置，计算相似度
        """
        if not str1 or not str2:
            return 0.0
        
        # 确保 str1 是较短的字符串
        if len(str1) > len(str2):
            str1, str2 = str2, str1
        
        best_ratio = 0.0
        # 滑动窗口在较长字符串中查找最佳匹配
        for i in range(len(str2) - len(str1) + 1):
            segment = str2[i:i + len(str1)]
            ratio = difflib.SequenceMatcher(None, str1, segment).ratio() * 100
            if ratio > best_ratio:
                best_ratio = ratio
        
        return best_ratio

    @staticmethod
    def _token_set_ratio(str1: str, str2: str) -> float:
        """
        计算基于token集合的匹配比率（模拟 rapidfuzz.fuzz.token_set_ratio）
        将字符串分词成token集合，然后计算相似度
        """
        # 使用正则表达式分词（支持中英文）
        def tokenize(text: str) -> set:
            # 按空格、标点符号分词，保留中文字符
            tokens = re.findall(r'\w+|[^\w\s]', text.lower())
            return set(tokens)
        
        tokens1 = tokenize(str1)
        tokens2 = tokenize(str2)
        
        if not tokens1 or not tokens2:
            return 0.0
        
        # 计算交集和并集
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        
        if not union:
            return 0.0
        
        # 计算Jaccard相似度（交集大小 / 并集大小）
        jaccard = len(intersection) / len(union) * 100
        
        # 将token集合转换为排序后的字符串，计算字符串相似度
        intersection_str = ' '.join(sorted(intersection))
        union_str1 = ' '.join(sorted(tokens1))
        union_str2 = ' '.join(sorted(tokens2))
        
        # 计算交集字符串与两个并集字符串的相似度
        ratio1 = difflib.SequenceMatcher(
            None, intersection_str, union_str1).ratio() * 100 if union_str1 else 0
        ratio2 = difflib.SequenceMatcher(
            None, intersection_str, union_str2).ratio() * 100 if union_str2 else 0
        
        # 综合Jaccard相似度和字符串相似度
        return (jaccard + (ratio1 + ratio2) / 2) / 2

    @staticmethod
    def _extract_best_match(query: str, choices: list, limit: int = 1) -> list:
        """
        从候选列表中提取最匹配的项（模拟 rapidfuzz.process.extract）
        返回格式: [(matched_string, score), ...]
        """
        if not choices:
            return []
        
        # 计算每个候选的相似度分数
        scored_choices = []
        for choice in choices:
            score = NumberNode._wr_ratio(query, choice)
            scored_choices.append((choice, score))
        
        # 按分数降序排序
        scored_choices.sort(key=lambda x: x[1], reverse=True)
        
        # 返回前 limit 个结果
        return scored_choices[:limit]

    def number_node(self, structured_inference: List[List], conclusion: str, search_records: List[Dict]) -> GraphInfo:
        """
        给节点编号
        Args:
            structured_inference: 结构化推理过程
            conclusion：结论
            search_records：搜索记录
        Returns:
            GraphInfo=(
            new_structured_inference: 编号优化后的结构化推理过程
            node_map: 节点id字典
            citation_ids: 引用节点id序列
            conclusion_ids: 最终结论节点id序列
            )
        """
        logger.info(f"[SOURCE TRACER INFER] number_node starting...")
        new_structured_inference = []
        # 使用 dataclass 的默认值创建全新的参数对象，避免不同调用间状态共享
        number_node_param = NumberNodeParam()
        try:
            for item in structured_inference:
                head_list, relation, tail = item
                head_id_list = []
                if not isinstance(head_list, list):
                    head_list = [head_list]
                # 处理头实体
                for head in head_list:
                    if isinstance(head, int):
                        # 引用节点
                        title, url = self.replace_index_with_url(head, search_records)
                        if title == "ProgrammerNode":
                            # programmer node 输出的特殊引用
                            number_node_param, head_id = self.number_programmer_node(head, number_node_param)
                        else:
                            # 有url的引用节点
                            number_node_param, head_id = self.number_citation_node(head, number_node_param, 
                                                                                   title, url)
                    else:
                        number_node_param, head_id = self.number_conclusion_node(head, number_node_param, conclusion)
                    head_id_list.append(head_id)
                # 处理尾实体
                number_node_param, tail_id = self.number_conclusion_node(tail, number_node_param, conclusion)
                new_structured_inference.append([head_id_list, relation, tail_id])
        except Exception as e:
            raise ValueError(f"ERROR in NUMBER_NODE: {e}") from e
        logger.debug(
            "[SOURCE TRACER INFER] number_node result\n %s", 
            json.dumps(new_structured_inference, ensure_ascii=False, indent=4))
        citation_ids = list(number_node_param.citation_ids)
        conclusion_ids = list(number_node_param.conclusion_ids)
        node_map = number_node_param.node_map
        return GraphInfo(structured_inference=new_structured_inference, 
                         node_map=node_map, citation_ids=citation_ids, conclusion_ids=conclusion_ids)

    @staticmethod
    def number_citation_node(node, number_node_param: NumberNodeParam, title, url) -> Tuple[NumberNodeParam, int]:
        """
        给引用节点编号
        """
        try:
            node_set = number_node_param.node_set
            node_map = number_node_param.node_map
            node_index = number_node_param.node_index
            citation_ids = number_node_param.citation_ids
            conclusion_ids = number_node_param.conclusion_ids
            node_id = -1
            node = str(node).strip()
            node_set.add(node)
            # 检测记录中是否有该引用
            for i, v in node_map.items():
                if v.get("url", "") == url:
                    node_id = i
                    break
            if node_id != -1:
                return NumberNodeParam(node_set=node_set, node_map=node_map,
                                       node_index=node_index, citation_ids=citation_ids,
                                       conclusion_ids=conclusion_ids), node_id
            else:
                # 新节点，编号
                head = f"《{title}》"
                node_map[node_index] = {"label": head, "url": url}
                citation_ids.add(node_index)
                node_id = node_index
                node_index += 1
        except Exception as e:
            if LogManager.is_sensitive():
                logger.warning(f'[SOURCE TRACER INFER] number_citation_node error: ***')
            else:
                logger.warning(f'[SOURCE TRACER INFER] number_citation_node error: {e}')
            raise e
        return NumberNodeParam(node_set=node_set, node_map=node_map,
                               node_index=node_index, citation_ids=citation_ids,
                               conclusion_ids=conclusion_ids), node_id

    def number_conclusion_node(self, node, number_node_param: NumberNodeParam, conclusion):
        """
        给结论节点编号
        """
        try:
            node_set = number_node_param.node_set
            node_map = number_node_param.node_map
            node_index = number_node_param.node_index
            citation_ids = number_node_param.citation_ids
            conclusion_ids = number_node_param.conclusion_ids
            node_id = -1
            node = str(node).strip()
            node_match = self._extract_best_match(node, list(node_set), limit=1)
            if node_match and node_match[0][1] > 90:
                # 已被编号的节点直接取号
                for i, v in node_map.items():
                    if v.get("label", "") == node_match[0][0]:
                        node_id = i
            else:
                # 新节点，编号
                node_set.add(node)
                node_map[node_index] = {"label": node}
                node_id = node_index
                node_index += 1
                if self._partial_ratio(node, conclusion) > 60 or self._token_set_ratio(node, conclusion) > 60:  # 结论节点
                    conclusion_ids.add(node_id)
        except Exception as e:
            if LogManager.is_sensitive():
                logger.warning(f'[SOURCE TRACER INFER] number_conclusion_node error: ***')
            else:
                logger.warning(f'[SOURCE TRACER INFER] number_conclusion_node error: {e}')
            raise e

        return NumberNodeParam(
            node_set=node_set,
            node_map=node_map,
            node_index=node_index,
            citation_ids=citation_ids,
            conclusion_ids=conclusion_ids,
        ), node_id

    @staticmethod
    def number_programmer_node(node, number_node_param: NumberNodeParam):
        """
        给programmer node的表述节点编号（看作特殊的引用）
        """
        try:
            node_set = number_node_param.node_set
            node_map = number_node_param.node_map
            node_index = number_node_param.node_index
            citation_ids = number_node_param.citation_ids
            conclusion_ids = number_node_param.conclusion_ids
            node_id = -1
            node = str(node).strip()
            node_set.add(node)
            for i, v in node_map.items():
                if v.get("label", "") == node:
                    node_id = i
                    break
            if node_id != -1:
                return NumberNodeParam(node_set=node_set, node_map=node_map,
                                       node_index=node_index, citation_ids=citation_ids,
                                       conclusion_ids=conclusion_ids), node_id
            else:
                # 新节点，编号
                node_map[node_index] = {"label": node, "is_program_info": True}
                citation_ids.add(node_index)
                node_id = node_index
                node_index += 1
        except Exception as e:
            if LogManager.is_sensitive():
                logger.warning(f'[SOURCE TRACER INFER] number_programmer_node error: ***')
            else:
                logger.warning(f'[SOURCE TRACER INFER] number_programmer_node error: {e}')
            raise e
        return NumberNodeParam(node_set=node_set, node_map=node_map,
                               node_index=node_index, citation_ids=citation_ids,
                               conclusion_ids=conclusion_ids), node_id

    @staticmethod
    def replace_index_with_url(index: int, search_records: List[Dict]) -> Tuple[str, str]:
        record = search_records[index]
        return record.get("title", ""), record.get("url", "")