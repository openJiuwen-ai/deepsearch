# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict

import pandas as pd


@dataclass
class MergeCellsConfig:
    """合并单元格配置"""
    merge_col: str  # 合并依据的列
    num_cols: int  # 要合并的列数
    start_col: int  # 起始列索引
    merged_format: dict  # 合并后的格式


class OutlineToExcelExporter:
    """将Outline结构导出到Excel的工具类（包含单元格合并）"""

    def __init__(self, outline_data: Dict):
        self.outline = outline_data

    def extract_all_data(self) -> Dict[str, List[Dict]]:
        """
        提取所有层级的数据，包含合并信息

        Returns:
            按表格分类的数据字典
        """
        outline_data = {
            'outlines': [],
            'sections': [],
            'plans': [],
            'steps': [],
            'retrieval_query_docs': [],
            'doc_infos': [],
            'toc': []  # 新增TOC数据
        }

        raw_sections = copy.deepcopy(self.outline).get('sections', [])
        for section in raw_sections:
            section.pop('background_knowledge', None)
            section.pop('plans', None)

        # 0. 提取大纲信息
        outline_row = {
            'outline_id': self.outline.get('id', ''),
            'language': self.outline.get('language', ''),
            'outline_title': self.outline.get('title', ''),
            'outline_thought': self.outline.get('thought', ''),
            'section_count': len(self.outline.get('sections', [])),
            'sections': json.dumps(raw_sections, ensure_ascii=False, indent=2),
        }
        outline_data['outlines'].append(outline_row)

        # 1. 提取章节信息
        for section in self.outline.get('sections', []):
            parent_id_relationships = self.format_parent_relations(section.get('parent_ids', []),
                                                                   section.get('relationships', []))

            raw_plans = copy.deepcopy(section.get('plans', []))
            for plan in raw_plans:
                plan.pop('background_knowledge', None)
                steps = plan.get('steps', {})
                for step in steps:
                    step.pop('step_result', None)
                    step.pop('evaluation', None)
                    step.pop('background_knowledge', None)
                    step.pop('retrieval_queries', None)

            section_row = {
                'section_id': section.get('id', ''),
                'section_title': section.get('title', ''),
                'section_description': section.get('description', ''),
                'is_core_section': '⭐ 核心' if section.get('is_core_section') else '普通',
                'parent_sections': '\n'.join(parent_id_relationships),
                'plan_count': str(len(section.get('plans', []))),
                'plans': json.dumps(raw_plans, ensure_ascii=False, indent=2),
                'merge_section': f"{section.get('id', '')}"  # 仅用于内部合并，不导出
            }
            outline_data['sections'].append(section_row)

            section_base_info = {
                "title": section.get('title', ''),
                "description": section.get('description', ''),
                "parent_sections": '[' + '; '.join(parent_id_relationships) + ']'
            }

            # 2. 提取Plan信息
            for plan in section.get('plans', []):

                raw_steps = copy.deepcopy(plan.get('steps', {}))
                for step in raw_steps:
                    step.pop('background_knowledge', None)
                    queries = step.get('retrieval_queries', {})
                    for query in queries:
                        query.pop('doc_infos', None)

                plan_row = {
                    'section_id': section.get('id', ''),
                    'section_base_info': "\n\n".join(f"{key}: {value}" for key, value in section_base_info.items()),
                    'plan_id': plan.get('id', ''),
                    'plan_title': plan.get('title', ''),
                    'plan_thought': plan.get('thought', ''),
                    'is_research_completed': '✅ 完成' if plan.get('is_research_completed') else '⏳ 未完成',
                    'step_count': str(len(plan.get('steps', []))),
                    'plan_background_knowledge': json.dumps(plan.get('background_knowledge', {}), ensure_ascii=False,
                                                            indent=2),
                    'steps': json.dumps(raw_steps, ensure_ascii=False, indent=2),
                    'merge_section': f"{section.get('id', '')}",  # 仅用于内部合并，不导出
                    'merge_plan': f"{plan.get('id', '')}"  # 仅用于内部合并，不导出
                }
                outline_data['plans'].append(plan_row)

                plan_base_info = {
                    "title": plan.get('title', ''),
                    "thought": plan.get('thought', ''),
                    "background_knowledge": plan.get('background_knowledge', '')
                }

                # 3. 提取Step信息
                for step in plan.get('steps', []):
                    step_parent_id_relationships = self.format_parent_relations(step.get('parent_ids', []),
                                                                                step.get('relationships', []))

                    raw_queries = copy.deepcopy(step.get('retrieval_queries', []))
                    for query in raw_queries:
                        query.pop('doc_infos', None)

                    step_row = {
                        'section_id': section.get('id', ''),
                        'section_base_info': "\n\n".join(f"{key}: {value}" for key, value in section_base_info.items()),
                        'plan_id': plan.get('id', ''),
                        'plan_base_info': "\n\n".join(f"{key}: {value}" for key, value in plan_base_info.items()),
                        'step_id': step.get('id', ''),
                        'step_title': step.get('title', ''),
                        'step_description': step.get('description', ''),
                        'parent_steps': '\n'.join(step_parent_id_relationships),
                        'step_result': step.get('step_result', ''),
                        'evaluation': step.get('evaluation', ''),
                        'background_knowledge': '; '.join(step.get('background_knowledge', [])),
                        'query_count': str(len(step.get('retrieval_queries', []))),
                        'queries': json.dumps(raw_queries, ensure_ascii=False, indent=2),
                        'merge_section': f"{section.get('id', '')}",  # 仅用于内部合并，不导出
                        'merge_plan': f"{plan.get('id', '')}",  # 仅用于内部合并，不导出
                        'merge_step': f"{step.get('id', '')}"  # 仅用于内部合并，不导出
                    }
                    outline_data['steps'].append(step_row)

                    step_base_info = {
                        "title": plan.get('title', ''),
                        "thought": plan.get('thought', ''),
                        "background_knowledge": plan.get('background_knowledge', ''),
                        "parent_steps": '[' + '; '.join(step_parent_id_relationships) + ']'
                    }
                    step_result = {
                        "info_summaries": step.get('step_result', ''),
                        "evaluation": step.get('evaluation', '')
                    }

                    # 4. 提取RetrievalQuery信息
                    for query_idx, query in enumerate(step.get('retrieval_queries', []), 1):
                        query_info_count = f"{len(query.get('doc_infos', []))}"
                        query_row = {
                            'section_id': section.get('id', ''),
                            'section_base_info': "\n\n".join(
                                f"{key}: {value}" for key, value in section_base_info.items()),
                            'plan_id': plan.get('id', ''),
                            'plan_base_info': "\n\n".join(f"{key}: {value}" for key, value in plan_base_info.items()),
                            'step_id': step.get('id', ''),
                            'step_base_info': "\n\n".join(f"{key}: {value}" for key, value in step_base_info.items()),
                            'step_result': "\n\n".join(f"{key}: {value}" for key, value in step_result.items()),
                            'query_id': f"{step.get('id', '')}-{query_idx}",
                            'query_text': query.get('query', ''),
                            'query_info_count': query_info_count,
                            'doc_infos': json.dumps(query.get('doc_infos', []), ensure_ascii=False),
                        }

                        # 为每个doc_info创建单独的记录
                        for doc_info in query.get('doc_infos', []):
                            doc_scores = (f"doc_time: {doc_info.get('doc_time', '')}\n\n"
                                          f"source_authority: {doc_info.get('source_authority', '')}\n\n"
                                          f"task_relevance: {doc_info.get('task_relevance', '')}\n\n"
                                          f"information_richness: {doc_info.get('information_richness', '')}")
                            query_row_copy = copy.deepcopy(query_row)
                            query_row_copy['doc_title'] = doc_info.get('title', '')
                            query_row_copy['doc_url'] = doc_info.get('url', '')
                            query_row_copy['doc_core_content'] = doc_info.get('core_content', '')
                            query_row_copy['doc_scores'] = doc_scores
                            query_row_copy['merge_section'] = f"{section.get('id', '')}"  # 仅用于内部合并，不导出
                            query_row_copy['merge_plan'] = f"{plan.get('id', '')}"  # 仅用于内部合并，不导出
                            query_row_copy['merge_step'] = f"{step.get('id', '')}"  # 仅用于内部合并，不导出
                            query_row_copy['merge_query'] = f"{step.get('id', '')}-{query_idx}"  # 仅用于内部合并，不导出
                            outline_data['retrieval_query_docs'].append(query_row_copy)

            # 5. 提取TOC信息（包含查询层级）
            outline_data['toc'].extend(self._extract_toc_data(section))

        return outline_data

    def _extract_toc_data(self, section) -> List[Dict]:
        """提取TOC数据"""
        toc_data = []

        # 大纲行
        if section.get('id') == '1':
            toc_data.append({
                '层级': '大纲',
                'ID': self.outline.get('id', ''),
                '标题': self.outline.get('title', ''),
                '描述': self.outline.get('thought', ''),
                '状态': '',
                '数量': f"{len(self.outline.get('sections', []))}个章节"
            })

        # 章节行
        toc_data.append({
            '层级': '章节',
            'ID': section.get('id', ''),
            '标题': section.get('title', ''),
            '描述': section.get('description', ''),
            '状态': '⭐ 核心' if section.get('is_core_section') else '普通',
            '数量': f"{len(section.get('plans', []))}个计划"
        })

        # 计划行
        for plan in section.get('plans', []):
            toc_data.append({
                '层级': '  计划',
                'ID': plan.get('id', ''),
                '标题': plan.get('title', ''),
                '描述': plan.get('thought', ''),
                '状态': '✅ 完成' if plan.get('is_research_completed') else '⏳ 未完成',
                '数量': f"{len(plan.get('steps', []))}个步骤"
            })

            # 步骤行
            for step in plan.get('steps', []):
                toc_data.append({
                    '层级': '    步骤',
                    'ID': step.get('id', ''),
                    '标题': step.get('title', ''),
                    '描述': step.get('description', ''),  # 不截取字符串
                    '状态': '',
                    '数量': f"{len(step.get('retrieval_queries', []))}个查询"
                })

                # 查询行（新增）
                for query_idx, query in enumerate(step.get('retrieval_queries', []), 1):
                    toc_data.append({
                        '层级': '      查询',
                        'ID': f"{step.get('id', '')}-{query_idx}",
                        '标题': query.get('query', ''),
                        '描述': query.get('thought', ''),
                        '状态': '',
                        '数量': f"{len(query.get('doc_infos', []))}篇文档",
                    })

        return toc_data

    @staticmethod
    def format_parent_relations(parent_ids, relationships):
        """将 parent_id 和 relationship 配对，格式化为 "id( relationship )" 列表"""
        return [f"{pid}( {rel} )" for pid, rel in zip(parent_ids, relationships)]

    @staticmethod
    def create_dataframes(outline_data: Dict[str, List[Dict]]) -> Dict[str, pd.DataFrame]:
        """创建DataFrame并设置列顺序和中文列名"""
        dataframes = {}

        # 大纲表
        if outline_data['sections']:
            df_outline = pd.DataFrame(outline_data['outlines'])
            df_outline = df_outline[[
                'outline_id', 'language', 'outline_title', 'outline_thought', 'sections'
            ]]
            df_outline.columns = ['大纲ID', '语言', '大纲主题', '大纲生成思路', '大纲章节']
            dataframes['outlines'] = df_outline

        # 章节表
        if outline_data['sections']:
            df_sections = pd.DataFrame(outline_data['sections'])
            df_sections = df_sections[[
                'section_id', 'section_title', 'section_description', 'is_core_section',
                'parent_sections', 'plan_count', 'plans'
            ]]
            df_sections.columns = ['章节ID', '章节标题', '章节描述', '是否核心章节',
                                   '依赖章节', '计划数量', '计划详情']
            dataframes['sections'] = df_sections

        # 计划表（包含合并信息）
        if outline_data['plans']:
            df_plans = pd.DataFrame(outline_data['plans'])
            # 过滤掉合并标识列
            plan_columns = [col for col in df_plans.columns if not col.startswith('merge_')]
            df_plans = df_plans[plan_columns]
            df_plans = df_plans[[
                'section_id', 'section_base_info', 'plan_id', 'plan_title', 'plan_thought',
                'is_research_completed', 'step_count', 'plan_background_knowledge', 'steps'
            ]]
            df_plans.columns = ['章节ID', '章节基础信息', '计划ID', '计划标题', '计划生成思路',
                                '研究状态', '步骤数量', '计划背景知识', '计划步骤']
            dataframes['plans'] = df_plans

        # 步骤表（包含合并信息）
        if outline_data['steps']:
            df_steps = pd.DataFrame(outline_data['steps'])
            # 过滤掉合并标识列
            step_columns = [col for col in df_steps.columns if not col.startswith('merge_')]
            df_steps = df_steps[step_columns]
            df_steps = df_steps[[
                'section_id', 'section_base_info', 'plan_id', 'plan_base_info',
                'step_id', 'step_title', 'step_description',
                'parent_steps', 'background_knowledge', 'step_result',
                'evaluation', 'query_count', 'queries'
            ]]
            df_steps.columns = ['章节ID', '章节基础信息', '计划ID', '计划基础信息',
                                '步骤ID', '步骤标题', '步骤描述',
                                '依赖步骤', '步骤背景知识', '步骤结果',
                                '评估', '查询数量', '查询详情']
            dataframes['steps'] = df_steps

        # 查询表（包含合并信息）- 修正字段名
        if outline_data['retrieval_query_docs']:
            df_queries = pd.DataFrame(outline_data['retrieval_query_docs'])
            # 过滤掉合并标识列
            query_columns = [col for col in df_queries.columns if not col.startswith('merge_')]
            df_queries = df_queries[query_columns]
            # 重新排列列顺序
            df_queries = df_queries[[
                'section_id', 'section_base_info', 'plan_id', 'plan_base_info',
                'step_id', 'step_base_info', 'step_result',
                'query_id', 'query_text', 'query_info_count', 'doc_infos',
                'doc_title', 'doc_url', 'doc_core_content', 'doc_scores',
            ]]
            df_queries.columns = ['章节ID', '章节基础信息', '计划ID', '计划基础信息',
                                  '步骤ID', '步骤基础信息', '步骤结果',
                                  '查询ID', '查询文本', '查询结果文档数量', '查询结果',
                                  '文档标题', '文档URL', '文档内容', '文档评分',
                                  ]
            dataframes['retrieval_query_docs'] = df_queries

        # TOC表
        if outline_data['toc']:
            df_toc = pd.DataFrame(outline_data['toc'])
            dataframes['toc'] = df_toc

        return dataframes

    def export_to_excel(self, output_path: str = "outline_analysis.xlsx"):
        """
        导出到Excel文件，包含单元格合并

        Args:
            output_path: 输出文件路径
        """
        # 提取数据
        outline_data = self.extract_all_data()

        # 创建DataFrame
        dataframes = self.create_dataframes(outline_data)

        # 创建Excel写入器
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            workbook = writer.book

            # 定义格式
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'align': 'center',
                'valign': 'vcenter',
                'fg_color': '#D7E4BC',
                'border': 1
            })

            outline_format = workbook.add_format({
                'bold': True,
                'font_color': '#0563C1',  # 蓝色
                'border': 1,
                'text_wrap': True,
                'valign': 'vcenter',
                'top': 2,  # 上边框加粗
                'bottom': 1
            })

            core_format = workbook.add_format({
                'bold': True,
                'font_color': '#FF0000',
                'border': 1,
                'text_wrap': True,
                'valign': 'vcenter'
            })

            normal_format = workbook.add_format({
                'border': 1,
                'text_wrap': False,
                'valign': 'top'
            })

            completed_format = workbook.add_format({
                'font_color': '#00B050',
                'bold': True,
                'border': 1,
                'text_wrap': True,
                'valign': 'vcenter'
            })

            in_progress_format = workbook.add_format({
                'font_color': '#00B050',
                'bold': True,
                'border': 1,
                'text_wrap': True,
                'valign': 'vcenter'
            })

            step_format = workbook.add_format({
                'border': 1,
                'text_wrap': True,
                'valign': 'vcenter',
                'font_color': '#0070C0'
            })

            merged_format = workbook.add_format({
                'text_wrap': True,
                'valign': 'vcenter'
            })

            # 0. 写入大纲表
            if 'outlines' in dataframes:
                df_outline = dataframes['outlines']
                df_outline.to_excel(writer, sheet_name='大纲', index=False)
                worksheet = writer.sheets['大纲']

                # 设置列宽
                worksheet.set_column('A:A', 15)  # 大纲ID
                worksheet.set_column('B:B', 20)  # 语言
                worksheet.set_column('C:C', 30)  # 大纲主题
                worksheet.set_column('D:D', 50)  # 大纲生成思考
                worksheet.set_column('E:E', 50)  # 大纲章节

                # 设置标题格式
                for col_num, value in enumerate(df_outline.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                # 设置内容格式
                for row_num in range(len(df_outline)):
                    for col_num in range(len(df_outline.columns)):
                        worksheet.write(row_num + 1, col_num,
                                        df_outline.iloc[row_num, col_num],
                                        outline_format)

            # 1. 写入章节表
            if 'sections' in dataframes:
                df_sections = dataframes['sections']
                df_sections.to_excel(writer, sheet_name='章节', index=False)
                worksheet = writer.sheets['章节']

                # 设置列宽
                worksheet.set_column('A:A', 15)  # 章节ID
                worksheet.set_column('B:B', 30)  # 章节标题
                worksheet.set_column('C:C', 50)  # 章节描述
                worksheet.set_column('D:D', 15)  # 是否核心
                worksheet.set_column('E:E', 30)  # 依赖章节
                worksheet.set_column('F:F', 10)  # 计划数量
                worksheet.set_column('G:G', 60)  # 计划详情

                # 设置标题格式
                for col_num, value in enumerate(df_sections.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                # 设置内容格式
                for row_num in range(len(df_sections)):
                    is_core = df_sections.iloc[row_num]['是否核心章节'] == '⭐ 核心'
                    cell_format = core_format if is_core else normal_format
                    for col_num in range(len(df_sections.columns)):
                        worksheet.write(row_num + 1, col_num,
                                        df_sections.iloc[row_num, col_num],
                                        cell_format)

            # 2. 写入计划表（合并章节单元格）
            if 'plans' in dataframes:
                df_plans = dataframes['plans']
                # 获取合并信息
                df_plans_raw = pd.DataFrame(outline_data['plans'])
                df_plans.to_excel(writer, sheet_name='计划', index=False)
                worksheet = writer.sheets['计划']

                # 设置列宽
                worksheet.set_column('A:A', 15)  # 章节ID
                worksheet.set_column('B:B', 40)  # 章节信息
                worksheet.set_column('C:C', 15)  # 计划ID
                worksheet.set_column('D:D', 30)  # 计划标题
                worksheet.set_column('E:E', 50)  # 计划思路
                worksheet.set_column('F:F', 12)  # 研究状态
                worksheet.set_column('J:J', 10)  # 步骤数量
                worksheet.set_column('H:H', 40)  # 背景知识
                worksheet.set_column('I:I', 50)  # 计划步骤

                # 设置标题格式
                for col_num, value in enumerate(df_plans.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                # 合并章节单元格（前2列）
                section_config = MergeCellsConfig(
                    merge_col="merge_section",
                    num_cols=2,
                    start_col=0,
                    merged_format=merged_format
                )
                self._merge_section_cells(worksheet, df_plans_raw, section_config)

                # 设置内容格式
                for row_num in range(len(df_plans)):
                    status = df_plans.iloc[row_num]['研究状态']
                    cell_format = completed_format if status == '✅ 完成' else in_progress_format

                    # 从第3列开始应用格式（跳过被合并的列）
                    for col_num in range(2, len(df_plans.columns)):
                        worksheet.write(row_num + 1, col_num,
                                        df_plans.iloc[row_num, col_num],
                                        cell_format)

            # 3. 写入步骤表（合并章节和计划单元格）
            if 'steps' in dataframes:
                df_steps = dataframes['steps']
                # 获取合并信息
                df_steps_raw = pd.DataFrame(outline_data['steps'])
                df_steps.to_excel(writer, sheet_name='步骤', index=False)
                worksheet = writer.sheets['步骤']

                # 设置列宽
                worksheet.set_column('A:A', 15)  # 章节ID
                worksheet.set_column('B:B', 40)  # 章节信息
                worksheet.set_column('C:C', 15, cell_format=merged_format)  # 计划ID
                worksheet.set_column('D:D', 40, cell_format=merged_format)  # 计划信息
                worksheet.set_column('E:E', 15)  # 步骤ID
                worksheet.set_column('F:F', 30)  # 步骤标题
                worksheet.set_column('G:G', 50)  # 步骤描述
                worksheet.set_column('H:H', 30)  # 依赖步骤
                worksheet.set_column('I:I', 30)  # 背景知识
                worksheet.set_column('J:J', 50)  # 步骤结果
                worksheet.set_column('K:K', 30)  # 评估
                worksheet.set_column('L:L', 10)  # 查询数量
                worksheet.set_column('M:M', 60)  # 查询详情

                # 设置标题格式
                for col_num, value in enumerate(df_steps.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                # 合并章节单元格（前2列）
                section_config = MergeCellsConfig(
                    merge_col="merge_section",
                    num_cols=2,
                    start_col=0,
                    merged_format=merged_format
                )
                self._merge_section_cells(worksheet, df_steps_raw, section_config)

                # 合并计划单元格（接着的2列）
                plan_config = MergeCellsConfig(
                    merge_col="merge_plan",
                    num_cols=2,
                    start_col=2,
                    merged_format=merged_format
                )
                self._merge_plan_cells(worksheet, df_steps_raw, plan_config)

                # 设置内容格式
                for row_num in range(len(df_steps)):
                    for col_num in range(4, len(df_steps.columns)):
                        worksheet.write(row_num + 1, col_num,
                                        df_steps.iloc[row_num, col_num],
                                        step_format)

            # 4. 写入查询表（合并章节、计划、步骤单元格，并合并查询单元格）
            if 'retrieval_query_docs' in dataframes:
                df_queries = dataframes['retrieval_query_docs']
                # 获取合并信息
                df_queries_raw = pd.DataFrame(outline_data['retrieval_query_docs'])
                df_queries.to_excel(writer, sheet_name='查询', index=False)
                worksheet = writer.sheets['查询']

                # 设置列宽
                worksheet.set_column('A:A', 15)  # 章节ID
                worksheet.set_column('B:B', 40)  # 章节信息
                worksheet.set_column('C:C', 15)  # 计划ID
                worksheet.set_column('D:D', 40)  # 计划信息
                worksheet.set_column('E:E', 15)  # 步骤ID
                worksheet.set_column('F:F', 40)  # 步骤基础信息
                worksheet.set_column('G:G', 40)  # 步骤结果
                worksheet.set_column('H:H', 15)  # 查询ID
                worksheet.set_column('I:I', 50)  # 查询文本
                worksheet.set_column('J:J', 20)  # 查询结果文档数量
                worksheet.set_column('K:L', 50)  # 查询结果
                worksheet.set_column('L:L', 40)  # 文档标题
                worksheet.set_column('M:M', 50)  # 文档URL
                worksheet.set_column('N:N', 60)  # 文档内容
                worksheet.set_column('O:O', 40)  # 文档评分

                # 设置标题格式
                for col_num, value in enumerate(df_queries.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                # 合并章节单元格（前2列）
                section_config = MergeCellsConfig(
                    merge_col="merge_section",
                    num_cols=2,
                    start_col=0,
                    merged_format=merged_format
                )
                self._merge_section_cells(worksheet, df_queries_raw, section_config)

                # 合并计划单元格（接着的2列）
                plan_config = MergeCellsConfig(
                    merge_col="merge_plan",
                    num_cols=2,
                    start_col=2,
                    merged_format=merged_format
                )
                self._merge_plan_cells(worksheet, df_queries_raw, plan_config)

                # 合并步骤单元格（接着的3列）
                step_config = MergeCellsConfig(
                    merge_col="merge_step",
                    num_cols=3,
                    start_col=4,
                    merged_format=merged_format
                )
                self._merge_step_cells(worksheet, df_queries_raw, step_config)

                # 合并查询单元格（接着的3列：查询ID到查询结果文档数量）
                query_config = MergeCellsConfig(
                    merge_col="merge_query",
                    num_cols=4,
                    start_col=7,
                    merged_format=merged_format
                )
                self._merge_query_cells(worksheet, df_queries_raw, query_config)

                # 设置内容格式
                for row_num in range(len(df_queries)):
                    for col_num in range(10, len(df_queries.columns)):  # 从文档标题开始
                        worksheet.write(row_num + 1, col_num,
                                        df_queries.iloc[row_num, col_num],
                                        normal_format)

            # 5. 写入TOC表
            if 'toc' in dataframes:
                df_toc = dataframes['toc']
                df_toc.to_excel(writer, sheet_name='目录', index=False)
                worksheet = writer.sheets['目录']

                # 设置列宽
                worksheet.set_column('A:A', 15)  # 层级
                worksheet.set_column('B:B', 20)  # ID
                worksheet.set_column('C:C', 40)  # 标题
                worksheet.set_column('D:D', 60)  # 描述
                worksheet.set_column('E:E', 15)  # 状态
                worksheet.set_column('F:F', 15)  # 数量

                # 设置标题格式
                for col_num, value in enumerate(df_toc.columns.values):
                    worksheet.write(0, col_num, value, header_format)

                # 设置内容格式
                for row_num in range(len(df_toc)):
                    row_data = df_toc.iloc[row_num]

                    # 根据层级设置不同格式
                    level = row_data['层级'].strip()
                    if level == '大纲':
                        cell_format = outline_format
                    elif level == '章节':
                        cell_format = core_format
                    elif level == '计划':
                        cell_format = completed_format if '完成' in str(row_data['状态']) else in_progress_format
                    elif level == '步骤':
                        cell_format = workbook.add_format({
                            'border': 1,
                            'text_wrap': True,
                            'valign': 'top',
                            'font_color': '#0070C0'
                        })
                    else:  # 查询
                        cell_format = workbook.add_format({
                            'border': 1,
                            'text_wrap': True,
                            'valign': 'top',
                            'font_color': '#7030A0',
                            'italic': True
                        })

                    for col_num in range(len(df_toc.columns)):
                        worksheet.write(row_num + 1, col_num,
                                        row_data.iloc[col_num],
                                        cell_format)

            # 6. 创建汇总表
            self._create_summary_sheet(writer, outline_data, workbook)

        return output_path

    @staticmethod
    def _merge_section_cells(worksheet, df, config: MergeCellsConfig):
        """合并章节单元格"""
        current_value = None
        start_row = 1  # 从第2行开始（跳过标题行）
        count = 0

        merge_col = config.merge_col
        num_cols = config.num_cols
        merged_format = config.merged_format

        for row_num in range(len(df)):
            value = df.iloc[row_num][merge_col]

            if value != current_value:
                if count > 1:
                    # 合并单元格
                    for col in range(num_cols):
                        worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                              df.iloc[start_row - 1, col], merged_format)
                current_value = value
                start_row = row_num + 1
                count = 1
            else:
                count += 1

        # 处理最后一组
        if count > 1:
            for col in range(num_cols):
                worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                      df.iloc[start_row - 1, col], merged_format)

    @staticmethod
    def _merge_plan_cells(worksheet, df, config: MergeCellsConfig):
        """合并计划单元格"""
        current_value = None
        start_row = 1
        count = 0

        merge_col = config.merge_col
        num_cols = config.num_cols
        start_col = config.start_col
        merged_format = config.merged_format

        for row_num in range(len(df)):
            value = df.iloc[row_num][merge_col]

            if value != current_value:
                if count > 1:
                    # 合并单元格
                    for col_offset in range(num_cols):
                        col = start_col + col_offset
                        worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                              df.iloc[start_row - 1, col], merged_format)
                current_value = value
                start_row = row_num + 1
                count = 1
            else:
                count += 1

        # 处理最后一组
        if count > 1:
            for col_offset in range(num_cols):
                col = start_col + col_offset
                worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                      df.iloc[start_row - 1, col], merged_format)

    @staticmethod
    def _merge_step_cells(worksheet, df, config: MergeCellsConfig):
        """合并步骤单元格"""
        current_value = None
        start_row = 1
        count = 0

        merge_col = config.merge_col
        num_cols = config.num_cols
        start_col = config.start_col
        merged_format = config.merged_format

        for row_num in range(len(df)):
            value = df.iloc[row_num][merge_col]

            if value != current_value:
                if count > 1:
                    # 合并单元格
                    for col_offset in range(num_cols):
                        col = start_col + col_offset
                        worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                              df.iloc[start_row - 1, col], merged_format)
                current_value = value
                start_row = row_num + 1
                count = 1
            else:
                count += 1

        # 处理最后一组
        if count > 1:
            for col_offset in range(num_cols):
                col = start_col + col_offset
                worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                      df.iloc[start_row - 1, col], merged_format)

    @staticmethod
    def _merge_query_cells(worksheet, df, config: MergeCellsConfig):
        """合并查询单元格"""
        current_value = None
        start_row = 1
        count = 0

        merge_col = config.merge_col
        num_cols = config.num_cols
        start_col = config.start_col
        merged_format = config.merged_format

        for row_num in range(len(df)):
            value = df.iloc[row_num][merge_col]

            if value != current_value:
                if count > 1:
                    # 合并单元格
                    for col_offset in range(num_cols):
                        col = start_col + col_offset
                        worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                              df.iloc[start_row - 1, col], merged_format)
                current_value = value
                start_row = row_num + 1
                count = 1
            else:
                count += 1

        # 处理最后一组
        if count > 1:
            for col_offset in range(num_cols):
                col = start_col + col_offset
                worksheet.merge_range(start_row, col, start_row + count - 1, col,
                                      df.iloc[start_row - 1, col], merged_format)

    def _create_summary_sheet(self, writer, sections_data, workbook):
        """创建汇总表"""
        summary_data = []

        # 计算统计
        total_sections = len(sections_data['sections'])
        total_plans = len(sections_data['plans'])
        total_steps = len(sections_data['steps'])

        total_queries = 0
        for section in sections_data['sections']:
            for step in sections_data['steps']:
                if step['section_id'] == section['section_id']:
                    total_queries += int(step['query_count'])

        total_documents = len(sections_data['retrieval_query_docs'])
        core_sections = sum(1 for s in sections_data['sections'] if '⭐' in s['is_core_section'])

        # 添加汇总信息
        summary_data.append(['📊 大纲结构汇总统计', '', ''])
        summary_data.append(['生成时间', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), ''])
        summary_data.append(['大纲标题', self.outline.get('title', '无标题'), ''])
        summary_data.append(['', '', ''])

        summary_data.append(['📈 数量统计', '数值', '说明'])
        summary_data.append(['章节总数', str(total_sections), f'核心章节: {core_sections}'])
        summary_data.append(
            ['计划总数', str(total_plans),
             f'平均每个章节: {total_plans / total_sections:.1f}' if total_plans > 0 else '0'])
        summary_data.append(
            ['步骤总数', str(total_steps),
             f'平均每个计划: {total_steps / total_plans:.1f}' if total_plans > 0 else '0'])
        summary_data.append(
            ['查询总数', str(total_queries),
             f'平均每个步骤: {total_queries / total_steps:.1f}' if total_steps > 0 else '0'])
        summary_data.append(
            ['文档总数', str(total_documents),
             f'平均每个查询: {total_documents / total_queries:.1f}' if total_queries > 0 else '0'])
        summary_data.append(['', '', ''])

        # 添加章节详情
        summary_data.append(['📋 章节详情', '计划数', '步骤数', '查询数', '文档数'])
        for section in sections_data['sections']:
            section_title = f"{'⭐ ' if '⭐' in section['is_core_section'] else ''}{section['section_title']}"
            plan_count = section['plan_count']
            # 计算该章节下的步骤总数
            step_count = sum(int(p['step_count']) for p in sections_data['plans']
                             if p['section_id'] == section['section_id'])

            # 计算该章节下的查询总数
            query_count = sum(int(p['query_count']) for p in sections_data['steps']
                              if p['section_id'] == section['section_id'])

            # 计算该章节下的文档总数
            doc_count = sum(1 for p in sections_data['retrieval_query_docs']
                            if p['section_id'] == section['section_id'])

            summary_data.append([section_title, plan_count, str(step_count), str(query_count), str(doc_count)])

        # 创建DataFrame
        df_summary = pd.DataFrame(summary_data)
        df_summary.to_excel(writer, sheet_name='汇总', index=False, header=False)

        # 设置格式
        worksheet = writer.sheets['汇总']
        header_format = workbook.add_format({
            'bold': True,
            'font_size': 14,
            'font_color': '#1F497D',
            'border': 1
        })

        title_format = workbook.add_format({
            'bold': True,
            'font_size': 12,
            'font_color': '#C00000',
            'border': 1
        })

        normal_format = workbook.add_format({
            'border': 1,
            'text_wrap': True,
            'valign': 'vcenter'
        })

        # 应用格式
        for row_num, row in enumerate(summary_data):
            for col_num, value in enumerate(row):
                if row_num == 0:  # 标题
                    worksheet.write(row_num, col_num, value, header_format)
                elif row_num in [4, 10]:  # 小标题
                    worksheet.write(row_num, col_num, value, title_format)
                else:
                    worksheet.write(row_num, col_num, value, normal_format)

        # 设置列宽
        worksheet.set_column('A:A', 25)
        worksheet.set_column('B:B', 25)
        worksheet.set_column('C:C', 25)
        worksheet.set_column('D:D', 25)
        worksheet.set_column('E:E', 25)
