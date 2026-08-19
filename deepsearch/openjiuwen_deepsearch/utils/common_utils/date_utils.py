# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""网页文档日期的提取与时效判定。

日期与约束都归一化为闭区间,用区间包含关系判定 compliant/violation/unknown;粒度不足(只有年/月)时归 unknown 不猜。
提取只走白名单(标准 meta/JSON-LD/URL 模式),不做全文扫描。
纯日期工具,不依赖 framework 层。
"""

import calendar
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Optional

Granularity = Literal["year", "month", "day"]
Confidence = Literal["high", "medium", "low"]
TemporalStatus = Literal["compliant", "violation", "unknown"]

MIN_PLAUSIBLE_DATE = date(1990, 1, 1)
FUTURE_TOLERANCE_DAYS = 2

# ---------------------------------------------------------------------------
# 日期字符串解析
# ---------------------------------------------------------------------------

# RFC 2822 与 ISO 8601 之外的常见字面格式(精确到日)。
_EXTRA_DATE_FORMATS = (
    "%b %d, %Y",    # Jan 1, 2024
    "%B %d, %Y",    # January 1, 2024
    "%d %b %Y",     # 1 Jan 2024
    "%d %B %Y",     # 1 January 2024
    "%Y %b %d",     # 2024 Jan 1
    "%Y %B %d",     # 2024 January 1
    "%Y/%m/%d",     # 2024/01/01
    "%Y年%m月%d日",  # 2024年1月1日
)

_MONTH_FORMATS = (
    "%Y-%m",        # 2024-03
    "%Y/%m",        # 2024/03
    "%Y年%m月",      # 2024年3月
    "%b %Y",        # Mar 2024
    "%B %Y",        # March 2024
    "%Y %b",        # 2024 Mar(PubMed 风格)
    "%Y %B",        # 2024 March
)


def parse_date_string(value: Any) -> Optional[date]:
    """把日期字符串解析为 date,带时区的一律换算到 UTC;失败返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None

    parsed = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None

    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None

    if parsed is None:
        for fmt in _EXTRA_DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


def parse_partial_date(value: Any) -> Optional[tuple[date, Granularity]]:
    """解析可能只有年/月粒度的日期字符串。

    Returns:
        (代表日期, 粒度);完全无法解析时返回 None。
        代表日期取区间的第一天,真实区间由 to_interval() 按粒度展开。
    """
    text = str(value or "").strip()
    if not text:
        return None

    exact = parse_date_string(text)
    if exact is not None:
        return exact, "day"

    for fmt in _MONTH_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date().replace(day=1), "month"
        except ValueError:
            continue

    if re.fullmatch(r"(19|20)\d{2}", text):
        return date(int(text), 1, 1), "year"

    return None


def to_interval(day: date, granularity: Granularity) -> tuple[date, date]:
    """按粒度把代表日期展开为闭区间 [lo, hi]。"""
    if granularity == "day":
        return day, day
    if granularity == "month":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.replace(day=1), day.replace(day=last)
    return date(day.year, 1, 1), date(day.year, 12, 31)


# ---------------------------------------------------------------------------
# DocDate 四元组
# ---------------------------------------------------------------------------

@dataclass
class DocDate:
    """文档日期四元组。

    Attributes:
        day: 代表日期(区间第一天),真实范围由 granularity 决定。
        granularity: 粒度,year/month/day。
        confidence: 置信度,high=白名单 meta/JSON-LD/引擎元数据,
            medium=URL 模式/修改时间类标签,low=LLM 推断等弱信号。
        source: 来源标识,如 tavily/url/html_meta:article:published_time/llm_inferred。
    """

    day: date
    granularity: Granularity
    confidence: Confidence
    source: str = ""

    def interval(self) -> tuple[date, date]:
        return to_interval(self.day, self.granularity)

    def to_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "granularity": self.granularity,
            "confidence": self.confidence,
            "source": self.source,
        }


def is_plausible(doc_date: DocDate, reference_date: Optional[date] = None) -> bool:
    """合理性校验:拒绝过老或显著未来的日期。

    未来容忍 FUTURE_TOLERANCE_DAYS 天以吸收时区/时钟偏差;超出的(典型如页面"今天"控件、动态时间戳)拒绝。
    """
    ref = reference_date or datetime.now(tz=timezone.utc).date()
    lo, hi = doc_date.interval()
    if hi < MIN_PLAUSIBLE_DATE:
        return False
    if lo > ref + timedelta(days=FUTURE_TOLERANCE_DAYS):
        return False
    return True


