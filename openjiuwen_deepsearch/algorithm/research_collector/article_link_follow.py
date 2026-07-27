"""Pure helpers for discovering one-hop article links in collector evidence."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from pydantic import BaseModel, Field

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import (
    canonicalize_url,
)

_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]*)\]\(((?:[^()\s]+|\([^()\s]*\))+?)\)"
)
_HTML_LINK_RE = re.compile(
    r"<a\b[^<>]*?\bhref\s*=\s*(['\"])([^'\"<>]*)\1[^<>]*>"
    r"((?:[^<]|<(?!/?a\b)[^>]*>)*)</a\s*>",
    flags=re.IGNORECASE,
)
_PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", flags=re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BLOCKED_SUFFIXES = {
    ".7z", ".avi", ".bmp", ".css", ".doc", ".docx", ".exe", ".gif",
    ".gz", ".ico", ".jpeg", ".jpg", ".js", ".mov", ".mp3", ".mp4",
    ".png", ".ppt", ".pptx", ".rar", ".svg", ".tar", ".webp", ".xls",
    ".xlsx", ".zip",
}
_CONTEXT_CHARS = 160
ARTICLE_LINK_SOURCE_FIELD = "_article_link_follow_source"
ARTICLE_LINK_SOURCE_COUNT_FIELD = "_article_link_follow_source_count"
_DEFAULT_SOURCE_LINK_LIMIT = 20
_MAX_SOURCE_ANCHOR_LENGTH = 200
_MAX_SOURCE_HREF_LENGTH = 2048
_WIKIPEDIA_BLOCKED_NAMESPACES = {
    "category", "file", "help", "portal", "special", "talk", "template",
    "user", "user_talk", "wikipedia", "wikipedia_talk",
}
_ACTION_TERMS = {
    "advertise", "contact", "login", "privacy", "register", "share",
    "signin", "signup", "subscribe", "广告", "联系我们", "登录", "隐私",
    "分享", "注册", "订阅",
}
_NAVIGATION_PATH_SEGMENTS = {"archive", "category", "categories", "tag", "tags"}
_EVIDENCE_TERMS = {
    "annual-report", "dataset", "methodology", "paper", "publication",
    "regulation", "report", "research", "study", "whitepaper", "white-paper",
    "年度报告", "方法", "数据集", "法规", "白皮书", "研究", "论文", "报告",
}
_STOP_WORDS = {
    "a", "an", "and", "for", "from", "in", "of", "on", "or", "the", "to",
    "与", "及", "和", "在", "对", "的",
}


@dataclass(frozen=True)
class ArticleLinkOrigin:
    """A parent document occurrence that exposed a candidate link."""

    parent_doc_id: str
    parent_title: str
    parent_url: str
    query: str
    anchor_text: str


@dataclass
class ArticleLinkCandidate:
    """A canonical candidate link plus its discovery context."""

    candidate_index: int
    url: str
    canonical_url: str
    anchor_text: str
    context_before: str
    context_after: str
    parent_doc_id: str
    parent_title: str
    parent_url: str
    query: str
    source_position: int = 0
    origins: list[ArticleLinkOrigin] = field(default_factory=list)


@dataclass(frozen=True)
class RankedArticleLink:
    """A deterministically selected candidate and its matched rules."""

    candidate_index: int
    reasons: tuple[str, ...]


@dataclass
class ArticleLinkCandidateBuildStats:
    """解释文章链接候选在纯构建阶段如何被过滤。"""

    source_doc_count: int = 0
    depth_filtered_parent_count: int = 0
    empty_parent_count: int = 0
    unfollowable_parent_count: int = 0
    raw_extracted_link_count: int = 0
    invalid_url_count: int = 0
    blocked_suffix_count: int = 0
    self_link_filtered_count: int = 0
    existing_url_filtered_count: int = 0
    attempted_url_filtered_count: int = 0
    wikipedia_system_filtered_count: int = 0
    duplicate_link_count: int = 0
    parent_limit_filtered_count: int = 0
    total_limit_filtered_count: int = 0
    final_candidate_count: int = 0


class ArticleLinkEvidence(BaseModel):
    """Bounded evidence extracted from a followed webpage."""

    title: str = ""
    original_content: str = ""
    key_passages: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _ExtractedLink:
    position: int
    href: str
    anchor_text: str
    context_before: str
    context_after: str


def _context(content: str, start: int, end: int) -> tuple[str, str]:
    return (
        content[max(0, start - _CONTEXT_CHARS):start].strip(),
        content[end:end + _CONTEXT_CHARS].strip(),
    )


def _extract_links(content: str) -> list[_ExtractedLink]:
    extracted: list[_ExtractedLink] = []
    protected_spans: list[tuple[int, int]] = [
        match.span() for match in _HTML_TAG_RE.finditer(content)
    ]
    for match in _MARKDOWN_LINK_RE.finditer(content):
        protected_spans.append(match.span())
        before, after = _context(content, match.start(), match.end())
        extracted.append(_ExtractedLink(
            position=match.start(),
            href=match.group(2).strip(),
            anchor_text=match.group(1).strip(),
            context_before=before,
            context_after=after,
        ))
    for match in _HTML_LINK_RE.finditer(content):
        protected_spans.append(match.span())
        before, after = _context(content, match.start(), match.end())
        extracted.append(_ExtractedLink(
            position=match.start(),
            href=match.group(2).strip(),
            anchor_text=_HTML_TAG_RE.sub("", match.group(3)).strip(),
            context_before=before,
            context_after=after,
        ))
    for match in _PLAIN_URL_RE.finditer(content):
        if any(start <= match.start() < end for start, end in protected_spans):
            continue
        before, after = _context(content, match.start(), match.end())
        extracted.append(_ExtractedLink(
            position=match.start(),
            href=match.group(0).rstrip(".,;:!?"),
            anchor_text="",
            context_before=before,
            context_after=after,
        ))
    return sorted(extracted, key=lambda item: item.position)


def _strip_links_from_context(value: str) -> str:
    """Remove link targets from context while retaining human-readable labels."""
    without_markdown = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    without_html = _HTML_LINK_RE.sub(
        lambda match: _HTML_TAG_RE.sub("", match.group(3)),
        without_markdown,
    )
    return " ".join(_PLAIN_URL_RE.sub("", without_html).split())


def _anchor_from_url(value: str) -> str:
    """Derive a conservative human-readable anchor from a URL path or title."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    query = parse_qs(parsed.query)
    raw = (query.get("title") or [""])[0]
    if not raw:
        raw = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    words = re.sub(r"[-_]+", " ", unquote(raw)).strip()
    return " ".join(words.split())[:_MAX_SOURCE_ANCHOR_LENGTH].strip()


