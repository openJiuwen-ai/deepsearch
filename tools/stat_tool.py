# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import argparse
import ast
import glob
import os
import re
from datetime import datetime

import pandas as pd

# -------------------------
# 命令行参数解析
# -------------------------
# --threads thread_id_1 thread_id_2
parser = argparse.ArgumentParser(description="解析 metrics.log 日志，导出csv")
parser.add_argument(
    "--threads", nargs="+", help="要筛选的一个或多个 thread_id", required=False
)
args = parser.parse_args()
# 给每个 thread_id 前加一个单引号
target_threads = ["'" + tid for tid in args.threads] if args.threads else None

# -------------------------
# 日志文件路径（绝对路径 + 支持轮转）
# -------------------------
project_root = os.path.dirname(os.path.dirname(__file__))  # 项目根目录
log_dir = os.path.join(project_root, "output/logs", "metrics")
log_pattern = os.path.join(log_dir, "metrics.log*")
log_files = glob.glob(log_pattern)


def sort_key(path):
    filename = os.path.basename(path)
    if filename == "metrics.log":
        return 0
    m = re.match(r"metrics\.log\.(\d+)", filename)
    if m:
        return int(m.group(1))
    return 0


# 日志文件排序，数字从大到小
log_files = sorted(log_files, key=sort_key, reverse=True)
print("解析的日志文件列表:", log_files)

# 拼接所有日志内容
content = ""
for file in log_files:
    with open(file, "r", encoding="utf-8") as f:
        content += f.read() + "\n"
lines = content.splitlines()

# -------------------------
# 逐行 Node 执行耗时日志解析
# -------------------------
pattern_node = (
    r"(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - \[TIME_STATS\] "
    r"thread_id: (?P<thread_id>[a-f0-9\-]+) -+ \[(?P<node>[A-Za-z0-9]+)\[(?P<section_idx>\d+)\]"
    r"\.(?P<method>[A-Za-z0-9_]+)\] executed time: (?P<duration>\d+\.\d+) s"
)

rows_node = []
for line in lines:
    m = re.search(pattern_node, line)
    if m:
        ts = datetime.strptime(m.group("timestamp"), "%Y-%m-%d %H:%M:%S,%f")
        thread_id = m.group("thread_id")
        section_idx = int(m.group("section_idx"))
        node = m.group("node")
        duration = float(m.group("duration"))
        rows_node.append({
            "Thread ID": thread_id,
            "Section Index": section_idx,
            "Node": node,
            "Timestamp": ts,
            "Duration (s)": duration
        })

df_node = pd.DataFrame(rows_node)
if not df_node.empty:
    df_node = df_node[df_node["Duration (s)"] > 0]

# 父节点规则
info_collector_child_nodes = [
    "GenerateQueryNode", "InfoRetrievalNode", "SupervisorNode",
    "SummaryNode", "ProgrammerNode", "GraphEndNode"
]
editor_team_nodes = [
    "InfoCollectorNode",
    "ResearchPlanReasoningNode",
    "SubReporterNode",
    "SubSourceTracerNode"
]


