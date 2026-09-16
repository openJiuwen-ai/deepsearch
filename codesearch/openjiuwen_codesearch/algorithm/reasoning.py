# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""一轮 LLM 决策：构造提示词 → 调主模型 → 返回规范化响应。

记忆每轮**重写进首条消息**（system prompt + issue + memory），不追加；
距轮次上限 warn_before_turns 轮时，向历史追加"必须提交"的系统警告。
"""


from typing import Optional
from openjiuwen_codesearch.algorithm.prompts import load_prompt
from openjiuwen_codesearch.llm.factory import ChatMessage, LLMClient, LLMResponse

TURN_LIMIT_WARNING = (
    "SYSTEM WARNING: You are about to run out of search turns. "
    "You MUST call `submit_final_snippets` now to conclude the task."
)


def build_base_prompt(
    query: str,
    topk: int,
    max_turns: int,
    issue_text: Optional[str] = None,
    past_queries: Optional[list[str]] = None,
) -> str:
    """system prompt（含 topk 约束）+ issue 正文。"""
    system_prompt = load_prompt("code_search").format(topk=topk, max_turns=max_turns)

    if issue_text:
        query_prompt = f"Original Issue:\n{issue_text}\n\nCurrent Sub-query / Focus:\n{query}"
    else:
        query_prompt = f"Issue:\n{query}"

    past_queries_str = "Past Queries:\n" + "\n".join(past_queries) + "\n\n" if past_queries else ""

    return system_prompt + "\n\n" + past_queries_str + query_prompt


def build_turn_messages(
    base_prompt: str, memory_text: str, history: list[ChatMessage]
) -> list[ChatMessage]:
    head = ChatMessage(role="user", content=base_prompt + "\n\n" + memory_text)
    return [head] + history


async def run_reasoning_turn(
    llm: LLMClient,
    base_prompt: str,
    memory_text: str,
    history: list[ChatMessage],
    tools: list[dict],
) -> LLMResponse:
    messages = build_turn_messages(base_prompt, memory_text, history)
    return await llm.invoke(messages, tools=tools)
