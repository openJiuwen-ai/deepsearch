# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.algorithm.report.compact_doc_info import (
    build_coverage_passage_block,
)
from openjiuwen_deepsearch.algorithm.report.report_common import (
    CONTENT_DATE_TIMELINESS_WEIGHT,
    EFFECT_SUB_REPORT_TAG,
    EXTRACT_BATCH_SIZE,
    FULLTEXT_TOP_K_PER_RATIONALE,
    MAX_CONCURRENT_BATCHES,
    MAX_EXTRACT_DOC_CHARS,
    MAX_RATIONALE_DESC_LEN,
    SELECTION_COVERAGE_FLOOR,
)
from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    _COVERAGE_MAX_CHARS_PER_DOC,
    _COVERAGE_MAX_TOTAL_CHARS,
    _COVERAGE_TOP_K_CAP,
    _coverage_fact_anchor_keys,
    exclude_passages,
    extract_coverage_passages,
    extract_key_passages,
    normalize_content_for_dedup,
    outline_summary_text,
)
from openjiuwen_deepsearch.algorithm.report.report_rationale_fulltext import (
    _format_reference_link,
    enrich_fulltext_for_section,
    get_required_document_content,
)
from openjiuwen_deepsearch.algorithm.report.report_utils import export_outline_without_plans
from openjiuwen_deepsearch.algorithm.report.retry_feedback import _append_retry_feedback_message
from openjiuwen_deepsearch.algorithm.research_collector.target_paper import find_exact_target_paper_facts
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    TemporalScope,
    _resolve_content_date_scope,
    build_section_local_contract_prompt_context,
)
from openjiuwen_deepsearch.utils.common_utils.date_utils import (
    classify_temporal,
    parse_content_window,
    timeliness_score,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import ainvoke_llm_with_stats, normalize_json_output, safe_float
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)


@dataclass
class PassageSelectionContext:
    """Encapsulates passage selection intermediate results for debug export."""
    rationales: list
    coverage_result: dict
    passages: list
    selected_passages: list


@dataclass
class TemporalSelectionOptions:
    """Optional temporal weighting inputs for rationale-coverage selection.

    ``temporal_scope`` accepts a ``TemporalScope`` model or a serialized dict
    (invalid values degrade to no weighting). The selection entry point only
    passes the intent's ``content_date_scope``, so the constraint type is
    guaranteed by the caller; this dataclass does not re-check it — a
    ``source_date`` scope passed directly would be weighted the same way.
    ``timeliness_weight`` of ``0.0`` keeps pure-coverage behavior.
    """
    temporal_scope: TemporalScope | dict | None = None
    timeliness_weight: float = 0.0


def ensure_exact_target_documents(
    selected_docs: list[dict], candidate_docs: list[dict], target_papers: list[dict] | None,
) -> list[dict]:
    """Keep exact user-targeted papers in a subsection once they are available as evidence."""
    result = list(selected_docs)
    required_docs = []
    selected_keys = {
        (str(doc.get("source_id") or ""), str(doc.get("url") or ""))
        for doc in result
    }
    for candidate in candidate_docs:
        if not isinstance(candidate, dict) or not find_exact_target_paper_facts(target_papers, [candidate]):
            continue
        key = (str(candidate.get("source_id") or ""), str(candidate.get("url") or ""))
        if key not in selected_keys:
            required_docs.append(candidate)
            selected_keys.add(key)
    return required_docs + result


def _normalize_rationales(
    rationales: list, max_rationales: int = 15
) -> list:
    """Post-process LLM-generated rationales: truncate descriptions, enforce quantity limits.

    - Truncate overlong descriptions to MAX_RATIONALE_DESC_LEN.
    - If rationales exceed max_rationales, keep all primary first, then truncate supplementary.
    - Renumber IDs sequentially (r1, r2, ...) after truncation.

    Args:
        rationales: Raw rationale list from LLM output.
        max_rationales: Hard upper bound on returned rationales.

    Returns:
        Normalized rationale list.
    """
    if not rationales:
        return []

    # Filter out non-dict elements so downstream callers can safely use .get()
    rationales = [r for r in rationales if isinstance(r, dict)]
    if not rationales:
        return []

    # Truncate overlong descriptions
    for r in rationales:
        desc = r.get("description", "")
        if not isinstance(desc, str):
            desc = str(desc) if desc is not None else ""
        r["description"] = desc[:MAX_RATIONALE_DESC_LEN] if len(desc) > MAX_RATIONALE_DESC_LEN else desc

    # Enforce quantity limit: keep all primary, truncate supplementary
    if len(rationales) > max_rationales:
        primary = [r for r in rationales if r.get("priority") == "primary"]
        supplementary = [r for r in rationales if r.get("priority") != "primary"]
        kept = primary[:max_rationales]
        remaining = max_rationales - len(kept)
        if remaining > 0:
            kept.extend(supplementary[:remaining])
        rationales = kept
        logger.warning(
            "[generate_rationales] truncated rationales from %s to %s "
            "(primary kept, supplementary truncated)",
            len(primary) + len(supplementary), len(rationales),
        )

    # Renumber IDs sequentially
    for idx, r in enumerate(rationales):
        r["id"] = f"r{idx + 1}"

    return rationales