def compute_info_parent(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    uniq = set(df["Node"].unique())
    if "InfoCollectorNode" in uniq:
        return "InfoCollectorNode"
    return None


info_collector_parent_node_value = compute_info_parent(df_node)


def assign_parent_node_for_line(node: str) -> str:
    if node in editor_team_nodes:
        return "EditorTeamNode"
    elif node in info_collector_child_nodes:
        return info_collector_parent_node_value if info_collector_parent_node_value else node
    else:
        return "None"


if not df_node.empty:
    df_node["Parent Node"] = df_node["Node"].apply(assign_parent_node_for_line)
    # 统计并排序（逐行）
    stats_node = df_node.groupby(["Thread ID", "Node"])["Duration (s)"].agg(
        **{
            "Average Duration": lambda x: round(x.mean(), 2),
            "Call Count": "count"
        }
    ).reset_index()
    df_node = df_node.merge(stats_node, on=["Thread ID", "Node"], how="left")
    df_node["Duration (s)"] = df_node["Duration (s)"].round(3)

    # SectionIndex=0 优先： 仅逐行数据有Section Index
    df_node["SectionOrder"] = df_node["Section Index"].apply(lambda x: -1 if x == 0 else 1)
    df_node = df_node.sort_values(
        by=["Thread ID", "SectionOrder", "Parent Node", "Average Duration", "Duration (s)"],
        ascending=[True, True, True, False, False]
    ).drop(columns=["SectionOrder"])

    cols_node = [
        "Thread ID", "Parent Node", "Node", "Duration (s)", "Average Duration",
        "Section Index", "Call Count", "Timestamp"
    ]
    df_node = df_node[cols_node]
    df_node["Thread ID"] = "'" + df_node["Thread ID"]

# -------------------------
# LLM 调用统计块解析
# -------------------------
rows_llm = []
# 映射Method -> Node/Parent
mapping = {
    "collector_info_retrieval": ("InfoRetrievalNode", "InfoCollectorNode"),
    "collector_query_generation": ("GenerateQueryNode", "InfoCollectorNode"),
    "collector_summary": ("SummaryNode", "InfoCollectorNode"),
    "collector_supervisor": ("SupervisorNode", "InfoCollectorNode"),
    "doc_evaluator": ("InfoRetrievalNode", "InfoCollectorNode"),
    "entry": ("EntryNode", "None"),
    "outline": ("OutlineNode", "None"),
    "outline_agent": ("OutlineNode", "None"),
    "plan_reasoning": ("PlanReasoningNode", "EditorTeamNode"),
    "plan_reasoning_agent": ("PlanReasoningNode", "EditorTeamNode"),
    "reporter_abstract": ("ReporterNode", "None"),
    "reporter_conclusion": ("ReporterNode", "None"),
    "source_tracer_content_recognition": ("SubSourceTracerNode", "EditorTeamNode"),
    "source_tracer_extract_messages": ("SourceTracerNode", "None"),
    "source_tracer_source_matching": ("SourceTracerNode/SubSourceTracerNode", "None/EditorTeamNode"),
    "sub_reporter": ("SubReporterNode", "EditorTeamNode"),
    "sub_reporter_classify_doc_infos": ("SubReporterNode", "EditorTeamNode"),
    "sub_reporter_outline": ("SubReporterNode", "EditorTeamNode"),
}

# 识别“LLM 调用统计日志
pattern_llm = re.compile(
    r"^[0-9\-:, ]+ - \[TIME_STATS\] thread_id: (?P<thread_id>[a-f0-9\-]+).*LLM CALL STATISTICS\]: (?P<dict_str>\{.*\})"
)

for line in lines:
    m = pattern_llm.search(line)
    if m:
        thread_id = m.group("thread_id")
        dict_str = m.group("dict_str")
        try:
            stats_dict = ast.literal_eval(dict_str)
        except Exception as e:
            print(f"LLM解析失败 Thread {thread_id}: {e}")
            continue

        method_name = stats_dict.get("method_name")
        duration = round(stats_dict.get("duration", 0.0), 3)
        input_tokens = stats_dict.get("input_tokens")
        output_tokens = stats_dict.get("output_tokens")
        total_tokens = stats_dict.get("total_tokens")

        # 映射 Node/Parent
        node, parent = mapping.get(method_name, (None, None))

        rows_llm.append({
            "Thread ID": "'" + thread_id,
            "Method Name": method_name,
            "Node": node,
            "Parent Node": parent,
            "Duration (s)": duration,
            "Input Tokens": input_tokens,
            "Output Tokens": output_tokens,
            "Total Tokens": total_tokens,
        })

df_llm = pd.DataFrame(rows_llm)

# 统计并排序（LLM）
if not df_llm.empty:
    stats_llm = df_llm.groupby(["Thread ID", "Method Name"]).agg(
        **{
            "Call Count": ("Duration (s)", "count"),
            "Average Duration": ("Duration (s)", "mean"),
            "Avg Total Tokens": ("Total Tokens", "mean"),
            "Avg Input Tokens": ("Input Tokens", "mean"),
            "Avg Output Tokens": ("Output Tokens", "mean"),
        }
    ).reset_index()

    stats_llm["Average Duration"] = stats_llm["Average Duration"].round(2)
    stats_llm["Avg Total Tokens"] = stats_llm["Avg Total Tokens"].round(1)
    stats_llm["Avg Input Tokens"] = stats_llm["Avg Input Tokens"].round(1)
    stats_llm["Avg Output Tokens"] = stats_llm["Avg Output Tokens"].round(1)

    df_llm = df_llm.merge(stats_llm, on=["Thread ID", "Method Name"], how="left")


    def parent_order(val):
        if val is None or str(val).lower() == "none":
            return 0
        elif val == "EditorTeamNode":
            return 1
        else:
            return 2


    df_llm["ParentOrder"] = df_llm["Parent Node"].apply(parent_order)
    df_llm = df_llm.sort_values(
        by=["Thread ID", "ParentOrder", "Node", "Average Duration", "Duration (s)"],
        ascending=[True, True, True, False, False],
    ).drop(columns=["ParentOrder"])

    cols_llm = [
        "Thread ID", "Parent Node", "Node", "Method Name",
        "Duration (s)", "Input Tokens", "Output Tokens", "Total Tokens",
        "Call Count", "Average Duration",
        "Avg Total Tokens", "Avg Input Tokens", "Avg Output Tokens"
    ]
    df_llm = df_llm[cols_llm]

    # 处理Thread ID， 避免科学计数
    df_llm["Thread ID"] = "'" + df_llm["Thread ID"]

# -------------------------
# Search Tool 调用统计解析
# -------------------------
rows_search = []

pattern_search = re.compile(
    r"^[0-9\-:, ]+ - \[TIME_STATS\] thread_id: (?P<thread_id>[a-f0-9\-]+).*SEARCH TOOL STATISTICS\]: "
    r"(?P<dict_str>\{.*\})"
)

for line in lines:
    m = pattern_search.search(line)
    if m:
        thread_id = m.group("thread_id")
        dict_str = m.group("dict_str")
        try:
            entry = ast.literal_eval(dict_str)
        except Exception as e:
            print(f"Search解析失败 Thread {thread_id}: {e}")
            continue

        rows_search.append({
            "Thread ID": "'" + thread_id,
            "Function Name": entry.get("function_name"),
            "Search Engine": entry.get("search_engine"),
            "Query": entry.get("query"),
            "Duration (s)": round(entry.get("duration", 0.0), 3),
            "Res Count": entry.get("res_count"),
            "Res Lens": entry.get("res_len_list"),
        })

df_search = pd.DataFrame(rows_search)

# -------------------------
# 按 thread_id 筛选
# -------------------------
if target_threads:
    if not df_node.empty:
        df_node = df_node[df_node["Thread ID"].isin(target_threads)]
    if not df_llm.empty:
        df_llm = df_llm[df_llm["Thread ID"].isin(target_threads)]
    if not df_search.empty:
        df_search = df_search[df_search["Thread ID"].isin(target_threads)]

# -------------------------
# 导出 CSV
# -------------------------
script_dir = os.path.dirname(__file__)  # 脚本所在目录
if not df_node.empty:
    df_node.to_csv(os.path.join(script_dir, "stats_node_execution_table.csv"), index=False)
    print("已导出 stats_node_execution_table.csv")

if not df_llm.empty:
    df_llm.to_csv(os.path.join(script_dir, "stats_llm_invoke_table.csv"), index=False)
    print("已导出 stats_llm_invoke_table.csv")

if not df_search.empty:
    df_search.to_csv(os.path.join(script_dir, "stats_search_tool_table.csv"), index=False, encoding="utf-8-sig")
    print("已导出 stats_search_tool_table.csv")

# -------------------------
# 汇总表
# -------------------------
if not df_node.empty:
    node_summary = df_node[["Thread ID", "Parent Node", "Node", "Average Duration", "Call Count"]].drop_duplicates()

    print("\n=== 节点耗时汇总数据 ===")
    print(node_summary.to_string(index=False))
    node_summary.to_csv(os.path.join(script_dir, "stats_node_execution_summary.csv"), index=False)
    print("已导出 stats_node_execution_summary.csv")

if not df_llm.empty:
    llm_summary = df_llm[["Thread ID", "Parent Node", "Node", "Method Name", "Average Duration", "Avg Total Tokens",
                          "Call Count"]].drop_duplicates()

    print("\n=== llm调用耗时汇总数据 ===")
    print(llm_summary.to_string(index=False))
    llm_summary.to_csv(os.path.join(script_dir, "stats_llm_invoke_summary.csv"), index=False)
    print("已导出 stats_llm_invoke_summary.csv")

if not df_search.empty:
    total_calls = len(df_search)
    avg_duration = round(df_search["Duration (s)"].mean(), 3)
    print("\n=== Search Tool 调用汇总数据 ===")
    print(f"Total Call Count: {total_calls}")
    print(f"Avg Call Time (s): {avg_duration}")