def _is_wikipedia_url(value: str) -> bool:
    try:
        hostname = (urlparse(value).hostname or "").casefold()
    except ValueError:
        return False
    return hostname == "wikipedia.org" or hostname.endswith(".wikipedia.org")


def _wikipedia_article_identity(value: str) -> tuple[str, str] | None:
    """Return host and normalized article title for Wikipedia article URLs."""
    if not _is_wikipedia_url(value):
        return None
    parsed = urlparse(value)
    title = ""
    if parsed.path.startswith("/wiki/"):
        title = parsed.path[len("/wiki/"):]
    elif parsed.path == "/w/index.php":
        title = (parse_qs(parsed.query).get("title") or [""])[0]
    title = unquote(title).replace(" ", "_").strip("_")
    if not title:
        return None
    return ((parsed.hostname or "").casefold(), title.casefold())


def _is_wikipedia_system_url(value: str) -> bool:
    """Reject Wikipedia actions, revisions, and non-article namespaces."""
    if not _is_wikipedia_url(value):
        return False
    parsed = urlparse(value)
    if parsed.path == "/w/index.php":
        return True
    identity = _wikipedia_article_identity(value)
    if identity is None:
        return True
    namespace = identity[1].split(":", 1)[0]
    return ":" in identity[1] and namespace in _WIKIPEDIA_BLOCKED_NAMESPACES


def build_article_link_source_with_count(
    content: str,
    max_links: int = _DEFAULT_SOURCE_LINK_LIMIT,
) -> tuple[str, int]:
    """Build a bounded pre-compression link source and its unique link count."""
    bounded_limit = max(0, int(max_links))
    if bounded_limit == 0:
        return "", 0
    records: list[str] = []
    seen_hrefs: set[str] = set()
    protected_spans = [
        match.span()
        for pattern in (_MARKDOWN_LINK_RE, _HTML_LINK_RE)
        for match in pattern.finditer(content or "")
    ]
    for extracted in _extract_links(content or ""):
        if any(
            start < extracted.position < end
            for start, end in protected_spans
        ):
            continue
        href = extracted.href.strip()
        if (
            not href
            or len(href) > _MAX_SOURCE_HREF_LENGTH
            or href in seen_hrefs
        ):
            continue
        seen_hrefs.add(href)
        safe_href = href.replace(")", "%29")
        anchor = _PLAIN_URL_RE.sub(
            "source link",
            extracted.anchor_text.strip(),
        )
        anchor = anchor.replace("[", "").replace("]", "").strip()
        anchor = (
            anchor[:_MAX_SOURCE_ANCHOR_LENGTH].strip()
            or _anchor_from_url(href)
            or "source link"
        )
        before = _strip_links_from_context(extracted.context_before)
        after = _strip_links_from_context(extracted.context_after)
        records.append(" ".join(
            part for part in (before, f"[{anchor}]({safe_href})", after) if part
        ))
        if len(records) >= bounded_limit:
            break
    return "\n".join(records), len(records)