# ---------------------------------------------------------------------------
# 时效状态判定(区间包含)
# ---------------------------------------------------------------------------

def temporal_status(
        doc_date: Optional[DocDate],
        start_date: Optional[date],
        end_date: Optional[date],
) -> TemporalStatus:
    """用区间包含关系判定文档相对约束的时效状态。

    - hi < start 或 lo > end:整个可能区间都在约束外 → violation;
    - lo >= start 且 hi <= end:整个可能区间都在约束内 → compliant;
    - 其余(区间交叠但说不清,如年粒度跨年界) → unknown,不猜。
    """
    if doc_date is None or (start_date is None and end_date is None):
        return "unknown"
    lo, hi = doc_date.interval()
    if start_date is not None and hi < start_date:
        return "violation"
    if end_date is not None and lo > end_date:
        return "violation"
    if (start_date is None or lo >= start_date) and (end_date is None or hi <= end_date):
        return "compliant"
    return "unknown"


def timeliness_score(status: TemporalStatus, confidence: Confidence) -> float:
    """时效状态 + 置信度 → 排序分。
    unknown 中性(0);compliant 有界奖励;violation 有界惩罚(仅非 high 置信会走到这里,high 置信 violation 已被硬删)。
    """
    if status == "unknown":
        return 0.0
    if status == "compliant":
        return 1.0 if confidence == "high" else 0.5
    return -1.0 if confidence == "high" else -0.5


# ---------------------------------------------------------------------------
# URL 日期提取(零成本档)
# ---------------------------------------------------------------------------

_URL_DATE_PATTERNS: tuple[tuple[re.Pattern, Granularity], ...] = (
    (re.compile(r"/((?:19|20)\d{2})-(\d{2})-(\d{2})[/._-]"), "day"),      # /2024-03-15/
    (re.compile(r"/((?:19|20)\d{2})/(\d{1,2})/(\d{1,2})/"), "day"),       # /2024/03/15/
    (re.compile(r"/((?:19|20)\d{2})(\d{2})(\d{2})[/._-]"), "day"),        # /20240315/
    (re.compile(r"/((?:19|20)\d{2})/(\d{1,2})/"), "month"),               # /2024/03/
)


def extract_url_date(url: str, reference_date: Optional[date] = None) -> Optional[DocDate]:
    """从 URL 路径提取日期模式。

    只匹配路径段形态的日期(带分隔符),降低误伤;置信度 medium。
    年主题词(如 /best-laptops-2024)不匹配这些模式,天然排除。
    """
    path = re.sub(r"^https?://[^/]+", "", str(url or ""))
    for pattern, granularity in _URL_DATE_PATTERNS:
        m = pattern.search(path)
        if not m:
            continue
        try:
            year, month = int(m.group(1)), int(m.group(2))
            day = int(m.group(3)) if granularity == "day" else 1
            candidate = DocDate(
                day=date(year, month, day),
                granularity=granularity,
                confidence="medium",
                source="url",
            )
        except ValueError:
            continue
        if is_plausible(candidate, reference_date):
            return candidate
    return None


# ---------------------------------------------------------------------------
# HTML <head> 白名单日期提取(顺带档)
# ---------------------------------------------------------------------------

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*[\"']([^\"']*)[\"']")
_JSONLD_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

# 白名单:发布时间语义(OG / schema.org / Dublin Core / 学术 prism·eprints·citation_*)。
_WHITELIST_PUBLISHED = (
    "article:published_time",
    "og:published_time",
    "citation_publication_date",
    "citation_online_date",
    "dc.date",
    "dc.date.issued",
    "dcterms.issued",
    "dcterms.date",
    "prism.publicationdate",
    "prism.coverdate",
    "eprints.date",
)
# 白名单:修改/更新时间语义(优先级次之,与发布时间冲突时让位)。
_WHITELIST_MODIFIED = (
    "article:modified_time",
    "og:updated_time",
    "dcterms.modified",
    "prism.modificationdate",
)

