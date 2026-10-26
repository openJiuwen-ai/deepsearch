#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容风控掩码工具：普通词等长替换，命中域名的地址整体替换为占位符。

词库仅从 package data 的 lexicon.bin 读取并校验。
首次需要输入掩码时才加载，解析结果和匹配器使用缓存。

第三方词库归属与许可：
    敏感词库源自 Sensitive-lexicon 项目，Copyright (c) 2024~2099 Konsheng，采用 MIT License。
    详见打包目录下随附的 LICENSE 文件；使用、修改、分发须保留该版权与许可声明。
    完整来源与归属说明见 docs/feature/moderation-mask-retry.md。
"""
from __future__ import annotations
import logging
import json
import re
import string
import time
from collections.abc import Iterator
from functools import lru_cache
from importlib import resources
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from openjiuwen_deepsearch.utils.moderation.detection import detect_error
from openjiuwen_deepsearch.utils.moderation.lexicon_resource import FORMAT, decode_lexicon
from openjiuwen_deepsearch.utils.moderation.content import MaskableText
from openjiuwen_deepsearch.utils.moderation.matcher import TextMatcher

logger = logging.getLogger(__name__)

MASK_CHAR = "*"
URL_PLACEHOLDER = "[URL_REDACTED]"
_HAN = re.compile(r"[\u3400-\u9fff]")
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+.-")
_ADDRESS = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"'`，。；！？]+"
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|(?<![\w@./:-])(?:[\w-]+\.)+[\w-]{2,}"
    r"(?::[^\s<>\"'`，。；！？/?#]*)?(?:[/?#][^\s<>\"'`，。；！？]*)?",
    re.UNICODE,
)
# 侧向常量：风控发生在输入侧 / 输出侧 / 判不出侧
SIDE_INPUT = "input"
SIDE_OUTPUT = "output"

# 输出侧被拦后追加的重试提示。措辞刻意中立——它本身也要过输入侧检查，
# 不能自带敏感词；且只描述"该怎么做"，不描述"哪些词不能用"（平台词库不可见）。
RETRY_FEEDBACK_PROMPT = (
    "【重试提示】你上一次的生成被平台内容安全策略拦截，未能返回。"
    "请重新生成同一份内容，并注意：\n"
    "1. 使用中性、概括、克制的表述，避免具体直白的敏感描写；\n"
    "2. 避免可能触发内容审核的措辞与称谓；\n"
    "3. 保持事实准确、信息完整，不要因此省略必要内容。"
)

_LEXICON_DIR_RESOURCE = "openjiuwen_deepsearch.utils.moderation.data.sensitive_lexicon"


@lru_cache(maxsize=1)
def _load_lexicon_bundled() -> frozenset[str]:
    """Load the versioned binary resource once, checking checksum and count."""
    try:
        root = resources.files(_LEXICON_DIR_RESOURCE)
        manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ValueError("Unsupported lexicon manifest")
        count = manifest.get("term_count")
        checksum = manifest.get("sha256")
        if type(count) is not int or count < 1 or not isinstance(checksum, str):
            raise ValueError("Invalid lexicon manifest")
        data = root.joinpath("lexicon.bin").read_bytes()
        terms = decode_lexicon(data, checksum)
        if len(terms) != count:
            raise ValueError("Lexicon term count mismatch")
        return terms
    except Exception as e:
        logger.warning("加载打包词库失败，跳过本次输入掩码: %s", type(e).__name__)
        return frozenset()


def get_lexicon() -> frozenset[str]:
    """获取经构建清理并校验的内置词库。"""
    return _load_lexicon_bundled()


def is_moderation_error(detail: str, cause: BaseException | None = None) -> bool:
    """Compatibility entry point for normalized moderation detection."""
    return detect_error(cause, detail) is not None


def moderation_error_side(detail: str, cause: BaseException | None = None) -> str:
    """Return explicit input/output side; preserve the legacy empty unknown value."""
    signal = detect_error(cause, detail)
    return signal.side if signal is not None and signal.side != "unknown" else ""


def append_retry_feedback(
    messages: list[Any],
    prompt: str = RETRY_FEEDBACK_PROMPT,
) -> list[Any]:
    """在 messages 末尾追加一条 user 提示，用于输出侧被拦后的重试。

    返回新列表，不修改入参。追加而非改写：system 提示模板往往是各节点精心构造的，
    动它风险大；追加一条 user 消息对所有 provider 都最兼容。
    """
    return [*messages, {"role": "user", "content": prompt}]


@lru_cache(maxsize=8)
def _compiled_matchers(terms: frozenset[str]) -> tuple[TextMatcher, frozenset[str]]:
    """Cache separate text rules and normalized domain rules."""
    domains = set()
    matcher = TextMatcher()
    text_terms = 0
    skipped = 0
    ordered_terms = sorted((term for term in terms if term), key=lambda term: (-len(term), term))
    for term_index, term in enumerate(ordered_terms):
        if term_index and term_index % 1024 == 0:
            # Matcher compilation runs in a worker thread on async request
            # paths. Yield the GIL periodically so the event loop remains
            # responsive even when the process is CPU constrained.
            time.sleep(0.001)
        domain = _address_domain(term)
        if domain:
            parsed = _parse_address(term)
            has_extra_components = any((parsed.query, parsed.fragment, parsed.username))
            if parsed.path not in ("", "/") or has_extra_components:
                skipped += 1
            else:
                domains.add(domain)
            continue
        if "://" in term:
            skipped += 1
            continue
        matcher.add(term, english=_is_english_term(term))
        text_terms += 1
    logger.info(
        "moderation matchers text_terms=%d domains=%d skipped_url_terms=%d",
        text_terms, len(domains), skipped,
    )
    return matcher, frozenset(domains)


def _is_english_term(term: str) -> bool:
    return not _HAN.search(term) and bool(re.search(r"[A-Za-z]", term))


def _text_replacement(span: tuple[int, int]) -> tuple[int, int, str]:
    start, end = span
    return start, end, MASK_CHAR * (end - start)


def _parse_address(address: str) -> SplitResult:
    if "://" not in address:
        address = urlunsplit(("", address, "", "", ""))
    return urlsplit(address)


def _address_domain(address: str) -> str:
    """Parse without network access; invalid/unsupported addresses stay unchanged."""
    try:
        parsed = _parse_address(address)
        if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        # Accessing port validates malformed ports as well.
        if parsed.port is not None and not 0 < parsed.port <= 65535:
            return ""
        labels = host.split(".")
        if len(host) > 253 or len(labels) < 2 or not all(_DOMAIN_LABEL.fullmatch(x) for x in labels):
            return ""
        if not re.fullmatch(r"[a-z]{2,}|xn--[a-z0-9-]+", labels[-1]):
            return ""
        return host
    except (ValueError, UnicodeError):
        return ""


def _trim_address(address: str) -> str:
    opening_for = {")": "(", "]": "[", "}": "{"}
    stack = []
    for index, char in enumerate(address):
        if char in "([{":
            stack.append(char)
        elif char in opening_for:
            if not stack or stack.pop() != opening_for[char]:
                address = address[:index]
                break
    separator = re.search(r"[,;](?=(?:https?://|www\.))", address, re.IGNORECASE)
    if separator:
        address = address[:separator.start()]
    return address.rstrip(".,;:!?")


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _address_starts(text: str) -> list[int]:
    """Locate possible starts in Python before applying the anchored URL regex.

    Unanchored email/scheme alternatives otherwise retry long prefixes at every
    character, holding the GIL even when the surrounding work is in a thread.
    """
    starts = set()
    scheme_start = email_start = label_start = None
    for index, char in enumerate(text):
        word = _is_word_char(char)
        if char in _SCHEME_CHARS:
            if scheme_start is None and char in string.ascii_letters:
                scheme_start = index
        else:
            if scheme_start is not None and text.startswith("://", index):
                starts.add(scheme_start)
            scheme_start = None
        if word or char in ".+-":
            if email_start is None:
                email_start = index
        else:
            if char == "@" and email_start is not None:
                starts.add(email_start)
            email_start = None
        if word or char == "-":
            if label_start is None:
                label_start = index
        else:
            if char == "." and label_start is not None:
                previous = text[label_start - 1] if label_start else ""
                if not previous or not (_is_word_char(previous) or previous in "@./:-"):
                    starts.add(label_start)
            label_start = None
    return sorted(starts)


def _address_candidates(text: str) -> Iterator[tuple[int, int, str]]:
    cursor = 0
    for start in _address_starts(text):
        if start < cursor:
            continue
        match = _ADDRESS.match(text, start)
        if match is None:
            continue
        address = _trim_address(match.group())
        end = match.start() + len(address)
        yield match.start(), end, address
        # A trimmed Markdown delimiter may be followed by another address.
        cursor = max(end, match.start() + 1)


def _domain_matches(host: str, domains: frozenset[str]) -> bool:
    labels = host.split(".")
    return any(".".join(labels[i:]) in domains for i in range(len(labels) - 1))


def mask_text(s: str, terms: frozenset[str]) -> tuple[str, int, int]:
    """Return masked text, replaced source-character count, and merged span count.

    Text hits preserve length; complete address replacements may change length.
    """
    if not s or not terms:
        return s, 0, 0
    pat, domains = _compiled_matchers(terms)
    replacements = []
    cursor = 0
    for start, end, address in _address_candidates(s):
        replacements.extend(
            _text_replacement(m)
            for m in pat.find_spans(s, cursor, start)
        )
        host = _address_domain(address)
        if host and _domain_matches(host, domains):
            replacements.append((start, end, URL_PLACEHOLDER))
        # All address candidates, including invalid addresses and emails, are protected.
        cursor = end
    replacements.extend(
        _text_replacement(m)
        for m in pat.find_spans(s, cursor)
    )
    if not replacements:
        return s, 0, 0
    parts = []
    cursor = masked_chars = span_count = 0
    last_end = -1
    for start, end, replacement in replacements:
        parts.extend((s[cursor:start], replacement))
        masked_chars += end - start
        span_count += start != last_end
        cursor = last_end = end
    parts.append(s[cursor:])
    return "".join(parts), masked_chars, span_count


def mask_messages(
    messages: list[Any],
    level: int,
    *,
    evidence_only: bool = False,
) -> tuple[list[Any], int, int]:
    """对 messages 掩码。level=1 只掩 user，level=2 掩 user+system。
    返回新的 messages 列表（不原地改）+ 掩码字符数 + 区间数。
    生产补救使用 evidence_only：仅处理带资料范围的 user 字符串，不猜测原始文本结构。
    """
    if evidence_only and not any(
        (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
        and isinstance(m.get("content") if isinstance(m, dict) else getattr(m, "content", None), MaskableText)
        for m in messages
    ):
        logger.info("moderation-mask-skipped reason=no_evidence_scope")
        return messages, 0, 0
    terms = get_lexicon()
    if not terms:
        return messages, 0, 0
    new_msgs: list[Any] = []
    total_chars = 0
    total_spans = 0
    target_roles = {"user"} if level == 1 else {"user", "system"}
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if role in target_roles and isinstance(content, str) and content:
            if evidence_only:
                masked, nchars, nspans = content, 0, 0
                if role == "user" and isinstance(content, MaskableText):
                    try:
                        masked, nchars, nspans = content.mask(terms)
                    except Exception as error:
                        logger.warning("资料范围无法重建，保留原消息: %s", type(error).__name__)
            else:
                masked, nchars, nspans = mask_text(content, terms)
            total_chars += nchars
            total_spans += nspans
            new_msgs.append(
                {**m, "content": masked} if isinstance(m, dict)
                else m.model_copy(update={"content": masked})
            )
        else:
            new_msgs.append(dict(m) if isinstance(m, dict) else m.model_copy())
    return new_msgs, total_chars, total_spans
