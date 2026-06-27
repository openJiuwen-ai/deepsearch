# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from openjiuwen_deepsearch.algorithm.user_feedback_processor.report_edit_utils import (
    strip_markup_in_range_with_metadata,
)
from openjiuwen_deepsearch.algorithm.source_trace.add_source import (
    add_source_references,
    generate_source_datas,
)
from openjiuwen_deepsearch.algorithm.source_trace.citation_checker_research import CitationCheckerResearch
from openjiuwen_deepsearch.algorithm.source_trace.content_analyzer import recognize_content_to_cite
from openjiuwen_deepsearch.algorithm.source_trace.source_matcher import match_sources
from openjiuwen_deepsearch.algorithm.source_trace.source_tracer_preprocessors import preprocess_search_record
from openjiuwen_deepsearch.utils.common_utils.text_utils import escape_markdown_link_text
from openjiuwen_deepsearch.utils.common_utils.markdown_url_utils import extract_markdown_url

logger = logging.getLogger(__name__)


class ChangeKind(str, Enum):
    """差异片段类型。"""

    EQUAL = "equal"
    REPLACE = "replace"
    INSERT = "insert"


@dataclass(frozen=True)
class DiffUnit:
    """用于 SequenceMatcher 的最小 diff 单元。

    Args:
        text: 原始 unit 文本。
        key: 用于精确匹配的规范化 key。
        start: unit 在所属 clean 文本中的起始偏移。
        end: unit 在所属 clean 文本中的结束偏移。
    """

    text: str
    key: str
    start: int
    end: int


@dataclass(frozen=True)
class RewriteSegment:
    """差异识别后的最终替换片段。

    Args:
        kind: 片段类型。
        text: 最终拼接用文本；equal 片段包含原始 markup。
        original_clean_start: 对应原文 clean 起始偏移；insert 时为 None。
        original_clean_end: 对应原文 clean 结束偏移；insert 时为 None。
    """

    kind: ChangeKind
    text: str
    original_clean_start: int | None = None
    original_clean_end: int | None = None


@dataclass(frozen=True)
class LocalTraceResult:
    """局部溯源结果。

    Args:
        text: 已转换为 checked citation 的局部文本，不包含局部参考文献章节。
        citation_data: 局部 citation checker 返回的有效 citation data。
        warning_info: 降级或失败时的 warning 信息。
    """

    text: str
    citation_data: list[dict] = field(default_factory=list)
    warning_info: str = ""


_DIFF_TOKEN_RE = re.compile(
    r"(```[\s\S]*?```|^#{1,6}\s+.*$|^\s*[-*+]\s+.*$|^\s*\|.*\|\s*$|\n{2,}|"
    r"[^\n。！？；.!?;]+[。！？；.!?;]?|\n+|\s+)",
    re.MULTILINE,
)
_REFERENCE_PREFIX_RE = re.compile(r"^\[(?P<index>\d+)\]\.\s+\[(?P<title>[^\]]+)\]\(")
_CHECKED_CITATION_PREFIX_RE = re.compile(
    r"\[\s*checked_citation:\s*(?P<id>\d+)\s*\]\[\[(?P<ref>\d+)\]\]\("
)
LOCAL_SOURCE_TRACE_MAX_CONCURRENCY = 4


@dataclass(frozen=True)
class ReferenceEntry:
    """参考文献条目解析结果。

    Args:
        index: 参考文献编号。
        title: 参考文献标题。
        url: 参考文献 URL。
    """

    index: int
    title: str
    url: str


@dataclass(frozen=True)
class CheckedCitationMarker:
    """checked citation 标记解析结果。

    Args:
        start: 标记起始偏移。
        end: 标记结束偏移。
        citation_id: 局部 citation id。
        reference_index: 局部参考文献编号。
        url: citation URL。
    """

    start: int
    end: int
    citation_id: int
    reference_index: int
    url: str


