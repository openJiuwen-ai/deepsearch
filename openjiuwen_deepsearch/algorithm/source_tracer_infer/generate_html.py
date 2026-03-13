# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import uuid
from pyvis.network import Network

from openjiuwen_deepsearch.algorithm.source_tracer_infer.html_template import (CLICK_SCRIPT, 
                                                                               LEGEND_FORMAT, 
                                                                               LEGEND_CONENT)
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import GraphInfo

logger = logging.getLogger(__name__)


class GenerateHTML:
    def __init__(self, language: str):
        self.language = language
        self.node_show_info = {}
        self.edge_show_info = {}
        
    def _select_show_info(self, is_english: bool):
        is_english = "en" in self.language or "english" in self.language or "英文" in self.language
        if is_english:
            self.node_show_info = {
                "programmer_node": {"color": "#e1cef0", "name": "Program Result"}, 
                "citation_node": {"color": "#def0ce", "name": "Reference"},
                "conclusion_node": {"color": "#d2e6f4", "name": "Interim Concl."},
                "intermediate_node": {"color": "#f6f6d2", "name": "Summary"},
                "final_conclusion_node": {"color": "#f5c2c7", "name": "Final Concl."}
                }
            self.edge_show_info = {
                "citation_edge": {"color": "#BEBEBE", "name": "refer"}, 
                "infer_edge": {"color": "#BEBEBE", "name": "infer"}, 
                "combine_edge": {"color": "#BEBEBE", "name": "summ"}
                }
        else:
            self.node_show_info = {
                "programmer_node": {"color": "#e1cef0", "name": "程序输出"}, 
                "citation_node": {"color": "#def0ce", "name": "参考文献"},
                "conclusion_node": {"color": "#d2e6f4", "name": "过程结论"},
                "intermediate_node": {"color": "#f6f6d2", "name": "汇总"},
                "final_conclusion_node": {"color": "#f5c2c7", "name": "最终结论"}
                }
            self.edge_show_info = {
                "citation_edge": {"color": "#BEBEBE", "name": "引用"}, 
                "infer_edge": {"color": "#BEBEBE", "name": "推理"}, 
                "combine_edge": {"color": "#BEBEBE", "name": "汇总"}
                }

    def run(self, checked_infer_graph: GraphInfo) -> str:
        logger.info(f"[SOURCE TRACER INFER] generate_html starting...")
        if checked_infer_graph is None:
            logger.warning(f"[SOURCE TRACER INFER] checked_infer_graph is None, cannot generate HTML")
            raise ValueError("checked_infer_graph cannot be None")
        self._select_show_info(is_english=False)
        structured_inference = checked_infer_graph.structured_inference
        node_map = checked_infer_graph.node_map
        citation_ids = checked_infer_graph.citation_ids
        conclusion_ids = checked_infer_graph.conclusion_ids
        # 创建有向图
        net = Network(notebook=False, height="100vh", width="100%")
        citation_node_index = 0
        for node_id, attrs in node_map.items():
            label = attrs.get("label", "")
            url = attrs.get("url", "")
            is_program_info = attrs.get("is_program_info", False)
            if node_id in citation_ids:
                if is_program_info:
                    net.add_node(node_id, label=label, 
                                 color=self.node_show_info["programmer_node"].get("color", "#e1cef0"),
                                size=15)  # programmer node 执行结果
                    continue
                citation_node_index += 1
                net.add_node(node_id, label=f"ref.{citation_node_index}", url=url, title='Click to navigate',
                            color=self.node_show_info["citation_node"].get("color", "#def0ce"), size=15)  # 引用节点
            else:
                net.add_node(node_id, label=label, 
                             color=self.node_show_info["conclusion_node"].get("color", "#d2e6f4"), size=15)  # 结论节点

        conclusion_set_idx = max(node_map.keys()) + 1
        for head_id_list, relation, tail_id in structured_inference:
            # 区分是结论还是引用
            # 引用节点
            if head_id_list[0] in citation_ids:
                for head_id in head_id_list:
                    net.add_edge(head_id, tail_id, label=self.edge_show_info["citation_edge"].get("name", "引用"), 
                                 arrows='to', font={'size': 12}, 
                                 color=self.edge_show_info["citation_edge"].get("color", "#BEBEBE"))
                continue
            # head如果是结论集合，先并归到一个节点
            if len(head_id_list) > 1:
                net.add_node(conclusion_set_idx, label="", 
                             color=self.node_show_info["intermediate_node"].get("color", "#f6f6d2"),
                            size=15)  # 结论集合节点
                for head_id in head_id_list:
                    net.add_edge(head_id, conclusion_set_idx, 
                                 label=self.edge_show_info["combine_edge"].get("name", "汇总"), 
                                 arrows='to', font={'size': 12},
                                color=self.edge_show_info["combine_edge"].get("color", "#BEBEBE"))
                head_id = conclusion_set_idx
                conclusion_set_idx += 1
            else:
                head_id = head_id_list[0]
            net.add_edge(head_id, tail_id, label=self.edge_show_info["infer_edge"].get("name", "推理"), 
                         arrows='to', font={'size': 12}, 
                         color=self.edge_show_info["infer_edge"].get("color", "#BEBEBE"))

        # 标记出最终结论节点
        for conclusion_id in conclusion_ids:
            net.get_node(conclusion_id)['color'] = self.node_show_info["final_conclusion_node"].get("color", "#f5c2c7")
            net.get_node(conclusion_id)['size'] = 20

        # 删除图中可能存在的孤点
        node_ids = net.get_nodes()
        for node_id in node_ids:
            if len(net.neighbors(node_id)) == 0:
                net.get_node(node_id)['hidden'] = True

        # 手动生成 HTML 内容并保存
        net.force_atlas_2based()  # 使用力导向布局
        html_content = net.generate_html()
        # 将脚本添加到生成的HTML中
        # 替换模板变量
        html_content = self._replace_template_variable(html_content)
        logger.info(f"[SOURCE TRACER INFER] generate_html end.")
        return html_content
    
    def _replace_template_variable(self, html_content: str):

        html_content = html_content.replace('</body>', f'<script>{CLICK_SCRIPT}</script></body>')
        # 添加legend脚本
        html_content = html_content.replace('</style>', f'{LEGEND_FORMAT}</style>')
        legend_content_format = LEGEND_CONENT.format(
            citation_node_color=self.node_show_info["citation_node"].get("color", "#def0ce"),
            citation_node_name=self.node_show_info["citation_node"].get("name", "参考文献"),
            conclusion_node_color=self.node_show_info["conclusion_node"].get("color", "#d2e6f4"),
            conclusion_node_name=self.node_show_info["conclusion_node"].get("name", "过程结论"),
            intermediate_node_color=self.node_show_info["intermediate_node"].get("color", "#f6f6d2"),
            intermediate_node_name=self.node_show_info["intermediate_node"].get("name", "汇总"),
            final_conclusion_node_color=self.node_show_info["final_conclusion_node"].get("color", "#f5c2c7"),
            final_conclusion_node_name=self.node_show_info["final_conclusion_node"].get("name", "最终结论")
            )
        html_content = html_content.replace('</body>', f'{legend_content_format}</body>')
        return html_content