class EvidenceMixin:
    """Evidence extraction and scoring mixin.

    Dependencies (provided by Reporter or other mixins):
        - self._llm: LLM instance (from Reporter.__init__)
        - self.strip_leading_number: (from MarkdownProcessorMixin)
    """

    async def _generate_section_rationales(self, current_inputs: dict) -> tuple[list, str]:
        """Generate section information dimensions (rationales).

        Inspired by METEORA: LLM generates rationales from section context +
        step_result + evaluation, grounded on actually collected information
        to ensure the coverage matrix evaluation is meaningful.

        Args:
            current_inputs: context containing section info and step_summaries.

        Returns:
            (rationale list, last_error). On success the error string is "";
            after retry exhaustion the list is [] and last_error carries the
            final failure detail. Each retry appends the previous failure as a
            data-bounded retry_feedback user message after the system prompt.
        """
        section_idx = current_inputs.get("section_idx", 1)
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        section_description = current_inputs.get("section_description", "")
        report_task = current_inputs.get("report_task", "")
        overall_outline = export_outline_without_plans(
            current_inputs.get("current_outline", {})
        )
        # Expand section_local_contract (nested dict) into top-level fields via the shared helper,
        # consistent with other prompt sites (report.py, sub_section_outline.py).
        contract_ctx = build_section_local_contract_prompt_context(
            current_inputs.get("section_local_contract")
        )
        section_focus = contract_ctx.get("section_focus", "")
        focus_dimensions = contract_ctx.get("allowed_dimensions", [])
        step_summaries = current_inputs.get("step_summaries", [])

        step_summaries_text = "\n".join(
            f"  - Step {s.get('plan_idx', '')}-{s.get('step_idx', '')}: {s.get('title', '')}\n"
            f"    Description: {s.get('description', '')}\n"
            f"    Collected: {s.get('step_result', '')}\n"
            f"    Evaluation: {s.get('evaluation', '')}"
            for s in step_summaries
        ) if step_summaries else "  No step summaries available."

        focus_dimensions_text = ", ".join(focus_dimensions) if focus_dimensions else "None specified"

        # Build user message with data (including untrusted step summaries)
        # separated from system prompt to prevent prompt injection.
        user_content = (
            f"Report task: {report_task}\n"
            f"Overall outline: {overall_outline}\n\n"
            f"Chapter title: {section_task}\n"
            f"Chapter description: {section_description}\n"
            f"Chapter focus: {section_focus}\n"
            f"Focus dimensions: {focus_dimensions_text}\n"
            f"Research step summaries:\n{step_summaries_text}\n\n"
            "Generate rationales for this chapter."
        )
        tmp_context = {
            "messages": [dict(role="user", content=user_content)],
        }

        max_retries = current_inputs.get("max_generate_retry_num", 3)
        last_error = None
        retry_feedback = ""
        for attempt_num in range(max_retries):
            llm_input = apply_system_prompt("rationale_generator", tmp_context)
            _append_retry_feedback_message(llm_input, retry_feedback)
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_RATIONALE_GENERATOR.value,
                )
            except Exception as e:
                last_error = f"LLM call failed: {e}"
                retry_feedback = (
                    "LLM call failed" if LogManager.is_sensitive() else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            if not llm_output or not llm_output.get("content"):
                last_error = "LLM returned empty content"
                retry_feedback = (last_error or "")[:500]
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            try:
                data = json.loads(normalize_json_output(llm_output.get("content", "")))
                if not isinstance(data, dict):
                    raise ValueError(f"LLM output is not a JSON object, got {type(data).__name__}")
                rationales = data.get("rationales", [])
                if not isinstance(rationales, list):
                    raise ValueError(f"'rationales' is not a list, got {type(rationales).__name__}")
                # Post-process: truncate overlong descriptions and enforce quantity limits
                rationales = _normalize_rationales(rationales, max_rationales=15)
                primary_count = sum(1 for r in rationales if r.get("priority") == "primary")
                supplementary_count = len(rationales) - primary_count
                logger.info(
                    "%s [generate_rationales] section_idx: [%s] generated %s rationales "
                    "(primary: %s, supplementary: %s) (attempt %s/%s)",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    len(rationales), primary_count, supplementary_count,
                    attempt_num + 1, max_retries,
                )
                return rationales, ""
            except Exception as e:
                last_error = f"failed to parse LLM output: {e}"
                retry_feedback = (
                    "failed to parse LLM output"
                    if LogManager.is_sensitive()
                    else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [generate_rationales] section_idx: [%s] attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

        logger.error(
            "%s [generate_rationales] section_idx: [%s] failed after %s attempts: %s",
            EFFECT_SUB_REPORT_TAG, section_idx,
            max_retries, last_error,
        )
        return [], (last_error or "unknown rationale error")

    @staticmethod
    async def _gather_with_limit(tasks: list, limit: int) -> list:
        """Run async tasks with a concurrency limit.

        Args:
            tasks: list of coroutines.
            limit: maximum number of concurrent tasks.

        Returns:
            List of results in the same order as tasks.
        """
        if not tasks:
            return []
        semaphore = asyncio.Semaphore(limit)

        async def _run_with_sem(task):
            async with semaphore:
                return await task

        return await asyncio.gather(*[_run_with_sem(t) for t in tasks])

    async def _extract_and_score_documents(
        self, current_inputs: dict, raw_passages: list, rationales: list
    ) -> tuple[dict, str]:
        """Extract relevant passages from documents and score rationale coverage.

        Replaces COINS chunking + ngram filter + coverage matrix with a single
        LLM-based extractive summarization + scoring step. The LLM sees full
        document context (not isolated passages) and extracts verbatim
        passages relevant to any rationale, preserving precise numbers/tables.

        Flow: batch documents (EXTRACT_BATCH_SIZE per batch) → parallel LLM
        extract+score → merge into coverage_result compatible format.

        Args:
            current_inputs: context dict.
            raw_passages: original passage list (passage-level, not chunked).
            rationales: rationale list with id/description/type.

        Returns:
            (result_dict, last_error). result_dict format is compatible with
            _select_by_rationale_coverage:
            - filtered_passages: extracted passages (passage-level dicts with
              doc_url/doc_title/passage_text/source/publish_time/doc_time)
            - coverage_matrix: {passage_N: {rationale_id: score}}
            - dimension_scores: {passage_N: {rationale_id: {coverage, reliability, data_density}}}.
              reliability/data_density are document-level values mirrored into
              every rationale entry (assessed once per passage, not per rationale)
            On total failure, degrades to original docs as passages (each
            truncated to the first 500 chars) with empty coverage_matrix and
            carries the combined error.
        """
        section_idx = current_inputs.get("section_idx", 1)
        section_task = self.strip_leading_number(current_inputs.get("section_task", ""))
        section_description = current_inputs.get("section_description", "")

        if not raw_passages or not rationales:
            logger.warning(
                f"{EFFECT_SUB_REPORT_TAG} [extract_score] section_idx: [{section_idx}] "
                f"empty passages ({len(raw_passages)}) or rationales ({len(rationales)})"
            )
            return {}, ""

        rationales_text = "\n".join(
            f"  {r.get('id', '')}: {r.get('description', '')} (type: {r.get('type', 'unknown')})"
            for r in rationales
        )

        batches = [
            raw_passages[i:i + EXTRACT_BATCH_SIZE]
            for i in range(0, len(raw_passages), EXTRACT_BATCH_SIZE)
        ]

        logger.info(
            "%s [extract_score] section_idx: [%s] split %s passages into %s batch(es), "
            "batch_size=%s, %s rationales",
            EFFECT_SUB_REPORT_TAG, section_idx, len(raw_passages),
            len(batches), EXTRACT_BATCH_SIZE, len(rationales),
        )

        section_ctx = {
            "section_task": section_task,
            "section_description": section_description,
            "section_idx": section_idx,
            "max_retries": current_inputs.get("max_generate_retry_num", 3),
            "extract_content_time": (
                _resolve_content_date_scope(
                    current_inputs.get("research_intent")
                ) is not None
            ),
        }

        tasks = [
            self._extract_batch(batch, batch_idx, rationales_text, section_ctx)
            for batch_idx, batch in enumerate(batches)
        ]
        batch_results = await self._gather_with_limit(tasks, MAX_CONCURRENT_BATCHES)

        # Merge batch results into coverage_result-compatible format
        filtered_passages: list = []
        coverage_matrix: dict = {}
        dimension_scores: dict = {}
        global_passage_idx = 0
        all_errors: list = []

        for batch_idx, (data, batch_docs, error) in enumerate(batch_results):
            if error:
                all_errors.append(error)
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s failed: %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx, error[:200],
                )
                continue
            if not data:
                continue

            documents = data.get("documents", [])
            if not isinstance(documents, list):
                documents = []
            for doc_result in documents:
                if not isinstance(doc_result, dict):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s doc_result is not a dict (type=%s), skipping",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                        type(doc_result).__name__,
                    )
                    continue
                passage_index = doc_result.get("doc_index", doc_result.get("passage_index"))
                if not isinstance(passage_index, int):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s doc missing "
                        "doc_index/passage_index, skipping to avoid misattribution",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    )
                    continue
                if passage_index < 0 or passage_index >= len(batch_docs):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s doc_index=%s "
                        "out of range (batch size %s), skipping",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                        passage_index, len(batch_docs),
                    )
                    continue
                parent_doc = batch_docs[passage_index]

                passages = doc_result.get("passages", [])
                if not isinstance(passages, list):
                    logger.warning(
                        "%s [extract_score] section_idx: [%s] batch %s "
                        "doc_index=%s passages is not a list "
                        "(type=%s), skipping",
                        EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                        passage_index, type(passages).__name__,
                    )
                    continue
                for passage in passages:
                    if not isinstance(passage, dict):
                        continue
                    text = passage.get("text", "")
                    if not text or not str(text).strip():
                        continue

                    passage_key = f"passage_{global_passage_idx}"
                    passage_dict = {
                        "doc_url": parent_doc.get("url", "") or parent_doc.get("doc_url", ""),
                        "doc_title": parent_doc.get("title", "") or parent_doc.get("doc_title", ""),
                        "doc_time": parent_doc.get("doc_time", ""),
                        "publish_time": parent_doc.get("publish_time", ""),
                        "source": parent_doc.get("source", ""),
                        "passage_text": str(text),
                        "original_content": parent_doc.get("original_content", ""),
                        "content_time": (
                            passage.get("content_time")
                            if section_ctx.get("extract_content_time") else None
                        ),
                    }
                    filtered_passages.append(passage_dict)

                    scores = passage.get("scores", {})
                    # New format: passage-level {"reliability", "data_density"} +
                    # per-rationale {"r1": {"coverage": 0.9}}.
                    # coverage_matrix stores coverage directly (used for top-k ranking);
                    # dimension_scores stores {coverage, reliability, data_density} per rationale.
                    passage_reliability = safe_float(
                        passage.get("reliability", 0.0)
                    )
                    passage_data_density = safe_float(
                        passage.get("data_density", 0.0)
                    )
                    cleaned = {}
                    dim_cleaned = {}
                    if isinstance(scores, dict):
                        for rid, dim_scores in scores.items():
                            if isinstance(dim_scores, dict):
                                c = safe_float(
                                    dim_scores.get("coverage", 0.0)
                                )
                                r = safe_float(
                                    dim_scores.get("reliability"),
                                    passage_reliability,
                                )
                                d = safe_float(
                                    dim_scores.get("data_density"),
                                    passage_data_density,
                                )
                                cleaned[str(rid)] = c
                                dim_cleaned[str(rid)] = {
                                    "coverage": c, "reliability": r,
                                    "data_density": d,
                                }
                            else:
                                # bool 是 int 子类但非合法分数，需显式排除
                                if isinstance(dim_scores, bool) or not isinstance(dim_scores, (int, float, str)):
                                    logger.warning(
                                        "%s [extract_score] section_idx: [%s] "
                                        "Unexpected score type for rationale "
                                        "%s: %s. Treating as 0.0.",
                                        EFFECT_SUB_REPORT_TAG, section_idx,
                                        rid, type(dim_scores).__name__,
                                    )
                                    c = 0.0
                                else:
                                    c = safe_float(dim_scores)
                                cleaned[str(rid)] = c
                                dim_cleaned[str(rid)] = {"coverage": c}
                    coverage_matrix[passage_key] = cleaned
                    dimension_scores[passage_key] = dim_cleaned
                    # Document-level dimensions are assessed once per passage,
                    # not per rationale, and stored at the top level for
                    # visualization selection.
                    passage_dict["reliability"] = passage_reliability
                    passage_dict["data_density"] = passage_data_density
                    # Write per-rationale scores back to passage_dict so that
                    # build_classified_content can attach them to the citation
                    # block, enabling the writing LLM to use coverage scores
                    # for passage prioritization as declared in the prompt.
                    passage_dict["scores"] = dim_cleaned
                    global_passage_idx += 1

        logger.info(
            "%s [extract_score] section_idx: [%s] merged %s passages from %s passages, "
            "%s batch(es) failed",
            EFFECT_SUB_REPORT_TAG, section_idx, len(filtered_passages),
            len(raw_passages), len(all_errors),
        )

        # Degraded path: all batches failed or no valid passages extracted →
        # use original docs as passages, truncated to the first 500 chars
        # (bounded for the writing LLM).
        if not filtered_passages:
            logger.warning(
                "%s [extract_score] section_idx: [%s] all batches failed or no "
                "valid passages extracted, degrading to original docs as passages",
                EFFECT_SUB_REPORT_TAG, section_idx,
            )
            for passage in raw_passages:
                content = str(passage.get("original_content", "") or "")
                if not content.strip():
                    continue
                passage_dict = {
                    "doc_url": passage.get("url", "") or passage.get("doc_url", ""),
                    "doc_title": passage.get("title", "") or passage.get("doc_title", ""),
                    "doc_time": passage.get("doc_time", ""),
                    "publish_time": passage.get("publish_time", ""),
                    "source": passage.get("source", ""),
                    "passage_text": content[:500],
                    "original_content": content,
                    "reliability": 0.0,
                    "data_density": 0.0,
                    "content_time": None,
                }
                filtered_passages.append(passage_dict)

            return {
                "filtered_passages": filtered_passages,
                "coverage_matrix": {},
                "dimension_scores": {},
            }, "; ".join(all_errors)[:500]

        return {
            "filtered_passages": filtered_passages,
            "coverage_matrix": coverage_matrix,
            "dimension_scores": dimension_scores,
        }, ""

    async def _extract_batch(
        self, batch_docs: list, batch_idx: int,
        rationales_text: str, section_ctx: dict,
    ) -> tuple:
        """Extract passages and score rationales for a single batch (1 LLM call).

        Args:
            batch_docs: list of original documents in this batch (passage-level).
            batch_idx: batch index (for logging).
            rationales_text: rationale text.
            section_ctx: dict with section_task, section_description, section_idx.

        Returns:
            (parsed_result_dict, batch_docs, last_error) tuple. On success the
            error string is ""; on failure parsed_result is an empty dict and
            last_error carries the final failure detail. Each retry appends the
            previous failure as a data-bounded retry_feedback user message.
        """
        section_task = section_ctx.get("section_task", "")
        section_description = section_ctx.get("section_description", "")
        section_idx = section_ctx.get("section_idx", -1)

        # Build document text for LLM input (untrusted data in user message)
        doc_parts = []
        for i, passage in enumerate(batch_docs):
            title = passage.get("title", "") or passage.get("doc_title", "")
            url = passage.get("url", "") or passage.get("doc_url", "")
            content = str(passage.get("original_content", "") or passage.get("passage_text", "") or "")
            if len(content) > MAX_EXTRACT_DOC_CHARS:
                content = content[:MAX_EXTRACT_DOC_CHARS]
            doc_parts.append(
                f"Document {i}:\nTitle: {title}\nURL: {url}\n"
                f"publish_time: {passage.get('publish_time', '')}\n"
                f"Content: {content}"
            )
        docs_text = "\n\n".join(doc_parts)

        user_content = (
            f"Chapter title: {section_task}\n"
            f"Chapter description: {section_description}\n\n"
            f"Information dimensions (rationales):\n{rationales_text}\n\n"
            f"Documents:\n{docs_text}\n\n"
            "Extract relevant passages from the documents above and score "
            "rationale coverage. Output ONLY a JSON object."
        )
        tmp_context = {
            "messages": [dict(role="user", content=user_content)],
            "extract_content_time": section_ctx.get("extract_content_time", False),
        }

        max_retries = section_ctx.get("max_retries", 3)
        last_error = None
        retry_feedback = ""
        for attempt_num in range(max_retries):
            llm_input = apply_system_prompt("passages_extractor", tmp_context)
            _append_retry_feedback_message(llm_input, retry_feedback)
            try:
                llm_output = await ainvoke_llm_with_stats(
                    llm=self._llm,
                    messages=llm_input,
                    agent_name=AgentLlmName.SUB_REPORTER_PASSAGES_EXTRACTOR.value,
                )
            except Exception as e:
                last_error = f"LLM call failed: {e}"
                retry_feedback = (
                    "LLM call failed" if LogManager.is_sensitive() else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            if not llm_output or not llm_output.get("content"):
                last_error = "LLM returned empty content"
                retry_feedback = (last_error or "")[:500]
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

            try:
                data = json.loads(normalize_json_output(llm_output.get("content", "")))
                if not isinstance(data, dict):
                    raise ValueError(f"LLM output is not a JSON object, got {type(data).__name__}")
                documents = data.get("documents", [])
                if not isinstance(documents, list):
                    documents = []
                n_docs = len(documents)
                n_passages = sum(
                    len(d.get("passages") or []) for d in documents
                    if isinstance(d, dict) and isinstance(d.get("passages"), list)
                )
                logger.info(
                    "%s [extract_score] section_idx: [%s] batch %s: parsed %s docs, %s passages (attempt %s/%s)",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    n_docs, n_passages, attempt_num + 1, max_retries,
                )
                return data, batch_docs, ""
            except Exception as e:
                last_error = f"failed to parse LLM output: {e}"
                retry_feedback = (
                    "failed to parse LLM output"
                    if LogManager.is_sensitive()
                    else (last_error or "")[:500]
                )
                logger.warning(
                    "%s [extract_score] section_idx: [%s] batch %s: attempt %s/%s %s",
                    EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
                    attempt_num + 1, max_retries, last_error,
                )
                continue

        logger.error(
            "%s [extract_score] section_idx: [%s] batch %s: failed after %s attempts: %s",
            EFFECT_SUB_REPORT_TAG, section_idx, batch_idx,
            max_retries, last_error,
        )
        return {}, batch_docs, (last_error or "unknown extraction error")

    @staticmethod
    def _select_by_rationale_coverage(
        passages: list, rationales: list, coverage_result: dict, top_k: int = 10,
        temporal: TemporalSelectionOptions | None = None,
    ) -> tuple:
        """Per-rationale top-k passage selection (0 LLM calls).

        For each rationale, sort passages by coverage score and take top-k.
        Deduplicate across rationales by passage identity (keep first occurrence).

        The keep-gate requires a passage's max raw coverage across rationales
        to reach SELECTION_COVERAGE_FLOOR (aligned with the downstream Layer-1
        filter in enrich_fulltext_for_section), so selected passages always
        survive the downstream coverage filter: temporal promotion can never
        be undone there, and a doomed sub-floor promotion cannot evict a
        floor-passing passage from top-k. Fallback: when the whole pool is
        below the floor, the legacy ``score > 0`` gate applies (mirrors
        Layer-1's own fallback).

        When ``temporal.temporal_scope`` is set and
        ``temporal.timeliness_weight > 0``, the *sort* key becomes
        ``coverage + timeliness_weight * temporal_score`` so time-compliant
        passages can outrank slightly-higher-coverage non-compliant ones. The
        keep-gate, however, is applied to the *raw* coverage only so temporal
        penalties never silently hard-delete a covered passage.
        Additionally, per rationale the pure-coverage top-k members that
        temporal promotion evicted are restored into the pool (union-restore),
        but only unknown-tier ones (timeliness 0, no date evidence): the
        innocent are protected, while violation/partial passages keep the
        demotion they earned. Restores are capped so each rationale receives
        at most FULLTEXT_TOP_K_PER_RATIONALE new passages in total, so for
        top_k <= that bound Layer-2's raw-coverage top-15 truncation cannot
        fire on per-rationale new deliveries (with the default top_k == 15,
        a saturated rationale gets no restores at all — restores beyond the
        bound would be delivered-then-truncated by Layer 2 anyway).
        Cross-rationale shared passages can still push a Layer-2 rationale
        list past the bound in extreme cases (accepted second-order edge).
        ``temporal is None`` keeps pure-coverage behavior. Constraint-type
        gating lives at the entry point, which resolves only the intent's
        ``content_date_scope`` into ``TemporalSelectionOptions``; this
        function does not check ``constraint_type`` itself, so any other
        scope type passed directly is weighted too.

        Args:
            passages: candidate passage list (already n-gram filtered).
            rationales: rationale list.
            coverage_result: coverage matrix evaluation result.
            top_k: maximum passages per rationale.
            temporal: optional ``TemporalSelectionOptions`` bundling the
                temporal scope (model or serialized dict; the entry point
                only passes ``content_date`` scopes, but any scope passed
                directly is weighted) and the timeliness weight.
                ``None`` (default) keeps pure-coverage behavior.

        Returns:
            (selected_passages, selected_passage_keys) tuple.
        """
        # Note: reliability and data_density are assessed per-passage but are NOT
        # used for rationale-based selection. Selection is driven by coverage scores
        # only. reliability/data_density are preserved on passage dicts for
        # downstream visualization selection and prompt enrichment.
        filtered_passages = coverage_result.get("filtered_passages", passages)
        coverage_matrix = coverage_result.get("coverage_matrix", {})

        rationale_ids = list(dict.fromkeys(
            r.get("id", "") for r in rationales if isinstance(r, dict)
        ))

        # Max raw coverage across rationales per passage, for the floor-aligned
        # keep-gate (see docstring). Computed once; the per-rationale loops reuse it.
        max_cov_by_idx: dict[int, float] = {}
        for idx in range(len(filtered_passages)):
            passage_cov = coverage_matrix.get(f"passage_{idx}", {})
            if not isinstance(passage_cov, dict):
                passage_cov = {}
            max_cov_by_idx[idx] = max(
                (safe_float(passage_cov.get(rid, 0.0)) for rid in rationale_ids),
                default=0.0,
            )
        any_above_floor = any(v >= SELECTION_COVERAGE_FLOOR for v in max_cov_by_idx.values())

        def _gate_ok(idx: int) -> bool:
            # 全池低于门槛时退回老门（score > 0），避免整章无证据。
            return not any_above_floor or max_cov_by_idx.get(idx, 0.0) >= SELECTION_COVERAGE_FLOOR

        temporal_scope = temporal.temporal_scope if temporal else None
        timeliness_weight = temporal.timeliness_weight if temporal else 0.0

        # Normalize a serialized dict temporal_scope into a TemporalScope model so
        # downstream helpers can use attribute access. None / invalid -> None.
        if isinstance(temporal_scope, dict):
            try:
                temporal_scope = TemporalScope.model_validate(temporal_scope)
            except Exception:
                temporal_scope = None

        use_temporal = (
            temporal_scope is not None
            and timeliness_weight > 0
        )

        # Pre-compute per-passage temporal score once (the entry point only
        # passes content_date scopes; constraint_type is not re-checked here).
        # The sort key adds the weighted temporal score; the keep-gate below stays on
        # raw coverage so a compliant-but-low passage can still be dropped and a
        # covered-but-non-compliant passage is never hard-deleted by time alone.
        # Four-tier counts are computed here so the sort key and the observability
        # log share one source of truth; effective_weight scales the base weight by
        # the candidate pool's known-date ratio (low signal -> auto exit, instead
        # of ranking on noise and squeezing coverage).
        temporal_scores: dict[int, float] = {}
        tier_counts = {"compliant": 0, "partial": 0, "violation": 0, "unknown": 0}
        known_ratio = 0.0
        effective_weight = 0.0
        if use_temporal:
            for idx, p in enumerate(filtered_passages):
                status = classify_temporal(
                    parse_content_window(p.get("content_time")), temporal_scope
                )
                temporal_scores[idx] = timeliness_score(status)
                tier_counts[status] = tier_counts.get(status, 0) + 1
            total = len(filtered_passages)
            known = total - tier_counts["unknown"]
            known_ratio = (known / total) if total else 0.0
            effective_weight = timeliness_weight * known_ratio

        def _sort_key(item) -> float:
            # item == (raw_coverage, idx); gate uses raw_coverage, sort uses weighted.
            score, idx = item
            t = temporal_scores.get(idx, 0.0) if use_temporal else 0.0
            return score + effective_weight * t

        # Track selected passages by identity to deduplicate
        seen_ids: set[int] = set()
        selected_passages: list = []
        selected_indices: list[int] = []
        restored_count = 0
        baseline_seen_ids: set[int] = set()  # virtual pure-coverage pool (union-restore)

        for rid in rationale_ids:
            # Sort passages by coverage score for this rationale (descending)
            scored = []
            for idx in range(len(filtered_passages)):
                passage_key = f"passage_{idx}"
                passage_cov = coverage_matrix.get(passage_key, {})
                if not isinstance(passage_cov, dict):
                    passage_cov = {}
                score = safe_float(passage_cov.get(rid, 0.0))
                scored.append((score, idx))

            # Sort by weighted score descending (raw coverage when not temporal).
            scored.sort(key=_sort_key, reverse=True)

            # Take top-k for this rationale, dedup across rationales. The keep-gate
            # uses the raw coverage (the tuple's first element) so temporal
            # penalties never hard-delete a passage that actually covers a rationale.
            count = 0
            for score, idx in scored:
                if count >= top_k:
                    break
                if score > 0 and _gate_ok(idx):  # 0 分门同 Layer 2；_gate_ok 对齐 Layer 1 的 0.15 门槛
                    passage = filtered_passages[idx]
                    if id(passage) not in seen_ids:
                        seen_ids.add(id(passage))
                        selected_passages.append(passage)
                        selected_indices.append(idx)
                        count += 1

            # Union-restore (content_date weighting only): re-add pure-coverage
            # top-k members that temporal promotion evicted for this rationale,
            # but ONLY unknown-tier ones (no date evidence, timeliness 0) — the
            # "没日期不罚" principle. Violation/partial passages earned their
            # demotion (timeliness < 0) and stay out: the soft filter protects
            # the innocent without diluting the penalty on proven non-compliant
            # content — additive for the undated, still zero-tolerance-by-demotion
            # for the dated-out-of-range.
            if use_temporal:
                # Replay the pure-coverage baseline with its own dedup trajectory
                # (baseline_seen_ids): per rationale, take top_k additions in
                # coverage order exactly like the main loop would without temporal
                # (same floor gate included). Baseline picks missing from the real
                # pool were evicted by weighting — restore the unknown-tier ones.
                # Tie-break by idx matches the baseline's stable sort on
                # idx-ordered `scored`. Restores are capped so this rationale's
                # total deliveries stay <= FULLTEXT_TOP_K_PER_RATIONALE, keeping
                # Layer-2's raw-coverage top-15 truncation a no-op; iteration is
                # coverage-descending, so the cap drops the weakest restores.
                restore_cap = max(0, FULLTEXT_TOP_K_PER_RATIONALE - count)
                restored_for_rid = 0
                cov_sorted = sorted(scored, key=lambda item: (-item[0], item[1]))
                cov_count = 0
                for score, idx in cov_sorted:
                    if cov_count >= top_k:
                        break
                    if score > 0 and _gate_ok(idx):
                        passage = filtered_passages[idx]
                        if id(passage) in baseline_seen_ids:
                            continue
                        baseline_seen_ids.add(id(passage))
                        cov_count += 1
                        if (restored_for_rid < restore_cap
                                and id(passage) not in seen_ids
                                and temporal_scores.get(idx, 0.0) == 0.0):
                            seen_ids.add(id(passage))
                            selected_passages.append(passage)
                            selected_indices.append(idx)
                            restored_count += 1
                            restored_for_rid += 1

        logger.info(
            "%s [select_by_rationale] selected %s passages from %s candidates "
            "for %s rationales (top_k=%s per rationale)",
            EFFECT_SUB_REPORT_TAG, len(selected_passages), len(filtered_passages),
            len(rationale_ids), top_k,
        )

        if use_temporal:
            # Coverage distribution across all passages × rationales.
            cov_values: list[float] = []
            for pv in coverage_matrix.values():
                if not isinstance(pv, dict):
                    continue
                for rid in rationale_ids:
                    cov_values.append(safe_float(pv.get(rid, 0.0)))
            if cov_values:
                cov_min = min(cov_values)
                cov_max = max(cov_values)
                cov_mean = sum(cov_values) / len(cov_values)
            else:
                cov_min = cov_max = cov_mean = 0.0
            logger.info(
                "%s [select_by_rationale] temporal weighting on: "
                "tiers=%s, coverage min/max/mean=%.3f/%.3f/%.3f, "
                "weight=%s, effective_weight=%.4f, known_ratio=%.3f, candidates=%s, "
                "restored=%s",
                EFFECT_SUB_REPORT_TAG, tier_counts,
                cov_min, cov_max, cov_mean,
                timeliness_weight, effective_weight, known_ratio, len(filtered_passages),
                restored_count,
            )

        # Keys are the indices into `filtered_passages` (i.e. coverage_matrix
        # keys), not indices into the selected subset, so downstream lookups
        # into coverage_matrix/dimension_scores stay aligned.
        return (
            selected_passages,
            [f"passage_{idx}" for idx in selected_indices],
        )


    @staticmethod
    def _write_doc_selection_debug(
        current_inputs: dict, ctx: PassageSelectionContext,
    ) -> None:
        """Pack doc-selection intermediate results into current_inputs.

        Stores debug data in current_inputs["doc_selection_debug"] so the caller
        (SubReporterNode → editor_team_manager_node._update_state) can write it
        back to Section.doc_selection_debug for ResultExporter to dump to JSON/Excel.
        """
        rationales = ctx.rationales
        coverage_result = ctx.coverage_result
        passages = ctx.passages
        selected_passages = ctx.selected_passages

        filtered_passages = coverage_result.get("filtered_passages", passages)
        doc_info_map = {
            f"passage_{i}": {
                "doc_title": d.get("doc_title", ""),
                "doc_url": d.get("doc_url", ""),
                "passage_text": (d.get("passage_text", "") or ""),
            }
            for i, d in enumerate(filtered_passages)
        }
        id_to_key = {id(d): f"passage_{i}" for i, d in enumerate(filtered_passages)}
        selected_summary = [
            {
                "passage_key": id_to_key.get(id(passage), ""),
                "doc_title": passage.get("doc_title", ""),
                "doc_url": passage.get("doc_url", ""),
                "passage_text": (passage.get("passage_text", "") or ""),
            }
            for passage in selected_passages
        ]

        current_inputs["doc_selection_debug"] = {
            "rationales": rationales,
            "doc_filter": {
                "before": len(passages),
                "after": len(filtered_passages),
            },
            "coverage_matrix": coverage_result.get("coverage_matrix", {}),
            "dimension_scores": coverage_result.get("dimension_scores", {}),
            "passage_info_map": doc_info_map,
            "selected_passages": selected_summary,
        }

    async def _prepare_evidence(self, current_inputs: dict, raw_passages: list, section_idx) -> tuple[bool, str, list]:
        """Prepare evidence for sub-report generation.

        Returns (success, error_message, classified_content).
        On failure, error_message describes the failure and classified_content is [].
        Sets current_inputs fields: sub_section_core_content, sub_section_references,
        structured_evidence_guide, classified_content, required_target_citation_indexes,
        doc_selection_debug.
        """
        # New flow: rationale generation -> extractive summarization + scoring -> selection -> verify
        rationales, rationale_error = await self._generate_section_rationales(current_inputs)
        if not rationales:
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"rationale generation failed"
            )
            detail = ""
            if rationale_error and not LogManager.is_sensitive():
                detail = f": {rationale_error[:500]}"
            return False, f"rationale generation fail{detail}", []

        # Extractive summarization + scoring: LLM sees full docs, extracts
        # verbatim passages, and scores rationale coverage in one step.
        # Replaces COINS chunking + ngram filter + coverage matrix.
        coverage_result, coverage_error = await self._extract_and_score_documents(
            current_inputs, raw_passages, rationales
        )
        if not coverage_result:
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"extractive scoring failed: {coverage_error}"
            )
            detail = ""
            if coverage_error and not LogManager.is_sensitive():
                detail = f": {coverage_error[:500]}"
            return False, f"extractive scoring fail{detail}", []

        classify_doc_infos_res_top_k_num = current_inputs.get(
            "classify_doc_infos_res_top_k_num", 15
        )

        # passages for downstream is the extracted passages (passage-level)
        passages = coverage_result.get("filtered_passages", [])

        if not coverage_result.get("coverage_matrix"):
            # Degraded path: batch failures, empty LLM output, or missing
            # scores. Skip scoring-based selection and use the extracted
            # passages directly so the chapter is not lost. No temporal
            # weighting here: without coverage scores there is nothing to
            # re-rank, and a hard cut by time would lose the chapter.
            selected_passages = passages[:classify_doc_infos_res_top_k_num]
        else:
            # content_date scopes weight the sort by temporal compliance;
            # source_date / None scopes fall back to pure-coverage selection.
            _tscope = _resolve_content_date_scope(current_inputs.get("research_intent"))
            try:
                selected_passages, _ = self._select_by_rationale_coverage(
                    passages, rationales, coverage_result,
                    top_k=classify_doc_infos_res_top_k_num,
                    temporal=TemporalSelectionOptions(
                        temporal_scope=_tscope,
                        timeliness_weight=CONTENT_DATE_TIMELINESS_WEIGHT,
                    ),
                )
            except (TypeError, ValueError, AttributeError, KeyError) as e:
                # Defensive: LLM-returned coverage scores may have unexpected
                # types that slip through safe_float. Fall back to truncation
                # so the chapter is not lost entirely.
                logger.warning(
                    "%s [extract_score] section_idx: [%s] _select_by_rationale_coverage "
                    "failed (%s: %s), degrading to truncation",
                    EFFECT_SUB_REPORT_TAG, section_idx, type(e).__name__, e,
                )
                selected_passages = passages[:classify_doc_infos_res_top_k_num]

        # Write doc-selection debug info back to Section for ResultExporter
        # Placed before early returns so debug data is captured on all exit paths
        research_intent = current_inputs.get("research_intent") or {}
        target_papers = (
            research_intent.get("target_papers", [])
            if isinstance(research_intent, dict)
            else getattr(research_intent, "target_papers", [])
        )
        required_target_documents = ensure_exact_target_documents(
            [], raw_passages, target_papers
        )
        has_usable_required_target = any(
            str(doc.get("url") or doc.get("doc_url") or "").strip()
            and get_required_document_content(doc)
            for doc in required_target_documents
        )

        self._write_doc_selection_debug(
            current_inputs,
            PassageSelectionContext(
                rationales=rationales,
                coverage_result=coverage_result,
                passages=passages,
                selected_passages=selected_passages,
            ),
        )

        if not selected_passages and not has_usable_required_target:
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"no passages selected after optimization"
            )
            return False, "no passages selected after optimization", []

        selected_urls = list(dict.fromkeys(
            passage.get("doc_url", "") for passage in selected_passages if passage.get("doc_url")
        ))
        if not selected_urls and not has_usable_required_target:
            logger.error(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"no valid URLs in selected passages"
            )
            return False, "no valid URLs in selected passages", []

        # Full-text selection: pick top-10 URLs by frequency, use their
        # original_content from info_collector, build unified writing inputs.
        fulltext_result = enrich_fulltext_for_section(
            passages={"selected": selected_passages, "raw": raw_passages},
            context={
                "rationales": rationales,
                "coverage_result": coverage_result,
                "required_documents": required_target_documents,
            },
            section_idx=section_idx,
            top_n=10,
        )
        current_inputs["sub_section_core_content"] = fulltext_result["sub_section_core_content"]
        current_inputs["sub_section_core_content_from_background_knowledge"] = False
        current_inputs["sub_section_references"] = fulltext_result["sub_section_references"]
        current_inputs["structured_evidence_guide"] = fulltext_result["structured_evidence_guide"]
        current_inputs["classified_content"] = fulltext_result["classified_content"]
        current_inputs["required_target_citation_indexes"] = fulltext_result.get(
            "required_target_citation_indexes", []
        )

        # Store full-text evidence debug data for Excel export
        fulltext_result_remaining = fulltext_result.get("remaining_passages", [])
        remaining_passage_keys = fulltext_result.get("remaining_passage_keys", [])
        current_inputs.setdefault("doc_selection_debug", {})["fulltext_evidence"] = {
            "fulltext_docs": [
                {
                    "citation_index": ev.citation_index,
                    "url": ev.url,
                    "doc_title": ev.doc_title,
                    "doc_time": ev.doc_time,
                    "original_content": str(ev.original_content or "")[:5000],
                    "key_passages": ev.key_passages,
                    "coverage_scores": ev.coverage_scores,
                    "fetch_success": ev.fetch_success,
                }
                for ev in fulltext_result.get("fulltext_evidences", [])
            ],
            "remaining_passages": [
                {
                    "citation_index": p.get("index"),
                    "doc_title": p.get("doc_title", ""),
                    "doc_url": p.get("doc_url", ""),
                    "passage_key": (
                        remaining_passage_keys[idx]
                        if idx < len(remaining_passage_keys) else ""
                    ),
                    "passage_text": (p.get("passage_text", "") or "")[:500],
                }
                for idx, p in enumerate(fulltext_result_remaining)
            ],
            "fulltext_count": fulltext_result.get("fulltext_count", 0),
            "remaining_count": fulltext_result.get("remaining_count", 0),
        }

        classified_content = fulltext_result["classified_content"]

        # Part A：规则版覆盖证据（默认开，"key + coverage"双通道）。
        # 独立开关 DS_COVERAGE_RULE_BLOCK 可单独关闭/回滚。
        if _rule_coverage_block_enabled():
            # 纯 CPU 的正则抽取流水线（最坏 ~百 ms/章节），放线程池避免
            # 阻塞事件循环；GIL 下无真并行，收益是循环恢复可调度。
            core_content_list, rule_passage_texts = await asyncio.to_thread(
                _append_rule_coverage_to_core,
                current_inputs.get("sub_section_core_content", []),
                fulltext_result.get("fulltext_evidences", []),
            )
            coverage_block_count = 1 if rule_passage_texts else 0
            logger.info(
                "[rule_coverage] section_idx=%s fulltext_docs=%s coverage_docs=%s "
                "outline_coverage_blocks=%s",
                section_idx,
                len(fulltext_result.get("fulltext_evidences", [])),
                len(rule_passage_texts),
                coverage_block_count,
            )
        else:
            core_content_list = list(current_inputs.get("sub_section_core_content", []))
            rule_passage_texts = {}
            logger.info(
                "[generate_sub_report] section_idx=%s rule coverage block disabled "
                "(DS_COVERAGE_RULE_BLOCK=%s)",
                section_idx,
                os.environ.get("DS_COVERAGE_RULE_BLOCK", "1").strip(),
            )
        current_inputs["sub_section_core_content"] = core_content_list

        if LogManager.is_sensitive():
            logger.info(
                f"{EFFECT_SUB_REPORT_TAG} [generate_sub_report] section_idx: [{section_idx}], "
                f"selected_content len: {len(classified_content)}"
            )
        return True, "", classified_content