def _normalize_diff_key(text: str) -> str:
    """生成 diff 精确匹配 key。

    Args:
        text: 原始 diff unit 文本。

    Returns:
        统一换行后的匹配 key；纯空白保留精确差异，文本内容折叠内部空白。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and normalized.strip() == "":
        # 纯空白 unit 代表真实格式，不能全部归一成空串，否则 SequenceMatcher 会吞掉换行变化。
        return f"__whitespace__:{normalized!r}"
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def split_diff_units(text: str) -> list[DiffUnit]:
    """按 Markdown 块、句子和空白切分 diff 单元。

    Args:
        text: 待切分 clean 文本。

    Returns:
        DiffUnit 列表，覆盖整个输入文本。
    """
    units: list[DiffUnit] = []
    cursor = 0
    for match in _DIFF_TOKEN_RE.finditer(text):
        if match.start() > cursor:
            chunk = text[cursor:match.start()]
            units.append(DiffUnit(chunk, _normalize_diff_key(chunk), cursor, match.start()))
        chunk = match.group(0)
        units.append(DiffUnit(chunk, _normalize_diff_key(chunk), match.start(), match.end()))
        cursor = match.end()
    if cursor < len(text):
        chunk = text[cursor:]
        units.append(DiffUnit(chunk, _normalize_diff_key(chunk), cursor, len(text)))
    return [unit for unit in units if unit.text]


def _raw_slice_for_clean_range(raw_text: str, boundary_map: list[int], clean_start: int, clean_end: int) -> str | None:
    """把 clean 范围映射回 raw 文本片段。

    Args:
        raw_text: 原始带 markup 文本。
        boundary_map: clean 边界到 raw 边界的映射。
        clean_start: clean 起始偏移。
        clean_end: clean 结束偏移。

    Returns:
        映射成功时返回 raw 片段，否则返回 None。
    """
    if clean_start < 0 or clean_end < clean_start or clean_end > len(boundary_map) - 1:
        return None
    raw_start = boundary_map[clean_start]
    raw_end = boundary_map[clean_end]
    if raw_start < 0 or raw_end < raw_start or raw_end > len(raw_text):
        return None
    return raw_text[raw_start:raw_end]


def build_diff_segments(
    raw_text: str,
    original_text_clean: str,
    rewritten_text: str,
    clean_boundary_to_raw_boundary: list[int],
) -> list[RewriteSegment]:
    """构建差异片段，未变化片段回填原始 markup。

    Args:
        raw_text: 原始被替换范围文本，包含 citation / inference markup。
        original_text_clean: 清洗后的原始文本。
        rewritten_text: LLM 改写后的文本。
        clean_boundary_to_raw_boundary: clean 边界到 raw 边界的映射。

    Returns:
        RewriteSegment 列表，按最终文本顺序排列。
    """
    old_units = split_diff_units(original_text_clean)
    new_units = split_diff_units(rewritten_text)
    matcher = difflib.SequenceMatcher(
        None,
        [unit.key for unit in old_units],
        [unit.key for unit in new_units],
        autojunk=False,
    )
    segments: list[RewriteSegment] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        old_text_start = old_units[old_start].start if old_start < len(old_units) else len(original_text_clean)
        old_text_end = old_units[old_end - 1].end if old_end > old_start else old_text_start
        new_text = "".join(unit.text for unit in new_units[new_start:new_end])
        if tag == "equal":
            old_text = original_text_clean[old_text_start:old_text_end]
            if old_text != new_text:
                # SequenceMatcher 使用归一化 key 匹配稳定锚点；字面内容不同仍必须保留改写结果。
                segments.append(RewriteSegment(ChangeKind.REPLACE, new_text, old_text_start, old_text_end))
                continue
            raw_text_slice = _raw_slice_for_clean_range(
                raw_text,
                clean_boundary_to_raw_boundary,
                old_text_start,
                old_text_end,
            )
            if raw_text_slice is None:
                logger.warning("[LocalSourceTrace] clean-to-raw mapping failed; treating equal block as changed.")
                segments.append(RewriteSegment(ChangeKind.REPLACE, new_text, old_text_start, old_text_end))
            else:
                segments.append(RewriteSegment(ChangeKind.EQUAL, raw_text_slice, old_text_start, old_text_end))
        elif tag == "insert":
            segments.append(RewriteSegment(ChangeKind.INSERT, new_text, None, None))
        elif tag == "replace":
            segments.append(RewriteSegment(ChangeKind.REPLACE, new_text, old_text_start, old_text_end))
        elif tag == "delete":
            continue
    return [segment for segment in segments if segment.text]


def collect_existing_citation_candidates(citation_messages: dict, removed_citations: list) -> list[dict]:
    """从被移除 checked citation id 找回已有来源候选。

    Args:
        citation_messages: 当前 final_result 中的 citation_messages。
        removed_citations: 被清洗的 citation 元数据列表。

    Returns:
        可转换为 source record 的候选来源列表。
    """
    data = citation_messages.get("data", []) if isinstance(citation_messages, dict) else []
    by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
    candidates = []
    seen_keys = set()
    for removed in removed_citations:
        citation_id = getattr(removed, "checked_citation_id", None)
        item = by_id.get(citation_id)
        if not item:
            continue
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content") or ""
        key = (title, url, content)
        if _has_complete_unseen_source(title, url, content, key, seen_keys):
            candidates.append({"title": title, "url": url, "content": content})
            seen_keys.add(key)
    return candidates


def _has_complete_unseen_source(title: str, url: str, content: str, key: tuple, seen_keys: set) -> bool:
    """判断来源条目是否信息完整且未重复。

    Args:
        title: 来源标题。
        url: 来源链接。
        content: 来源正文内容。
        key: 用于去重的来源唯一键。
        seen_keys: 已收集来源键集合。

    Returns:
        来源包含标题、链接、正文且尚未出现时返回 True。
    """
    return bool(title and url and content) and key not in seen_keys


def convert_doc_infos_to_search_records(doc_infos: list) -> list[dict]:
    """把 doc_infos 转为 source trace search_record 条目。

    Args:
        doc_infos: collector 或 new_task 返回的文档列表。

    Returns:
        包含 title、url、content 的 search record 列表。
    """
    records = []
    seen_keys = set()
    for item in doc_infos or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("original_content") or item.get("content") or item.get("chunk") or ""
        key = (title, url, content)
        if _has_complete_unseen_source(title, url, content, key, seen_keys):
            records.append({"title": title, "url": url, "content": content})
            seen_keys.add(key)
    return records


def _parse_reference_line(line: str) -> ReferenceEntry | None:
    """解析单行参考文献。

    Args:
        line: 单行报告文本。

    Returns:
        解析成功时返回 ReferenceEntry；格式不匹配或 URL 未闭合时返回 None。
    """
    match = _REFERENCE_PREFIX_RE.match(line)
    if not match:
        return None
    open_paren_index = match.end() - 1
    parsed_url = extract_markdown_url(line, open_paren_index)
    if parsed_url is None:
        return None
    url, end = parsed_url
    if line[end:].strip():
        return None
    return ReferenceEntry(index=int(match.group("index")), title=match.group("title"), url=url)


def _iter_checked_citation_markers(text: str) -> list[CheckedCitationMarker]:
    """解析文本中的 checked citation 标记。

    Args:
        text: 待解析文本。

    Returns:
        URL 成功闭合的 checked citation 标记列表。
    """
    markers = []
    for match in _CHECKED_CITATION_PREFIX_RE.finditer(text or ""):
        open_paren_index = match.end() - 1
        parsed_url = extract_markdown_url(text, open_paren_index)
        if parsed_url is None:
            continue
        url, end = parsed_url
        markers.append(
            CheckedCitationMarker(
                start=match.start(),
                end=end,
                citation_id=int(match.group("id")),
                reference_index=int(match.group("ref")),
                url=url,
            )
        )
    return markers


def extract_reference_map(report: str) -> tuple[dict[str, int], int]:
    """提取正文现有参考文献 URL 到编号的映射。

    Args:
        report: 完整报告正文。

    Returns:
        ``(url_to_reference_index, max_reference_index)``。
    """
    reference_map: dict[str, int] = {}
    max_index = 0
    for line in (report or "").splitlines():
        entry = _parse_reference_line(line.strip())
        if entry is None:
            continue
        index = entry.index
        reference_map[entry.url] = index
        max_index = max(max_index, index)
    return reference_map, max_index


def append_reference_entries(
    report: str,
    new_reference_items: list[dict],
    existing_reference_map: dict[str, int],
    max_reference_index: int,
) -> str:
    """按 append-only 策略追加新增参考文献。

    Args:
        report: 完整报告正文。
        new_reference_items: 新增 citation data，需包含 url、title、reference_index。
        existing_reference_map: 已有 URL 到参考编号的映射。
        max_reference_index: 已有最大参考编号，用于防御异常编号。

    Returns:
        追加新参考文献后的报告正文。
    """
    additions = []
    seen_urls = set(existing_reference_map)
    for item in new_reference_items:
        url = item.get("url", "")
        title = item.get("title", "")
        reference_index = item.get("reference_index")
        if _should_skip_reference_entry(url, title, reference_index, seen_urls):
            continue
        reference_index = max(int(reference_index), max_reference_index + 1)
        max_reference_index = max(max_reference_index, reference_index)
        safe_title = escape_markdown_link_text(title)
        additions.append(f"[{reference_index}]. [{safe_title}]({url})")
        seen_urls.add(url)
    if not additions:
        return report
    return f"{report.rstrip()}\n\n" + "\n\n".join(additions) + "\n\n"


def _should_skip_reference_entry(url: str, title: str, reference_index: int | None, seen_urls: set) -> bool:
    """判断参考文献条目是否应跳过追加。

    Args:
        url: 参考文献链接。
        title: 参考文献标题。
        reference_index: 参考文献编号。
        seen_urls: 已存在或已追加的参考文献 URL 集合。

    Returns:
        缺少链接、标题、编号，或 URL 已存在时返回 True。
    """
    return not (url and title and reference_index) or url in seen_urls


def _strip_generated_reference_section(text: str) -> str:
    """移除 citation checker 为局部文本追加的临时参考文献章节。

    Args:
        text: citation checker 输出文本。

    Returns:
        去除尾部参考文献条目后的局部文本。
    """
    lines = (text or "").rstrip().splitlines()
    cut = len(lines)
    while cut > 0 and not lines[cut - 1].strip():
        cut -= 1
    while cut > 0 and _parse_reference_line(lines[cut - 1].strip()) is not None:
        cut -= 1
        while cut > 0 and not lines[cut - 1].strip():
            cut -= 1
    return "\n".join(lines[:cut]).rstrip()


async def run_local_source_trace(
    text: str,
    source_records: list[dict],
    llm_model_name: str,
    language: str = "zh-CN",
) -> LocalTraceResult:
    """对单个变化片段执行溯源生成和溯源校验。

    Args:
        text: 变化片段文本。
        source_records: 候选来源记录，包含 title、url、content。
        llm_model_name: 溯源生成和校验使用的模型名。
        language: 报告语言标识。

    Returns:
        LocalTraceResult: 局部溯源后的文本、有效 citation data 和 warning。
    """
    if not text.strip():
        logger.info("[LocalSourceTrace] segment trace skipped. reason=empty_text")
        return LocalTraceResult(text=text)
    if not source_records:
        logger.info(
            "[LocalSourceTrace] segment trace skipped. reason=no_source_records text_len=%s",
            len(text),
        )
        return LocalTraceResult(text=text, warning_info="No local source records for changed rewrite segment.")
    try:
        logger.info(
            "[LocalSourceTrace] segment trace started. text_len=%s source_record_count=%s",
            len(text),
            len(source_records),
        )
        search_record = {"search_record": source_records}
        preprocessed_search_record = preprocess_search_record(search_record, 3000)
        if not preprocessed_search_record:
            logger.info(
                "[LocalSourceTrace] segment trace degraded. reason=records_filtered source_record_count=%s",
                len(source_records),
            )
            return LocalTraceResult(text=text, warning_info="Local source records were filtered during preprocessing.")

        recognition_result = await recognize_content_to_cite(text, 0.9, llm_model_name)
        if not recognition_result:
            logger.info("[LocalSourceTrace] segment trace degraded. reason=no_citable_content text_len=%s", len(text))
            return LocalTraceResult(text=text, warning_info="Local source trace found no citable content.")

        trace_results = await match_sources(recognition_result, preprocessed_search_record, 40, llm_model_name)
        if not trace_results:
            logger.info(
                "[LocalSourceTrace] segment trace degraded. reason=no_source_matches citable_item_count=%s",
                len(recognition_result),
            )
            return LocalTraceResult(text=text, warning_info="Local source trace found no source matches.")

        source_datas = generate_source_datas(text, preprocessed_search_record, trace_results)
        traced_text, source_datas = add_source_references(text, source_datas)
        if not source_datas:
            logger.info(
                "[LocalSourceTrace] segment trace degraded. reason=no_valid_source_data trace_result_count=%s",
                len(trace_results),
            )
            return LocalTraceResult(text=text, warning_info="Local source trace produced no valid source data.")

        checker = CitationCheckerResearch(llm_model_name)
        checked_payload = await checker.checker({"language": language, "article": traced_text}, source_datas)
        checked_result = json.loads(checked_payload)
        checked_text = checked_result.get("checked_trace_source_report_content", "")
        citation_messages = checked_result.get("citation_messages", {})
        logger.info(
            "[LocalSourceTrace] segment trace completed. source_data_count=%s citation_data_count=%s",
            len(source_datas),
            len(citation_messages.get("data", [])),
        )
        return LocalTraceResult(
            text=_strip_generated_reference_section(checked_text),
            citation_data=citation_messages.get("data", []),
        )
    except Exception as error:
        logger.warning("[LocalSourceTrace] local source trace failed: %s", error)
        return LocalTraceResult(text=text, warning_info=f"Local source trace failed: {error}")


async def _run_local_source_trace_with_semaphore(
    semaphore: asyncio.Semaphore,
    text: str,
    source_records: list[dict],
    llm_model_name: str,
    language: str,
) -> LocalTraceResult:
    """在并发上限内执行单个变化片段的局部溯源。

    Args:
        semaphore: 控制同时溯源片段数量的信号量。
        text: 变化片段文本。
        source_records: 候选来源记录。
        llm_model_name: 溯源使用的模型名。
        language: 报告语言标识。

    Returns:
        单个片段的局部溯源结果。
    """
    async with semaphore:
        return await run_local_source_trace(text, source_records, llm_model_name, language=language)


def _max_existing_citation_id(existing_citation_messages: dict) -> int:
    """获取已有 citation data 最大 id。

    Args:
        existing_citation_messages: 当前 final_result citation_messages。

    Returns:
        最大 id；没有数据时返回 -1。
    """
    data = existing_citation_messages.get("data", []) if isinstance(existing_citation_messages, dict) else []
    ids = [item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), int)]
    return max(ids) if ids else -1


def apply_global_citation_numbering(
    local_text: str,
    local_citation_data: list[dict],
    existing_citation_messages: dict,
    existing_reference_map: dict[str, int],
    max_reference_index: int,
) -> tuple[str, list[dict]]:
    """把局部 citation id/reference_index 适配为全局编号。

    Args:
        local_text: 局部 checked citation 文本。
        local_citation_data: 局部 citation data。
        existing_citation_messages: 现有 citation_messages。
        existing_reference_map: 现有 URL 到参考编号映射。
        max_reference_index: 现有最大参考编号。

    Returns:
        ``(new_text, new_citation_data)``。
    """
    next_citation_id = _max_existing_citation_id(existing_citation_messages) + 1
    next_reference_index = max_reference_index + 1
    citation_id_map: dict[int, int] = {}
    reference_index_by_url = dict(existing_reference_map)
    local_data_by_id = {item.get("id"): dict(item) for item in local_citation_data if isinstance(item, dict)}
    updated_data_by_local_id: dict[int, dict] = {}
    local_id_order: list[int] = []

    def replace_checked_citation_marker(marker: CheckedCitationMarker) -> str:
        """替换单个局部 checked citation 标记。

        Args:
            marker: 已解析的 checked citation 标记。

        Returns:
            替换为全局编号后的 checked citation 标记。
        """
        nonlocal next_citation_id, next_reference_index
        local_id = marker.citation_id
        url = marker.url
        if local_id not in citation_id_map:
            citation_id_map[local_id] = next_citation_id
            next_citation_id += 1
            local_id_order.append(local_id)
        if url not in reference_index_by_url:
            reference_index_by_url[url] = next_reference_index
            next_reference_index += 1
        global_id = citation_id_map[local_id]
        global_reference_index = reference_index_by_url[url]
        item = local_data_by_id.get(local_id, {"url": url})
        item["id"] = global_id
        item["reference_index"] = global_reference_index
        item["url"] = item.get("url") or url
        updated_data_by_local_id[local_id] = item
        return f"[checked_citation:{global_id}][[{global_reference_index}]]({url})"

    parts = []
    cursor = 0
    for marker in _iter_checked_citation_markers(local_text):
        parts.append(local_text[cursor:marker.start])
        parts.append(replace_checked_citation_marker(marker))
        cursor = marker.end
    parts.append(local_text[cursor:])
    updated_text = "".join(parts)
    updated_data = [
        updated_data_by_local_id[local_id]
        for local_id in local_id_order
        if local_id in updated_data_by_local_id
    ]
    return updated_text, updated_data


def _removed_citations_for_clean_range(strip_result, clean_start: int | None, clean_end: int | None) -> list:
    """按变化片段 clean 范围筛选被移除的 citation。

    Args:
        strip_result: MarkupStripResult。
        clean_start: 变化片段原文 clean 起始。
        clean_end: 变化片段原文 clean 结束。

    Returns:
        落在变化范围内的 removed citation 列表。
    """
    if clean_start is None or clean_end is None:
        return []
    boundary_map = strip_result.clean_boundary_to_raw_boundary
    if clean_start < 0 or clean_end > len(boundary_map) - 1:
        return []
    raw_start = boundary_map[clean_start]
    raw_end = boundary_map[clean_end]
    return [
        item for item in strip_result.removed_citations
        if item.raw_start >= raw_start and item.raw_end <= raw_end
    ]


def _merge_source_records(*record_groups: list[dict]) -> list[dict]:
    """合并来源候选并去重。

    Args:
        *record_groups: 多组来源记录。

    Returns:
        去重后的来源记录列表。
    """
    merged = []
    seen = set()
    for records in record_groups:
        for record in records:
            key = (record.get("title"), record.get("url"), record.get("content"))
            if all(key) and key not in seen:
                merged.append(record)
                seen.add(key)
    return merged


def _restore_segment_boundary_whitespace(original_text: str, traced_text: str) -> str:
    """恢复局部溯源片段的首尾空白边界。

    Args:
        original_text: diff 片段进入局部溯源前的原始文本。
        traced_text: 局部溯源和 citation checker 处理后的文本。

    Returns:
        保留原片段首尾空白后的溯源文本。
    """
    leading_whitespace = original_text[: len(original_text) - len(original_text.lstrip())]
    trailing_whitespace = original_text[len(original_text.rstrip()):]
    if leading_whitespace:
        traced_text = leading_whitespace + traced_text.lstrip()
    if trailing_whitespace:
        traced_text = traced_text.rstrip() + trailing_whitespace
    return traced_text


def _resolve_diff_original_text_clean(action_result: dict, stripped_original_text: str) -> str:
    """选择用于 diff 的完整原文 clean 文本。

    Args:
        action_result: 子处理器返回的改写结果。
        stripped_original_text: 从 original_text 实时清洗出的完整 clean 文本。

    Returns:
        与 original_text raw 边界一致的 clean 文本。
    """
    action_clean = action_result.get("original_text_clean", "")
    if action_clean == stripped_original_text:
        return action_clean
    if action_clean:
        logger.info(
            "[LocalSourceTrace] original_text_clean does not match stripped original_text; "
            "using stripped full original text for diff."
        )
    return stripped_original_text


async def apply_local_source_trace_to_action_result(
    feedback: dict,
    action_result: dict,
    final_result: dict,
    llm_model_name: str,
    language: str = "zh-CN",
) -> dict:
    """对改写动作结果应用差异感知局部溯源。

    Args:
        feedback: 用户反馈动作。
        action_result: 子处理器返回的改写结果。
        final_result: 当前完整结果快照。
        llm_model_name: 溯源使用的模型名。
        language: 报告语言标识。

    Returns:
        增强后的 action_result，可能包含 citation_messages 和 warning_info。
    """
    action = feedback.get("action", "")
    if action in {"sync", "finish"} or "rewritten_text" not in action_result:
        logger.info(
            "[LocalSourceTrace] action trace skipped. action=%s reason=non_rewrite_action_or_missing_rewritten_text",
            action,
        )
        return action_result

    original_text = action_result.get("original_text", "")
    rewritten_text = action_result.get("rewritten_text", "")
    if not rewritten_text:
        logger.info("[LocalSourceTrace] action trace skipped. action=%s reason=empty_rewritten_text", action)
        return action_result

    logger.info(
        "[LocalSourceTrace] action trace started. action=%s original_len=%s rewritten_len=%s",
        action,
        len(original_text),
        len(rewritten_text),
    )
    if original_text:
        strip_result = strip_markup_in_range_with_metadata(original_text, 0, len(original_text))
        original_text_clean = _resolve_diff_original_text_clean(action_result, strip_result.text)
        segments = build_diff_segments(
            raw_text=original_text,
            original_text_clean=original_text_clean,
            rewritten_text=rewritten_text,
            clean_boundary_to_raw_boundary=strip_result.clean_boundary_to_raw_boundary,
        )
        removed_citations = strip_result.removed_citations
    else:
        segments = [RewriteSegment(ChangeKind.INSERT, rewritten_text, None, None)]
        removed_citations = []

    citation_messages = final_result.get("citation_messages", {}) or {}
    existing_reference_map, max_reference_index = extract_reference_map(final_result.get("response_content", "") or "")
    all_existing_candidates = collect_existing_citation_candidates(citation_messages, removed_citations)
    action_doc_records = convert_doc_infos_to_search_records(action_result.get("source_trace_doc_infos", []))
    existing_citation_data = citation_messages.get("data", []) or []
    changed_segment_count = sum(1 for segment in segments if segment.kind != ChangeKind.EQUAL)
    logger.info(
        "[LocalSourceTrace] action trace diff prepared. action=%s segment_count=%s "
        "changed_segment_count=%s existing_candidate_count=%s action_doc_record_count=%s",
        action,
        len(segments),
        changed_segment_count,
        len(all_existing_candidates),
        len(action_doc_records),
    )

    new_citation_data: list[dict] = []
    warning_parts: list[str] = []
    final_parts: list[str | None] = [None] * len(segments)
    trace_tasks: dict[int, asyncio.Task[LocalTraceResult]] = {}
    trace_semaphore = asyncio.Semaphore(LOCAL_SOURCE_TRACE_MAX_CONCURRENCY)
    traced_segment_count = 0
    skipped_no_source_segment_count = 0
    for segment_index, segment in enumerate(segments):
        if segment.kind == ChangeKind.EQUAL:
            final_parts[segment_index] = segment.text
            continue

        segment_removed = (
            _removed_citations_for_clean_range(strip_result, segment.original_clean_start, segment.original_clean_end)
            if original_text
            else []
        )
        segment_candidates = collect_existing_citation_candidates(citation_messages, segment_removed)
        if not segment_candidates and action in {"expand", "polish", "shorten"}:
            segment_candidates = all_existing_candidates
        source_records = _merge_source_records(segment_candidates, action_doc_records)
        if not source_records:
            skipped_no_source_segment_count += 1
            final_parts[segment_index] = segment.text
            continue

        traced_segment_count += 1
        trace_tasks[segment_index] = asyncio.create_task(
            _run_local_source_trace_with_semaphore(
                trace_semaphore,
                segment.text,
                source_records,
                llm_model_name,
                language,
            )
        )

    trace_results = {}
    if trace_tasks:
        task_results = await asyncio.gather(*trace_tasks.values())
        trace_results = dict(zip(trace_tasks.keys(), task_results))

    for segment_index, segment in enumerate(segments):
        if final_parts[segment_index] is not None:
            continue

        trace_result = trace_results.get(segment_index)
        if trace_result is None:
            raise KeyError(f"missing local source trace result for segment index {segment_index}")
        if trace_result.warning_info:
            warning_parts.append(trace_result.warning_info)
        local_traced_text = _restore_segment_boundary_whitespace(segment.text, trace_result.text)
        traced_text, traced_data = apply_global_citation_numbering(
            local_text=local_traced_text,
            local_citation_data=trace_result.citation_data,
            existing_citation_messages={"data": [*existing_citation_data, *new_citation_data]},
            existing_reference_map=existing_reference_map,
            max_reference_index=max_reference_index,
        )
        for item in traced_data:
            item_url = item.get("url")
            item_reference_index = item.get("reference_index")
            if item_url and item_reference_index and item_url not in existing_reference_map:
                existing_reference_map[item_url] = item_reference_index
                max_reference_index = max(max_reference_index, item_reference_index)
        new_citation_data.extend(traced_data)
        final_parts[segment_index] = traced_text

    final_rewritten_text = "".join(part or "" for part in final_parts)
    start = action_result["rewritten_start_offset"]
    end = action_result["rewritten_end_offset"]
    new_report = action_result["new_report"][:start] + final_rewritten_text + action_result["new_report"][end:]
    reference_map_for_report, max_reference_for_report = extract_reference_map(new_report)
    new_report = append_reference_entries(
        new_report,
        new_citation_data,
        reference_map_for_report,
        max_reference_for_report,
    )

    updated = dict(action_result)
    updated.update(
        {
            "new_report": new_report,
            "rewritten_text": final_rewritten_text,
            "rewritten_end_offset": start + len(final_rewritten_text),
        }
    )
    if new_citation_data:
        updated_citation_messages = dict(citation_messages or {})
        updated_citation_messages["data"] = [*existing_citation_data, *new_citation_data]
        updated["citation_messages"] = updated_citation_messages
    if warning_parts:
        existing_warning = action_result.get("warning_info", "")
        joined_warning = " | ".join(warning_parts)
        updated["warning_info"] = f"{existing_warning} | {joined_warning}" if existing_warning else joined_warning
    logger.info(
        "[LocalSourceTrace] action trace completed. action=%s segment_count=%s changed_segment_count=%s "
        "traced_segment_count=%s skipped_no_source_segment_count=%s new_citation_count=%s warning_count=%s",
        action,
        len(segments),
        changed_segment_count,
        traced_segment_count,
        skipped_no_source_segment_count,
        len(new_citation_data),
        len(warning_parts),
    )
    return updated
