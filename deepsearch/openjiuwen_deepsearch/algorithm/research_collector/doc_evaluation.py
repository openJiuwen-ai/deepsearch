# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import json

from typing import Annotated, Optional, List, Any
from pydantic import Field

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.utils.common_utils.llm_utils import normalize_json_output, ainvoke_llm_with_stats, \
    record_llm_retry_log
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def build_evaluator_messages(documents: List[dict]) -> List[dict]:
    """构造文档评估的短输入消息。

    Args:
        documents: evidence documents，不应包含或使用 original_content。

    Returns:
        user message 列表。
    """
    messages = []
    for idx, doc in enumerate(documents):
        compact_doc = {
            "source_id": doc.get("source_id", ""),
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "source": doc.get("source", ""),
            "key_passages": doc.get("key_passages", []),
            "publish_time": doc.get("publish_time", ""),
        }
        messages.append(dict(role="user", content=f"Document {idx}: {json.dumps(compact_doc, ensure_ascii=False)}\n"))
    return messages


async def run_doc_evaluation(
        query: Annotated[str, Field(description="Search query of current step")],
        documents: Annotated[List[dict] | None, Field(description="Compact evidence documents")] = None,
        llm: Annotated[Any, Field(description="llm of doc evaluation")] = None,
):
    """Post process the search result with compact evidence inputs.

    Args:
        query: (str) 当前检索 query。
        documents: (List[dict] | None) compact evidence documents，不应包含 original_content。
        llm: (Any) 文档评估使用的 LLM。

    Returns:
        List[dict]，评分结果列表。

    Raises:
        TypeError: documents 不是 compact evidence document 字典列表时抛出。
    """

    logger.info("[POST PROCESSING] Start content evaluation.")

    if documents is None:
        evaluator_documents = []
    elif not isinstance(documents, list) or not all(isinstance(doc, dict) for doc in documents):
        raise TypeError("documents must be a list of compact evidence document dicts.")
    else:
        evaluator_documents = documents
    scored_result_str = await info_evaluator(query, evaluator_documents, llm)
    scored_result = parse_evaluator_output(scored_result_str)

    if not isinstance(scored_result, list):
        scored_result = []

    output_scored_result = []
    for idx, scored in enumerate(scored_result):
        processed_item = process_scored_item(scored, idx, evaluator_documents)
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
            logger.error(f"[POST PROCESSING] Load Json Failed, error:{e}, scored_result_str: {scored_result_str}")
        return []


def process_scored_item(scored: dict, idx: int, documents: List[dict]) -> Optional[dict]:
    """Process each scored item.

    Args:
        scored: evaluator 返回的单条评分。
        idx: 当前评分项序号，用于日志定位。
        documents: compact evidence documents。

    Returns:
        处理后的评分项；索引无效时返回 None。
    """

    if not isinstance(scored, dict):
        if LogManager.is_sensitive():
            logger.error("[POST PROCESSING] Error processing scored item")
        else:
            logger.error(f"[POST PROCESSING] Error processing scored item: invalid item type | Index: {idx}")
        return None
    scored = scored.copy()

    try:
        scored = ensure_document_index_field(scored, idx)
        validate_document_index(scored, documents)
        log_content_and_scores(scored, documents)
        return scored
    except (KeyError, ValueError, IndexError) as e:
        if LogManager.is_sensitive():
            logger.error(f"[POST PROCESSING] Error processing scored item")
        else:
            logger.error(f"[POST PROCESSING] Error processing scored item: {e} | Item: {scored}")
        return None


def extract_scores(scored: dict) -> dict:
    """Extract scores from the scored dictionary."""

    if "score" in scored:
        score_val = scored.get('score', {})
        return score_val if isinstance(score_val, dict) else {}
    if "scores" in scored:
        scores_val = scored.get('scores', {})
        return scores_val if isinstance(scores_val, dict) else {}
    return {}


def ensure_document_index_field(scored: dict, idx: int) -> dict:
    """确保 evaluator 评分项包含规范的 document_index 字段。

    Args:
        scored: evaluator 返回的评分项。
        idx: 当前评分项序号；保留用于调用链上下文，不替代 document_index。

    Returns:
        补齐 document_index、scores、doc_time 后的评分项。

    Raises:
        KeyError: 缺少 document_index 或出现已废弃的 content 索引字段时抛出。
    """

    if 'content' in scored:
        raise KeyError("deprecated content field; use document_index instead")
    if 'document_index' not in scored:
        raise KeyError("document_index")
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


def validate_document_index(scored: dict, documents: List[dict]):
    """Validate the compact document index.

    Args:
        scored: evaluator 返回的评分项。
        documents: compact evidence documents。

    Raises:
        IndexError: document_index 超出 documents 范围时抛出。
        ValueError: document_index 无法转换为整数时抛出。
    """

    document_index = int(scored['document_index'])
    if document_index < 0 or document_index >= len(documents):
        raise IndexError(f"[POST PROCESSING] document_index {document_index} is out of range for documents.")


def _document_preview_for_log(document: dict | str) -> str:
    """生成 compact document 的日志预览。

    Args:
        document: compact evidence document 或旧字符串正文。

    Returns:
        用于日志的短文本预览。
    """
    if isinstance(document, dict):
        preview = " ".join(str(passage) for passage in document.get("key_passages", []))
        if not preview:
            preview = document.get("title", "")
    else:
        preview = str(document)
    return preview[:100] + "..." if len(preview) > 100 else preview


def log_content_and_scores(scored: dict, documents: List[dict]):
    """记录 compact document 和评分。

    Args:
        scored: evaluator 返回的评分项。
        documents: compact evidence documents。
    """

    document_index = int(scored['document_index'])
    truncated_content = _document_preview_for_log(documents[document_index])

    scores = extract_scores(scored)
    score_str = str(scores) if scores else "No valid score data"
    if not LogManager.is_sensitive():
        logger.info(f"[POST PROCESSING] Content: {truncated_content} | evaluation score: {score_str}")


async def info_evaluator(query: str, documents: List[dict], llm: Any):
    """评估 compact evidence documents。

    Args:
        query: 当前检索 query。
        documents: 短 evidence 文档列表。
        llm: 文档评估使用的 LLM。

    Returns:
        LLM 输出的 JSON 字符串；失败时返回空数组字符串。
    """

    context = {"query": query, "messages": build_evaluator_messages(documents)}
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
                llm, prompt, agent_name=AgentLlmName.DOC_EVALUATOR.value)
            return response
        except Exception as e:
            current_try = retry_idx + 1
            task_description = "getting info for evaluation"
            record_llm_retry_log(current_try, max_retries, error=e, operation=task_description)

    return None
