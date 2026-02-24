# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import json

from typing import Annotated, Optional, List, Any
from pydantic import Field

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.utils.common_utils.llm_utils import normalize_json_output, ainvoke_llm_with_stats, \
    record_llm_retry_log
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


async def run_doc_evaluation(
        query: Annotated[str, Field(description="Search query of current step")],
        contents: Annotated[List[str], Field(description="Lists of contents from external sources")],
        llm: Annotated[Any, Field(description="llm of doc evaluation")],
):
    """Post process the search result."""

    logger.info("[POST PROCESSING] Start content evaluation.")

    scored_result_str = await info_evaluator(query, contents, llm)
    scored_result = parse_evaluator_output(scored_result_str)

    if not isinstance(scored_result, list):
        scored_result = []

    output_scored_result = []
    for idx, scored in enumerate(scored_result):
        processed_item = process_scored_item(scored, idx, contents)
        if processed_item:
            output_scored_result.append(processed_item)

    logger.info("[POST PROCESSING] Process finish.")
    return output_scored_result


def parse_evaluator_output(scored_result_str: str) -> List[dict]:
    """Parse the output of the info evaluator."""

    try:
        return json.loads(normalize_json_output(scored_result_str))
    except json.JSONDecodeError as e:
        if LogManager.is_sensitive():
            logger.error(f"[POST PROCESSING] Load Json Failed")
        else:
            logger.error(f"[POST PROCESSING] Load Json Failed, error:{e}.")
        return []


def process_scored_item(scored: dict, idx: int, contents: List[str]) -> Optional[dict]:
    """Process each scored item."""

    if not isinstance(scored, dict):
        scored = {'content': str(idx), 'doc_time': "Unknown", 'scores': {}}
        return scored

    original_scored = scored.copy()
    scored = ensure_content_field(scored, idx)
    try:
        validate_content_index(scored, contents)
        log_content_and_scores(scored, contents)
        return scored
    except (KeyError, ValueError, IndexError) as e:
        if LogManager.is_sensitive():
            logger.error(f"[POST PROCESSING] Error processing scored item")
        else:
            logger.error(f"[POST PROCESSING] Error processing scored item: {e} | Item: {scored}")
        return original_scored


def extract_scores(scored: dict) -> dict:
    """Extract scores from the scored dictionary."""

    if "score" in scored:
        score_val = scored.get('score', {})
        return score_val if isinstance(score_val, dict) else {}
    if "scores" in scored:
        scores_val = scored.get('scores', {})
        return scores_val if isinstance(scores_val, dict) else {}
    return {}


def ensure_content_field(scored: dict, idx: int) -> dict:
    """Ensure that 'content' field exists in the scored dictionary."""

    if 'content' not in scored:
        scored['content'] = str(idx)
    if "scores" not in scored:
        if "score" in scored:
            scored["scores"] = scored["score"] if isinstance(scored["score"], dict) else {}
            del scored["score"]
        else:
            scored["scores"] = {}
    if not isinstance(scored["scores"], dict):
        scored["scores"] = {}
    if "doc_time" not in scored:
        scored["doc_time"] = "Unknown"
    return scored


def validate_content_index(scored: dict, contents: List[str]):
    """Validate the content index."""

    content_idx = int(scored['content'])
    if content_idx < 0 or content_idx >= len(contents):
        raise IndexError(f"[POST PROCESSING] content index {content_idx} is out of range for contents.")


def log_content_and_scores(scored: dict, contents: List[str]):
    """log the content and its scores."""

    content_idx = int(scored['content'])
    content = contents[content_idx]
    truncated_content = content[:100] + "..." if len(content) > 100 else content

    scores = extract_scores(scored)
    score_str = str(scores) if scores else "No valid score data"
    if not LogManager.is_sensitive():
        logger.info(f"[POST PROCESSING] Content: {truncated_content} | evaluation score: {score_str}")


async def info_evaluator(query: str, contents: List[str], llm: Any):
    """Evaluate the information."""

    context = {"query": query, "messages": []}

    for idx, content in enumerate(contents):
        context["messages"].append(
            dict(role="user", content=f"Content {idx}: {content}\n"))
    prompts = apply_system_prompt("info_evaluator_doc", context)
    try:
        response = await invoke_llm_with_retry(prompts, llm)
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error(f"[POST PROCESSING] Failed to evaluate doc. ")
        else:
            logger.error(f"[POST PROCESSING] Failed to evaluate doc. {e}")
        return "[]"

    if response is None:
        return "[]"
    return response.get("content", "")


async def invoke_llm_with_retry(prompt: list, llm: Any, max_retries=5):
    """Invoke LLM with retry mechanism."""
    for retry_idx in range(max_retries):
        try:
            response = await ainvoke_llm_with_stats(
                llm, prompt, agent_name=NodeId.DOC_EVALUATOR.value)
            return response
        except Exception as e:
            current_try = retry_idx + 1
            task_description = "getting info for evaluation"
            record_llm_retry_log(current_try, max_retries, error=e, operation=task_description)

    return None
