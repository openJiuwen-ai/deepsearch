# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
import uuid
from collections import defaultdict, deque
from pyvis.network import Network

from openjiuwen_deepsearch.algorithm.source_tracer_infer.html_template import (CLICK_SCRIPT,
                                                                               LEGEND_FORMAT,
                                                                               LEGEND_CONENT)
from openjiuwen_deepsearch.algorithm.source_tracer_infer.infer_call_model import GraphInfo
from openjiuwen_deepsearch.utils.common_utils.url_utils import validate_and_sanitize_url

logger = logging.getLogger(__name__)


class GenerateHTML:
    def __init__(self, language: str):
        self.language = language
        self.node_show_info = {}
        self.edge_show_info = {}
        self.reset_button_text = ""
        self.max_label_width = 20  # 最大单行字符数，超过则换行

    def _wrap_label(self, label: str) -> str:
        """
        将过长label按字符宽度换行显示。
        使用换行符，确保在图谱中正确显示。
        """
        if len(label) <= self.max_label_width:
            return label

        # 按设定宽度切分，优先在标点或空格处换行
        lines = []
        current_line = ""
        for char in label:
            current_line += char
            # 达到宽度限制，或者在标点符号处优先换行
            if len(current_line) >= self.max_label_width:
                # 尝试在标点符号处换行（更自然）
                break_chars = ['。', '，', '；', '：', '、', '？', '！', '.', ',', ';', ':', ' ', '\n']
                if char in break_chars or len(current_line) >= self.max_label_width + 5:
                    lines.append(current_line)
                    current_line = ""
                elif len(current_line) >= self.max_label_width * 1.5:
                    # 强制换行
                    lines.append(current_line)
                    current_line = ""

        if current_line:
            lines.append(current_line)

        return "\n".join(lines)

    @staticmethod
    def _estimate_node_width(label: str) -> float:
        """
        估算节点显示宽度（像素）。
        中文字符约15px，英文字符约10px，换行增加高度不影响宽度。
        """
        # 取最长行的宽度
        lines = label.split("\n") if "\n" in label else [label]
        max_line = max(lines, key=len)
        # 简化估算：假设主要是中文，每个字符约12px
        char_width = 12
        base_width = 40  # 基础节点宽度（圆形+padding）
        return base_width + len(max_line) * char_width

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
            self.reset_button_text = "Reset Layout"
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
            self.reset_button_text = "重置布局"

    def _calculate_layer_layout(self, node_map, citation_ids, conclusion_ids, structured_inference):
        """
        计算层级树状布局:
        - Layer 0 (top): Citation nodes (绿色引用节点)
        - Layer 1-N: Conclusion nodes (蓝色结论节点)
        - Layer N (bottom): Final conclusion nodes (红色最终结论)

        使用拓扑排序确定层级，然后根据节点label宽度动态调整间距。
        """
        # 构建图的邻接关系
        edges_from_node = defaultdict(list)  # node -> [outgoing targets]
        edges_to_node = defaultdict(list)    # node -> [incoming sources]
        all_nodes = set(node_map.keys())

        conclusion_set_idx = max(node_map.keys()) + 1
        intermediate_nodes = set()  # 汇聚节点

        # 从 structured_inference 构建边关系
        for head_id_list, _, tail_id in structured_inference:
            if head_id_list[0] in citation_ids:
                # 引用边
                for head_id in head_id_list:
                    edges_from_node[head_id].append(tail_id)
                    edges_to_node[tail_id].append(head_id)
            else:
                if len(head_id_list) > 1:
                    # 创建汇聚节点
                    intermediate_nodes.add(conclusion_set_idx)
                    for head_id in head_id_list:
                        edges_from_node[head_id].append(conclusion_set_idx)
                        edges_to_node[conclusion_set_idx].append(head_id)
                    edges_from_node[conclusion_set_idx].append(tail_id)
                    edges_to_node[tail_id].append(conclusion_set_idx)
                    conclusion_set_idx += 1
                else:
                    head_id = head_id_list[0]
                    edges_from_node[head_id].append(tail_id)
                    edges_to_node[tail_id].append(head_id)

        # 使用拓扑排序计算层级
        node_layers = {}
        # 引用节点作为 Layer 0
        for node_id in citation_ids:
            node_layers[node_id] = 0

        # BFS 计算其他节点层级
        visited = set(citation_ids)
        queue = deque()

        # 从引用节点开始 BFS
        for node_id in citation_ids:
            for target in edges_from_node[node_id]:
                if target not in visited:
                    queue.append(target)

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            # 层级 = 所有前置节点的最大层级 + 1
            max_parent_layer = -1
            for parent in edges_to_node[current]:
                if parent in node_layers:
                    max_parent_layer = max(max_parent_layer, node_layers[parent])

            if max_parent_layer >= 0:
                node_layers[current] = max_parent_layer + 1

            # 添加后继节点到队列
            for target in edges_from_node[current]:
                if target not in visited:
                    queue.append(target)

        # 最终结论节点放在最底层
        max_layer = max(node_layers.values()) if node_layers else 0
        for conclusion_id in conclusion_ids:
            if conclusion_id in node_layers:
                node_layers[conclusion_id] = max(node_layers[conclusion_id], max_layer + 1)
            else:
                node_layers[conclusion_id] = max_layer + 1

        # 计算每层节点数量，均匀分布
        layer_nodes = defaultdict(list)
        for node_id, layer in node_layers.items():
            layer_nodes[layer].append(node_id)

        # 添加未分层节点（孤点）到中间层
        for node_id in all_nodes:
            if node_id not in node_layers:
                mid_layer = max_layer // 2 if max_layer > 0 else 1
                node_layers[node_id] = mid_layer
                layer_nodes[mid_layer].append(node_id)

        # 计算位置坐标，根据节点宽度动态调整间距
        # 布局参数
        canvas_height = 800  # 画布高度
        canvas_width = 1400  # 画布宽度（增大以容纳更长label）
        min_spacing = 80  # 最小节点间距
        layer_height = canvas_height / (max(node_layers.values()) + 2) if node_layers else canvas_height / 3

        # 计算每个节点的显示宽度
        node_widths = {}
        for node_id, attrs in node_map.items():
            label = attrs.get("label", "")
            wrapped_label = self._wrap_label(label)
            node_widths[node_id] = self._estimate_node_width(wrapped_label)

        # 汇聚节点宽度（通常较小）
        for idx in range(max(node_map.keys()) + 1, conclusion_set_idx):
            node_widths[idx] = 50

        node_positions = {}
        for layer, nodes in layer_nodes.items():
            y = layer * layer_height + 50  # layer小（引用节点）在上（y小），layer大（最终结论）在下（y大）

            # 根据节点宽度动态分配位置，避免重叠
            if len(nodes) == 1:
                node_positions[nodes[0]] = (canvas_width / 2, y)
            else:
                # 计算该层总宽度需求
                total_width_needed = sum(node_widths.get(n, 60) for n in nodes)
                # 如果总宽度超过画布，需要扩展画布或压缩间距
                available_spacing = canvas_width - total_width_needed
                spacing_per_gap = available_spacing / (len(nodes) + 1)

                # 确保最小间距
                actual_spacing = max(spacing_per_gap, min_spacing)

                # 从左侧开始，逐个放置节点
                current_x = (canvas_width - (len(nodes) - 1) * actual_spacing - total_width_needed) / 2
                for node_id in nodes:
                    node_positions[node_id] = (current_x + node_widths.get(node_id, 60) / 2, y)
                    current_x += actual_spacing + node_widths.get(node_id, 60)

        return node_positions, intermediate_nodes, conclusion_set_idx

    def run(self, checked_infer_graph: GraphInfo) -> str:
        """根据推理图数据生成可交互的 HTML 可视化结果，使用层级树状布局。"""
        logger.info(f"[SOURCE TRACER INFER] generate_html starting...")
        if checked_infer_graph is None:
            logger.warning(f"[SOURCE TRACER INFER] checked_infer_graph is None, cannot generate HTML")
            raise ValueError("checked_infer_graph cannot be None")
        self._select_show_info(is_english=False)
        structured_inference = checked_infer_graph.structured_inference
        node_map = checked_infer_graph.node_map
        citation_ids = checked_infer_graph.citation_ids
        conclusion_ids = checked_infer_graph.conclusion_ids
        
        if not structured_inference or not node_map:
            logger.warning("[GenerateHTML] Empty graph detected, skip current inference.")
            raise ValueError("[GenerateHTML] Empty graph detected, skip current inference.")

        # 计算层级布局
        node_positions, intermediate_nodes, new_node_start_idx = self._calculate_layer_layout(
            node_map, citation_ids, conclusion_ids, structured_inference
        )

        # 创建有向图，使用预设布局
        net = Network(notebook=False, height="100vh", width="100%")

        # 配置网络选项：启用物理引擎进行初始稳定，然后禁用
        # 增大节点间距以适应换行label
        net.set_options("""
{
  "physics": {
    "enabled": true,
    "stabilization": {
      "enabled": true,
      "iterations": 300,
      "updateInterval": 25
    },
    "solver": "hierarchicalRepulsion",
    "hierarchicalRepulsion": {
      "nodeDistance": 180,
      "centralGravity": 0.0
    }
  },
  "layout": {
    "improvedLayout": false,
    "hierarchical": {
      "enabled": true,
      "direction": "UD",
      "sortMethod": "directed",
      "levelSeparation": 200,
      "nodeSpacing": 150
    }
  },
  "nodes": {
    "font": {
      "size": 12,
      "face": "sans-serif",
      "align": "center",
      "multi": true
    },
    "shape": "box",
    "borderWidth": 2,
    "shadow": true
  },
  "edges": {
    "smooth": {
      "enabled": true,
      "type": "curvedCW",
      "roundness": 0.5
    }
  },
  "interaction": {
    "dragNodes": true,
    "dragView": true,
    "zoomView": true
  }
}
""")

        # 添加节点并设置位置，应用label换行
        citation_node_index = 0
        for node_id, attrs in node_map.items():
            label = attrs.get("label", "")
            url = attrs.get("url", "")
            is_program_info = attrs.get("is_program_info", False)
            pos = node_positions.get(node_id, (600, 400))

            # 对label应用换行处理
            wrapped_label = self._wrap_label(label)

            if node_id in citation_ids:
                if is_program_info:
                    # 程序输出节点，可能也需要换行
                    net.add_node(node_id, label=self._wrap_label(label),
                                 color=self.node_show_info["programmer_node"].get("color", "#e1cef0"),
                                 size=15, x=pos[0], y=pos[1], shape="box",
                                 font={"multi": True, "align": "center"})
                    continue
                citation_node_index += 1
                # 验证URL scheme安全性，只允许http/https
                safe_url = validate_and_sanitize_url(url)
                if safe_url:
                    net.add_node(node_id, label=f"ref.{citation_node_index}", url=safe_url,
                                title='Click to navigate',
                                color=self.node_show_info["citation_node"].get("color", "#def0ce"),
                                size=15, x=pos[0], y=pos[1])
                else:
                    # URL不安全，添加不带链接的节点，并记录警告
                    logger.warning(
                        f"[GenerateHTML]: Unsafe URL scheme blocked for citation node {node_id}, "
                        f"url: {url[:100]}"
                    )
                    net.add_node(node_id, label=f"ref.{citation_node_index}",
                                title='Reference (link blocked)',
                                color=self.node_show_info["citation_node"].get("color", "#def0ce"),
                                size=15, x=pos[0], y=pos[1])
            else:
                net.add_node(node_id, label=wrapped_label,
                             color=self.node_show_info["conclusion_node"].get("color", "#d2e6f4"),
                             size=15, x=pos[0], y=pos[1], shape="box",
                             font={"multi": True, "align": "center"})

        # 添加边和汇聚节点
        conclusion_set_idx = new_node_start_idx
        for head_id_list, _, tail_id in structured_inference:
            # 引用边
            if head_id_list[0] in citation_ids:
                for head_id in head_id_list:
                    net.add_edge(head_id, tail_id,
                                 label=self.edge_show_info["citation_edge"].get("name", "引用"),
                                 arrows='to', font={'size': 12},
                                 color=self.edge_show_info["citation_edge"].get("color", "#BEBEBE"),
                                 smooth={'type': 'curvedCW', 'roundness': 0.3})
                continue

            # 结论集合汇聚节点
            if len(head_id_list) > 1:
                pos = node_positions.get(conclusion_set_idx, (600, 300))
                net.add_node(conclusion_set_idx, label="",
                             color=self.node_show_info["intermediate_node"].get("color", "#f6f6d2"),
                             size=15, x=pos[0], y=pos[1], shape="circle")
                for head_id in head_id_list:
                    net.add_edge(head_id, conclusion_set_idx,
                                 label=self.edge_show_info["combine_edge"].get("name", "汇总"),
                                 arrows='to', font={'size': 12},
                                 color=self.edge_show_info["combine_edge"].get("color", "#BEBEBE"),
                                 smooth={'type': 'curvedCW', 'roundness': 0.2})
                head_id = conclusion_set_idx
                conclusion_set_idx += 1
            else:
                head_id = head_id_list[0]

            net.add_edge(head_id, tail_id,
                         label=self.edge_show_info["infer_edge"].get("name", "推理"),
                         arrows='to', font={'size': 12},
                         color=self.edge_show_info["infer_edge"].get("color", "#BEBEBE"),
                         smooth={'type': 'curvedCW', 'roundness': 0.4})

        # 标记最终结论节点，保持box形状以支持换行
        for conclusion_id in conclusion_ids:
            if conclusion_id in net.node_ids:
                node = net.get_node(conclusion_id)
                node['color'] = self.node_show_info["final_conclusion_node"].get("color", "#f5c2c7")
                node['size'] = 20
                node['shape'] = 'box'
                node['font'] = {"multi": True, "align": "center", "size": 14}

        # 隐藏孤点
        node_ids = net.get_nodes()
        for node_id in node_ids:
            if len(net.neighbors(node_id)) == 0:
                net.get_node(node_id)['hidden'] = True

        # 生成 HTML
        html_content = net.generate_html()
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
            final_conclusion_node_name=self.node_show_info["final_conclusion_node"].get("name", "最终结论"),
            reset_button_text=self.reset_button_text
            )
        html_content = html_content.replace('</body>', f'{legend_content_format}</body>')
        return html_content