def build_article_link_source(
    content: str,
    max_links: int = _DEFAULT_SOURCE_LINK_LIMIT,
) -> str:
    """Build a compact, bounded link source from pre-compression webpage text."""
    source, _ = build_article_link_source_with_count(content, max_links=max_links)
    return source


def count_article_links(content: str) -> int:
    """按候选构建使用的相同语法统计正文中的链接出现次数。"""
    return len({
        extracted.href.strip()
        for extracted in _extract_links(content or "")
        if extracted.href.strip()
    })


def _has_blocked_suffix(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return PurePosixPath(parsed.path).suffix.casefold() in _BLOCKED_SUFFIXES


def _is_followable_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    suffix = PurePosixPath(parsed.path).suffix.casefold()
    return suffix not in _BLOCKED_SUFFIXES


def _is_depth_one(doc: dict) -> bool:
    discovery = doc.get("discovery")
    if not isinstance(discovery, dict):
        return False
    try:
        return int(discovery.get("depth", 0)) >= 1
    except (TypeError, ValueError):
        return False


def build_article_link_candidates(
    docs: list[dict],
    existing_urls: set[str],
    attempted_urls: set[str] | None = None,
    per_parent_limit: int = 20,
    total_limit: int = 50,
    stats: ArticleLinkCandidateBuildStats | None = None,
) -> list[ArticleLinkCandidate]:
    """Build canonical, one-hop candidates from parent document content."""
    existing_canonical = {
        canonical
        for url in existing_urls
        if (canonical := canonicalize_url(str(url)))
    }
    attempted_canonical = {
        canonical
        for url in attempted_urls or set()
        if (canonical := canonicalize_url(str(url)))
    }
    candidates_by_url: dict[str, ArticleLinkCandidate] = {}
    ordered_urls: list[str] = []

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if stats is not None:
            stats.source_doc_count += 1
        if _is_depth_one(doc):
            if stats is not None:
                stats.depth_filtered_parent_count += 1
            continue
        parent_url = str(doc.get("url") or "").strip()
        sidecar = doc.get(ARTICLE_LINK_SOURCE_FIELD)
        content = (
            sidecar
            if isinstance(sidecar, str) and sidecar.strip()
            else str(doc.get("original_content") or "")
        )
        if not content.strip():
            if stats is not None:
                stats.empty_parent_count += 1
            continue
        if not _is_followable_http_url(parent_url):
            if stats is not None:
                stats.unfollowable_parent_count += 1
            continue
        parent_canonical = canonicalize_url(parent_url)
        parent_wikipedia_identity = _wikipedia_article_identity(parent_url)
        accepted_for_parent = 0
        extracted_links = _extract_links(content)
        if stats is not None:
            stats.raw_extracted_link_count += len(extracted_links)

        for extracted in extracted_links:
            resolved = urljoin(parent_url, extracted.href)
            if not _is_followable_http_url(resolved):
                if stats is not None:
                    if _has_blocked_suffix(resolved):
                        stats.blocked_suffix_count += 1
                    else:
                        stats.invalid_url_count += 1
                continue
            canonical = canonicalize_url(resolved)
            if not canonical:
                if stats is not None:
                    stats.invalid_url_count += 1
                continue
            candidate_wikipedia_identity = _wikipedia_article_identity(resolved)
            if canonical == parent_canonical or (
                parent_wikipedia_identity is not None
                and candidate_wikipedia_identity == parent_wikipedia_identity
            ):
                if stats is not None:
                    stats.self_link_filtered_count += 1
                continue
            if _is_wikipedia_system_url(resolved):
                if stats is not None:
                    stats.wikipedia_system_filtered_count += 1
                continue
            if canonical in existing_canonical:
                if stats is not None:
                    stats.existing_url_filtered_count += 1
                continue
            if canonical in attempted_canonical:
                if stats is not None:
                    stats.attempted_url_filtered_count += 1
                continue

            origin = ArticleLinkOrigin(
                parent_doc_id=str(doc.get("doc_id") or ""),
                parent_title=str(doc.get("title") or ""),
                parent_url=parent_url,
                query=str(doc.get("query") or ""),
                anchor_text=extracted.anchor_text,
            )
            existing = candidates_by_url.get(canonical)
            if existing is not None:
                if stats is not None:
                    stats.duplicate_link_count += 1
                if origin not in existing.origins:
                    existing.origins.append(origin)
                continue
            if accepted_for_parent >= per_parent_limit:
                if stats is not None:
                    stats.parent_limit_filtered_count += 1
                continue

            candidates_by_url[canonical] = ArticleLinkCandidate(
                candidate_index=-1,
                url=canonical,
                canonical_url=canonical,
                anchor_text=extracted.anchor_text,
                context_before=extracted.context_before,
                context_after=extracted.context_after,
                parent_doc_id=origin.parent_doc_id,
                parent_title=origin.parent_title,
                parent_url=origin.parent_url,
                query=origin.query,
                source_position=extracted.position,
                origins=[origin],
            )
            ordered_urls.append(canonical)
            accepted_for_parent += 1

    bounded_limit = max(0, total_limit)
    if stats is not None:
        stats.total_limit_filtered_count = max(0, len(ordered_urls) - bounded_limit)
    result = [candidates_by_url[url] for url in ordered_urls[:bounded_limit]]
    for index, candidate in enumerate(result):
        candidate.candidate_index = index
    if stats is not None:
        stats.final_candidate_count = len(result)
    return result


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _keywords(value: str) -> set[str]:
    normalized = _normalize_text(value)
    result = {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) >= 2 and token not in _STOP_WORDS
    }
    for chinese_run in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(chinese_run) == 1:
            continue
        result.add(chinese_run)
        result.update(
            chinese_run[index:index + 2]
            for index in range(len(chinese_run) - 1)
        )
    return result