def _rule_coverage_block_enabled() -> bool:
    """解析独立开关 DS_COVERAGE_RULE_BLOCK（默认开）。

    标准布尔口径：`1`/`true`/`yes`/`on`（大小写与首尾空白不敏感）开启，
    其余值（如 `0`/`false`/`off`/空串）关闭，避免用户写 `true` 被静默关闭。
    """
    return os.environ.get("DS_COVERAGE_RULE_BLOCK", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _extract_doc_coverage_passages(item: dict) -> list[str]:
    """抽取单个选中文档的覆盖证据，并与该文档的大纲摘要块文本去重。

    去重基准是条目摘要块的实际渲染口径（方案乙）：fulltext 条目在大纲里渲染
    原文前 500 字符，摘要块已有的段落不再经规则块重复供给。key_passages 通道
    已退役（rationale 接管后不再进入大纲/写作 prompt），不再作为去重基准。
    """
    original_content = str(item.get("original_content") or "")
    if not original_content:
        return []
    passages = extract_coverage_passages(
        content=original_content,
        max_passages=_COVERAGE_TOP_K_CAP,
        max_chars=_COVERAGE_MAX_CHARS_PER_DOC,
    )
    summary_basis = [outline_summary_text(original_content)]
    passages = exclude_passages(passages, summary_basis)
    return [passage.text for passage in passages]


def _fit_coverage_to_budget(texts: list[str], budget: int) -> list[str]:
    """把单文档覆盖证据裁入剩余预算：整块尽量保留，放不下的块跳过、继续尝试
    后面更小的块（与 `collector_evidence._extract_coverage_passages_cached` 的
    预算循环同语义）；仅当第一个块就超出预算时截断它，确保每个文档至少能
    贡献一个证据块。"""
    kept: list[str] = []
    remaining = max(0, int(budget))
    for text in texts:
        if remaining <= 0:
            break
        if len(text) > remaining:
            if not kept:
                kept.append(text[:remaining])
                remaining = 0
                break
            continue
        kept.append(text)
        remaining -= len(text)
    return kept


def _append_rule_coverage_to_core(
    core_content_list: list[str],
    fulltext_evidences: list,
) -> tuple[list[str], dict[int, list[str]]]:
    """Part A：把规则版覆盖证据（默认开）组装进大纲证据，回到"key + coverage"双通道。

    运行时大网 evidence 由 `enrich_fulltext_for_section` 只拼 key 块；这里对每个
    全文证据（按 `build_core_content_list` 相同的 1..N 文档编号）抽取规则覆盖段落、
    裁入章节共享预算，`build_coverage_passage_block` 聚合后追加到大网证据末尾。
    章节共享预算耗尽后跳过剩余文档的抽取（结果恒为空，无谓开销）。

    性能预算（生产口径 top_n=10 × 10000 字符、高事实密度最坏用例，含缓存未
    命中的冷调用）：约 150 ms 纯 CPU（锚点去重键集合已按块缓存复用；2026-08
    测量口径，优化前同用例约 1.5 s）。本函数被 `_prepare_evidence` 经
    `asyncio.to_thread` 调用，不阻塞事件循环。

    Returns:
        (组装后的大网核心内容, 每文档规则覆盖段落文本) —— 后者仅供运行时日志
        统计覆盖文档数与大纲覆盖块数。
    """
    coverage_sections: list[tuple[int, list[str]]] = []
    coverage_passage_texts: dict[int, list[str]] = {}
    budget = _COVERAGE_MAX_TOTAL_CHARS
    for doc_index, evidence in enumerate(fulltext_evidences or [], start=1):
        if budget <= 0:
            break  # 章节共享预算已耗尽，后续文档无需再抽取
        kept_coverage = _fit_coverage_to_budget(
            _extract_doc_coverage_passages(
                {
                    "original_content": str(getattr(evidence, "original_content", "") or ""),

                }
            ),
            budget,
        )
        if not kept_coverage:
            continue
        coverage_sections.append((doc_index, kept_coverage))
        coverage_passage_texts[doc_index] = kept_coverage
        budget -= sum(len(text) for text in kept_coverage)
    if not coverage_sections:
        return list(core_content_list), coverage_passage_texts
    return (
        list(core_content_list) + [build_coverage_passage_block(coverage_sections)],
        coverage_passage_texts,
    )