# JSON-LD 中只信任这些主实体的日期;Comment/BreadcrumbList 等嵌套实体不收。
_JSONLD_ARTICLE_TYPES = {
    "article", "newsarticle", "blogposting", "techarticle",
    "scholarlyarticle", "report", "webpage",
}


def _parse_meta_tags(html: str) -> list[tuple[str, str]]:
    """解析 <meta> 标签,返回 (小写键名, content) 列表。"""
    pairs = []
    for tag in _META_TAG_RE.findall(html):
        attrs = dict(_META_ATTR_RE.findall(tag))
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or ""
        content = attrs.get("content", "")
        if key and content:
            pairs.append((key.lower(), content.strip()))
    return pairs


def _walk_jsonld(obj: Any, found: list[tuple[str, str]]) -> None:
    """递归收集 JSON-LD 主实体的 datePublished/dateModified。"""
    if isinstance(obj, dict):
        node_type = str(obj.get("@type", "")).lower()
        if node_type in _JSONLD_ARTICLE_TYPES:
            for key, rank in (("datePublished", "published"), ("dateModified", "modified")):
                value = obj.get(key)
                if isinstance(value, str) and parse_date_string(value) is not None:
                    found.append((f"jsonld:{rank}", value.strip()))
        for value in obj.values():
            _walk_jsonld(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_jsonld(item, found)


def extract_html_head_date(
        html: str,
        reference_date: Optional[date] = None,
) -> Optional[DocDate]:
    """从 HTML(通常只传 <head>)提取发布日期,白名单 + 冲突处理。

    优先级:发布语义标签 > JSON-LD datePublished > 修改语义标签 > JSON-LD dateModified。
    同优先级候选解析出不同日期 → None(不猜)。所有候选过 is_plausible 校验。
    """
    if not html:
        return None
    head = html[:200_000]

    ranked: list[tuple[int, str, str]] = []  # (优先级, 来源标识, 原始值)
    for key, content in _parse_meta_tags(head):
        if key in _WHITELIST_PUBLISHED:
            ranked.append((0, f"html_meta:{key}", content))
        elif key in _WHITELIST_MODIFIED:
            ranked.append((2, f"html_meta:{key}", content))

    jsonld_found: list[tuple[str, str]] = []
    for m in _JSONLD_RE.finditer(head):
        block = m.group(1).strip()
        try:
            _walk_jsonld(json.loads(block), jsonld_found)
        except (ValueError, TypeError):
            continue
    for source, value in jsonld_found:
        ranked.append((1 if source.endswith("published") else 3, source, value))

    best_rank = None
    best: list[DocDate] = []
    for rank, source, raw in ranked:
        parsed = parse_date_string(raw)
        if parsed is None:
            continue
        candidate = DocDate(day=parsed, granularity="day", confidence="high", source=source)
        if not is_plausible(candidate, reference_date):
            continue
        if best_rank is None or rank < best_rank:
            best_rank, best = rank, [candidate]
        elif rank == best_rank:
            best.append(candidate)

    if not best:
        return None
    distinct = {c.day for c in best}
    if len(distinct) > 1:
        return None
    return best[0]


# ---------------------------------------------------------------------------
# 多来源合并
# ---------------------------------------------------------------------------

_CONFIDENCE_RANK: dict[Confidence, int] = {"high": 0, "medium": 1, "low": 2}


def merge_doc_dates(candidates: list[Optional[DocDate]]) -> Optional[DocDate]:
    """多来源合并:取最高置信档;同档日期互相矛盾(跨粒度按最粗粒度比较)→ None。"""
    valid = [c for c in candidates if c is not None]
    if not valid:
        return None
    best_rank = min(_CONFIDENCE_RANK[c.confidence] for c in valid)
    best = [c for c in valid if _CONFIDENCE_RANK[c.confidence] == best_rank]
    coarsest: Granularity = "day"
    for c in best:
        if c.granularity == "year":
            coarsest = "year"
            break
        if c.granularity == "month":
            coarsest = "month"
    keys = set()
    for c in best:
        lo, _ = c.interval()
        if coarsest == "year":
            keys.add((lo.year,))
        elif coarsest == "month":
            keys.add((lo.year, lo.month))
        else:
            keys.add((lo.year, lo.month, lo.day))
    if len(keys) > 1:
        return None
    return best[0]