def _matches_task(value: str, task_keywords: set[str]) -> bool:
    return bool(task_keywords and task_keywords.intersection(_keywords(value)))


def _contains_term(value: str, terms: set[str]) -> bool:
    normalized = _normalize_text(unquote(value))
    return any(term in normalized for term in terms)


def _hard_filtered(candidate: ArticleLinkCandidate) -> bool:
    parsed = urlparse(candidate.canonical_url)
    path = unquote(parsed.path or "")
    if path in {"", "/"}:
        return True
    path_segments = {segment.casefold() for segment in path.split("/") if segment}
    if path_segments.intersection(_NAVIGATION_PATH_SEGMENTS):
        return True
    return _contains_term(f"{candidate.anchor_text} {path}", _ACTION_TERMS)


def select_article_link_candidates(
    candidates: list[ArticleLinkCandidate],
    task_text: str,
    max_urls: int,
) -> list[RankedArticleLink]:
    """Select useful links with a hard-filter, match, and stable-sort funnel."""
    task_keywords = _keywords(task_text)
    ranked: list[tuple[tuple, RankedArticleLink]] = []
    for candidate in candidates:
        if _hard_filtered(candidate):
            continue
        candidate_task_keywords = task_keywords | _keywords(candidate.query)
        anchor_match = _matches_task(candidate.anchor_text, candidate_task_keywords)
        context_match = _matches_task(
            f"{candidate.context_before} {candidate.context_after}",
            candidate_task_keywords,
        )
        evidence_keyword = _contains_term(
            f"{candidate.anchor_text} {urlparse(candidate.canonical_url).path}",
            _EVIDENCE_TERMS,
        )
        if not (anchor_match or context_match or evidence_keyword):
            continue
        reasons = tuple(
            reason
            for matched, reason in (
                (anchor_match, "anchor_match"),
                (context_match, "context_match"),
                (evidence_keyword, "evidence_keyword"),
            )
            if matched
        )
        selected = RankedArticleLink(candidate.candidate_index, reasons)
        priority = (
            not anchor_match,
            not context_match,
            not evidence_keyword,
            candidate.source_position,
            candidate.canonical_url,
        )
        ranked.append((priority, selected))

    ranked.sort(key=lambda item: item[0])
    return [item[1] for item in ranked[:max(0, max_urls)]]
