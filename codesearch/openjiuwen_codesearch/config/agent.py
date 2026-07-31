# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Literal

from pydantic import BaseModel


class SearchAgentConfig(BaseModel):
    # 引擎：graph = openjiuwen workflow 图形态（默认，SDK 亮点，Studio/Ops 可观测）；
    # react = 纯代码循环（openjiuwen 不可用时的兜底）；auto = graph 可用则 graph。
    engine: Literal["auto", "react", "graph"] = "auto"
    max_turns: int = 20
    warn_before_turns: int = 2        # 距上限 N 轮时注入"必须提交"警告
    # graph 引擎的 workflow 执行超时（openjiuwen 默认仅 60s，多轮检索必须放宽）
    time_limit_seconds: int = 900
    retrieve_topk: int = 20           # 最终返回的 snippet 数上限
    search_topk: int = 10             # 每次 search_codebase 检索条数
    filter_concurrency: int = 8       # 过滤 agent 并发上限（semaphore）
    stagnation_rounds: int = 3        # 连续 N 个含检索的轮次无新增 → STAGNATED
    strict_trigram: bool = True       # trigram 检索后按真实子串包含过滤
    trace_dir: str = "agent_logs"     # 轨迹 jsonl 目录；空串关闭轨迹
