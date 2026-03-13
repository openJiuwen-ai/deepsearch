# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from collections import deque
import logging
import networkx as nx

from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import call_model, is_equal_length, GraphInfo
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


class SupplementGraph:
    def __init__(self, model_name):
        self.model_name = model_name

    async def run(self, graph_info: GraphInfo) -> GraphInfo:
        """
        检查存在的自环并删除，检查是否非连通，是则修补，无法修补则删除非必要子图使结果为连通图
        Args:
            graph_info: tuple(new_structured_inference, node_map, citation_ids, conclusion_ids)
        Returns:
            tuple(new_structured_inference, node_map, citation_ids, conclusion_ids)
        """
        logger.info(f"[SOURCE TRACER INFER] check_and_supplement_graph starting...")
        try:
            new_structured_inference = graph_info.structured_inference
            node_map = graph_info.node_map
            citation_ids = graph_info.citation_ids
            conclusion_ids = graph_info.conclusion_ids
            # 生成图
            graph, new_structured_inference = self.generate_graph(new_structured_inference)
            # 过滤不符合最终结论节点要求的节点 (过滤掉有出边的结论节点)
            conclusion_ids = self.filter_conclusion_node(graph, conclusion_ids)
            # 如果过滤后conclusion_ids为空，则无最终结论节点，或最终结论节点多于1个，该图不符合输出条件，过滤掉
            if len(conclusion_ids) != 1:
                logger.warning(f"[SOURCE TRACER INFER] The count of final conclusion node should be ONE.")
                raise ValueError(f"Graphs with a number of conclusion nodes not equal to 1 are filtered out.")
            # 删除没有入边的结论节点
            (new_structured_inference, 
             node_map, conclusion_ids) = self.remove_no_indegree_conclusion_node(new_structured_inference, 
                                                                                node_map, citation_ids, 
                                                                                conclusion_ids)
            graph, new_structured_inference = self.generate_graph(new_structured_inference)
            if nx.is_weakly_connected(graph):
                # 连通图
                logger.info(f"[SOURCE TRACER INFER] There is a connected graph. Return origin graph.")
                # 剪枝
                new_graph_info = self.cut_branch(new_structured_inference, node_map, citation_ids, conclusion_ids)
                return new_graph_info
            # 非连通图，先补边修复
            new_tuples = await self.supplement_graph(graph, node_map, citation_ids, conclusion_ids)
            if new_tuples:
                new_structured_inference.extend(new_tuples)
                # 添加的三元组不能是无依据理论节点
                new_structured_inference, node_map, conclusion_ids = self.remove_no_indegree_conclusion_node(
                    new_structured_inference, node_map, citation_ids, conclusion_ids)
                graph, new_structured_inference = self.generate_graph(new_structured_inference)
                # 再次检测是否连通
                if nx.is_weakly_connected(graph):
                    # 连通图
                    logger.info(f"[SOURCE TRACER INFER] Successfully completed the disconnected graph.")
                    # 剪枝
                    new_graph_info = self.cut_branch(new_structured_inference, node_map, citation_ids, conclusion_ids)
                    return new_graph_info
            # 无法修复的非连通图
            # 仅保留必要子图（包含最终结论的子图）
            remove_nodes = self.remove_disconnected_subgraph(graph, conclusion_ids)
            # 更新删除子图后的结构推理图和节点映射
            new_structured_inference, node_map = self.update_graph_info_with_remove_nodes(new_structured_inference, 
                                                                                         node_map, remove_nodes)
            # 剪枝
            new_graph_info = self.cut_branch(new_structured_inference, node_map, citation_ids, conclusion_ids)
        except Exception as e:
            if LogManager.is_sensitive():
                logger.warning(f"[SOURCE TRACER INFER] ERROR in SupplementGraph: ***")
            else:
                logger.warning(f"[SOURCE TRACER INFER] ERROR in SupplementGraph: {e}")
            raise e
        return new_graph_info

    @staticmethod
    def generate_graph(structured_inference):
        """生成有向图，并删除图中的自环"""
        # 组织成图
        graph = nx.DiGraph()
        graph_node_connection = []
        new_structured_inference = []
        for structure in structured_inference:
            head_ids, relation, tail_id = structure
            if tail_id in head_ids:
                # 删除自环
                head_ids.remove(tail_id)
                if not head_ids:
                    continue # 删除自环三元组
            for head_id in head_ids:
                graph_node_connection.append((head_id, tail_id, {"label": relation}))
            new_structured_inference.append([head_ids, relation, tail_id])
        graph.add_edges_from(graph_node_connection)

        # 如果自环自成非连通图中的子图，还需要删除对应的node_map, 这种情况尚未发现，不做处理
        return graph, new_structured_inference

    @staticmethod
    def filter_conclusion_node(graph, conclusion_ids):
        """从序列中过滤掉不符合最终结论节点要求的节点"""
        logger.info(f"[SOURCE TRACER INFER] filter_conclusion_node starting...")
        logger.info(f"[SOURCE TRACER INFER] The input conclusion_id is {conclusion_ids}.")
        # 提取最终结论节点
        new_conclusion_ids = []
        for c_id in conclusion_ids:
            if graph.out_degree(c_id) == 0:
                # 最终结论没有出边
                new_conclusion_ids.append(c_id)
        logger.info(f"[SOURCE TRACER INFER] The filtered conclusion_ids is {new_conclusion_ids}.")
        return new_conclusion_ids

    def remove_no_indegree_conclusion_node(self, structured_inference, node_map, citation_ids, conclusion_ids):
        """移除没有入边的结论节点（无来源结论）"""
        logger.info(f"[source_tracer_infer] remove_no_indegree_conclusion_node starting...")
        logger.info(f"[source_tracer_infer] The structured inference before removing is\n {structured_inference}.")
        graph, structured_inference = self.generate_graph(structured_inference)
        remove_nodes = set()
        del_structure_index = []
        for index, (head_ids, _, tail_id) in enumerate(structured_inference):
            for head_id in head_ids:
                if head_id not in citation_ids and graph.in_degree(head_id) == 0:
                    if head_id in node_map:
                        head_ids.remove(head_id)
                        del node_map[head_id]
                        remove_nodes.add(head_id)
            # 检测尾实体在删除头实体后是否变成新的没有入边的结论节点
            tail_node_parents = set(list(graph.predecessors(tail_id)))
            is_subset = tail_node_parents.issubset(remove_nodes)
            if is_subset and tail_id in node_map:
                # 尾实体是没入边的结论，删除
                del_structure_index.append(index)
                del node_map[tail_id]
                remove_nodes.add(tail_id)
        conclusion_ids = [i for i in conclusion_ids if i not in remove_nodes]
        new_structured_inference = [structure for index, structure in enumerate(structured_inference) 
                                    if index not in del_structure_index]
        logger.info(f"[source_tracer_infer] The structured inference after removing is\n {new_structured_inference}")
        return new_structured_inference, node_map, conclusion_ids

    async def supplement_graph(self, graph, node_map, conclusion_ids, citation_ids):
        """
        加边修补非连通子图
        """
        logger.info(f"[source_tracer_infer] supplement_graph starting...")
        # 非连通图，先补边修复
        connected_components = list(nx.weakly_connected_components(graph))
        # 组织node_id和对应的结论
        llm_input = []
        for comp in connected_components:
            input_comp = []
            for node_id in comp:
                # 只传输非最终结论的结论信息
                if node_id not in citation_ids and node_id not in conclusion_ids:
                    input_comp.append({"id": node_id, "label": node_map[node_id].get("origin_text", "")})
            llm_input.append(input_comp)

        detection_func_and_args = {"detection_func": is_equal_length, "args": 3} # 需要添加检测函数，检测输出的每个结构为三元组
        new_tuples = await call_model(self.model_name, "infer_supplement_prompt", 
                                      {"graphs": llm_input}, detection_func_and_args, 
                                      agent_name=NodeId.SOURCE_TRACER_INFER.value + "_supplement_graph")
        # 去除可能存在的来自同一连通分量的新关系
        del_tuple_index = []
        for index, new_tuple in enumerate(new_tuples):
            for comp in connected_components:
                if new_tuple[0][0] in comp and new_tuple[2] in comp:
                    # 首尾节点来自同一个连通分量，删除
                    del_tuple_index.append(index)
                elif new_tuple[0][0] not in node_map or new_tuple[2] not in node_map:
                    # 捏造的不存在的节点，删除
                    del_tuple_index.append(index)
        new_tuples = [t for index, t in enumerate(new_tuples) if index not in del_tuple_index]
        logger.info(f"[source_tracer_infer]: supplement_graph end, the new_relations is: {new_tuples}")
        return new_tuples

    @staticmethod
    def remove_disconnected_subgraph(graph, conclusion_ids):
        """
        删除非连通图中非主要子图, conclusion_ids通常长度仅为1
        """
        logger.info("[source_tracer_infer]: remove_disconnected_subgraph starting...")
        remove_nodes = []
        # 遍历所有子图，删除不包含最终结论节点的子图
        connected_components = list(nx.weakly_connected_components(graph))
        for comp in connected_components:
            has_conclusion_id = False
            for node_id in comp:
                if node_id in conclusion_ids:
                    has_conclusion_id = True
                    break
            if not has_conclusion_id:
                # 删除子图
                remove_nodes.extend(list(comp))
        logger.info(f"[source_tracer_infer]: remove_disconnected_subgraph end. Remove node ids: {remove_nodes}")
        return remove_nodes

    @staticmethod
    def update_graph_info_with_remove_nodes(structured_inference, node_map, remove_nodes):
        """将非连通图中的非关键子图删除"""
        logger.info(f"[SOURCE TRACER INFER] update_graph_info_with_remove_nodes starting...")
        del_structure_index = []
        if remove_nodes:
            for index, (head_id_list, _, tail_id) in enumerate(structured_inference):
                if tail_id in remove_nodes:
                    del_structure_index.append(index)
                    if tail_id in node_map:
                        node_map.pop(tail_id)
                    for head_id in head_id_list:
                        if head_id in node_map:
                            node_map.pop(head_id)
            structured_inference = [structure for index, structure in enumerate(structured_inference) 
                                    if index not in del_structure_index]
        return structured_inference, node_map

    def cut_branch(self, new_structured_inference, node_map, citation_ids, conclusion_ids) -> GraphInfo:
        """对图谱剪枝，剪掉冗余的分支"""
        logger.info("[SOURCE TRACER INFER] cut_branch starting...")
        graph, new_structured_inference = self.generate_graph(new_structured_inference)
        visit_node_ids = deque(conclusion_ids)
        save_node_set = set()
        while visit_node_ids:
            visit_node = visit_node_ids.popleft()
            if visit_node not in save_node_set:
                save_node_set.add(visit_node)
                if visit_node not in citation_ids: # 引用节点没有前驱
                    # 遍历没有遍历过的节点，且该节点不为引用节点（引用节点无前驱）
                    predecessors = list(graph.predecessors(visit_node))
                    visit_node_ids.extend(predecessors)
        # save_node_set 之外的是冗余节点
        # 删除冗余分支，更新图谱结构
        new_structured_inference, node_map, citation_ids = self._del_redundant_node(new_structured_inference, node_map,
                                                                                    citation_ids, save_node_set)
        return GraphInfo(structured_inference=new_structured_inference, 
                         node_map=node_map, citation_ids=citation_ids, conclusion_ids=conclusion_ids)

    @staticmethod
    def _del_redundant_node(structured_inference, node_map, citation_ids, save_node_set):
        """删除剪枝后的冗余三元组和节点"""
        # 删除 structured_inference 中的冗余
        new_structured_inference = []
        for index, (head_id_list, _, tail_id) in enumerate(structured_inference):
            if tail_id in save_node_set and (set(head_id_list) <= save_node_set):
                new_structured_inference.append(structured_inference[index])

        # 删除 node_map 中的冗余
        new_node_map = {node_id: info for node_id, info in node_map.items() if node_id in save_node_set}

        # 删除citation_ids 中的冗余节点
        new_citation_ids = [node_id for node_id in citation_ids if node_id in save_node_set]

        return new_structured_inference, new_node_map, new_citation_ids
