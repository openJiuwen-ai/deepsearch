#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容风控掩码工具：普通词等长替换，命中域名的地址整体替换为占位符。

词库从 package data 的 lexicon.bin 读取并校验，可通过 lexicon_dir 指定外部文本目录覆盖。
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
from collections.abc import Iterator
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openjiuwen_deepsearch.utils.common_utils.llm_utils import llm_error_chain_blob
from openjiuwen_deepsearch.utils.common_utils.lexicon_resource import FORMAT, decode_lexicon

logger = logging.getLogger(__name__)

MASK_CHAR = "*"
URL_PLACEHOLDER = "[URL_REDACTED]"
_HAN = re.compile(r"[\u3400-\u9fff]")
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ADDRESS = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"'`，。；！？]+"
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|(?<![\w@./:-])(?:[\w-]+\.)+[\w-]{2,}"
    r"(?::[^\s<>\"'`，。；！？/?#]*)?(?:[/?#][^\s<>\"'`，。；！？]*)?",
    re.UNICODE,
)
# 只匹配明确的风控短语，避免误伤普通 input/output data 错误
_MODERATION_INPUT_PHRASE = "input data may contain inappropriate"
_MODERATION_OUTPUT_PHRASE = "output data may contain inappropriate"
_MODERATION_PHRASES = (
    _MODERATION_INPUT_PHRASE,
    _MODERATION_OUTPUT_PHRASE,
    "data_inspection_failed",
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

_LEXICON_DIR_RESOURCE = "openjiuwen_deepsearch.utils.common_utils.data.sensitive_lexicon"


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


@lru_cache(maxsize=4)
def _load_lexicon_dir(path: str) -> frozenset[str]:
    """从外部目录加载词库（按路径缓存）。"""
    p = Path(path)
    if not p.is_dir():
        logger.warning("词库目录不存在或不是目录: %s", path)
        return frozenset()
    terms: set[str] = set()
    for f in sorted(p.glob("*.txt")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.strip()
            if t and not t.startswith("#") and len(t) >= 2:
                terms.add(t)
    return frozenset(terms)


def get_lexicon(lexicon_dir: str | None = None) -> frozenset[str]:
    """获取词库。lexicon_dir 非空则用外部目录；目录无效或为空时回退到打包词库。"""
    if lexicon_dir:
        terms = _load_lexicon_dir(lexicon_dir)
        if terms:
            return terms
        logger.warning("外部词库目录无效或为空，回退到打包词库: %s", lexicon_dir)
    return _load_lexicon_bundled()


def _moderation_blob(detail: str, cause: BaseException | None) -> str:
    """把 detail 与异常链拼成一段小写文本，供风控短语匹配。"""
    blob = detail or ""
    if cause is not None:
        blob = f"{blob}\n{llm_error_chain_blob(cause)}"
    return blob.lower()


def is_moderation_error(detail: str, cause: BaseException | None = None) -> bool:
    """判断异常是否内容风控 400（Input/Output data inappropriate / data_inspection_failed）。

    复用 llm_error_chain_blob 遍历异常链，与 _is_context_limit_error 保持一致的覆盖方式。
    """
    if not detail and cause is None:
        return False
    lower = _moderation_blob(detail, cause)
    return any(p in lower for p in _MODERATION_PHRASES)


def moderation_error_side(detail: str, cause: BaseException | None = None) -> str:
    """判断风控 400 发生在哪一侧：`SIDE_INPUT` / `SIDE_OUTPUT`，判不出则返回 "" 。

    用于分流重试策略——输入侧的内容是我们发出去的文本，掩码能直接移除触发内容；
    输出侧被拦的是模型现场生成的内容（我们从未收到），掩码够不着，只能追加提示约束生成。
    `data_inspection_failed` 不带侧信息，返回 "" 由调用方决定兜底走向。

    输出侧优先判断：平台的输出侧错误串里也可能带 input 字样（如 "input id"），
    先命中更长更明确的 output 串可避免误判。
    """
    if not detail and cause is None:
        return ""
    lower = _moderation_blob(detail, cause)
    if _MODERATION_OUTPUT_PHRASE in lower:
        return SIDE_OUTPUT
    if _MODERATION_INPUT_PHRASE in lower:
        return SIDE_INPUT
    return ""


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
def _compiled_pattern(terms: frozenset[str]) -> tuple[re.Pattern, frozenset[str]]:
    """Cache separate text rules and normalized domain rules."""
    domains = set()
    alternatives = []
    skipped = 0
    for term in sorted((t for t in terms if t), key=lambda t: (-len(t), t)):
        domain = _address_domain(term)
        if domain:
            parsed = urlsplit(term if "://" in term else "//" + term)
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username:
                skipped += 1
            else:
                domains.add(domain)
            continue
        if "://" in term:
            skipped += 1
            continue
        escaped = re.escape(term)
        if _is_english_term(term):
            escaped = r"(?<![A-Za-z0-9_])(?ai:" + escaped + r")(?![A-Za-z0-9_])"
        alternatives.append(escaped)
    logger.info(
        "moderation matchers text_terms=%d domains=%d skipped_url_terms=%d",
        len(alternatives), len(domains), skipped,
    )
    return re.compile("|".join(alternatives) if alternatives else r"(?!)"), frozenset(domains)


def _is_english_term(term: str) -> bool:
    return not _HAN.search(term) and bool(re.search(r"[A-Za-z]", term))


def _text_replacement(match: re.Match) -> tuple[int, int, str]:
    return match.start(), match.end(), MASK_CHAR * (match.end() - match.start())


def _address_domain(address: str) -> str:
    """Parse without network access; invalid/unsupported addresses stay unchanged."""
    try:
        parsed = urlsplit(address if "://" in address else "//" + address)
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


def _address_candidates(text: str) -> Iterator[tuple[int, int, str]]:
    cursor = 0
    while match := _ADDRESS.search(text, cursor):
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
    pat, domains = _compiled_pattern(terms)
    replacements = []
    cursor = 0
    for start, end, address in _address_candidates(s):
        replacements.extend(
            _text_replacement(m)
            for m in pat.finditer(s, cursor, start)
        )
        host = _address_domain(address)
        if host and _domain_matches(host, domains):
            replacements.append((start, end, URL_PLACEHOLDER))
        # All address candidates, including invalid addresses and emails, are protected.
        cursor = end
    replacements.extend(
        _text_replacement(m)
        for m in pat.finditer(s, cursor)
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
    lexicon_dir: str | None = None,
) -> tuple[list[Any], int, int]:
    """对 messages 掩码。level=1 只掩 user，level=2 掩 user+system。
    返回新的 messages 列表（不原地改）+ 掩码字符数 + 区间数。
    """
    terms = get_lexicon(lexicon_dir)
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
