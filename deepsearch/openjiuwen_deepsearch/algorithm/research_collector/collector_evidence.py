# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import functools
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


from openjiuwen_deepsearch.common.common_constants import MAX_COLLECTOR_DOC_CONTENT_LENGTH

logger = logging.getLogger(__name__)

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"spm", "from", "source", "ref", "fbclid", "gclid"}
MAX_PASSAGE_LENGTH = 500
DEFAULT_KEY_PASSAGE_COUNT = 5
#: CJK 统一表意文字，用于判断句子间是否需要空格分隔。
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def _join_sentences(sentences: list[str]) -> str:
    """CJK 句子间无空格，拉丁文句子间需空格分隔。"""
    if all(_CJK_RE.search(s) for s in sentences):
        return "".join(sentences)
    return " ".join(sentences)


# ---------------------------------------------------------------------------
# 数字事实模式（key_passages 与 Coverage 共用）：单位分类 + 前置货币符号 +
# 学术统计。从 extract_key_passages 内部提为模块级共享常量，避免两处各自维护。
# ---------------------------------------------------------------------------
_CURRENCY_UNITS = [
    "美元", "人民币", "元", "欧元", "日元", "英镑", "港元", "港币",
    "韩元", "新台币", "澳元", "加元", "卢比", "卢布", "比索", "泰铢",
    "新加坡元", "瑞士法郎", "USD", "RMB", "CNY", "JPY", "EUR", "GBP",
    "yuan", "yen", "rupee",
]
# 长词在前：该表拼进 `_SUFFIX_UNIT_PATTERN` 的正则交替分支，按位置短路匹配，
# "万亿" 若排在 "万" 之后永远不可达（"万" 抢先命中，量级被截断成 10^4）。
_LARGE_NUMBER_UNITS = ["万亿", "千万", "百万", "十万", "兆", "亿", "万"]
_PERCENT_UNITS = ["%", "％", "‰", "个百分点", "pp", "bp", "bps", "个基点"]
_TIME_UNITS = ["年", "月", "日", "季度", "周", "天", "小时", "分钟", "秒", "时"]
_COUNT_UNITS = [
    "个", "件", "人", "次", "台", "家", "只", "条", "座", "栋", "架",
    "辆", "艘", "起", "宗", "例", "份", "批", "轮", "届", "场", "间", "户", "名",
]
_LENGTH_UNITS = [
    "公里", "千米", "米", "厘米", "毫米", "微米", "纳米", "英里", "英尺", "英寸",
    "km", "cm", "mm", "μm", "nm",
]
_WEIGHT_UNITS = ["吨", "千克", "公斤", "克", "毫克", "磅", "盎司", "kg", "mg", "lb"]
_VOLUME_UNITS = ["升", "毫升", "立方米", "立方厘米", "加仑", "桶", "mL", "ml", "L"]
_TECH_UNITS = [
    "TB", "GB", "MB", "KB", "PB", "bps", "Mbps", "Gbps", "Hz", "MHz", "GHz",
    "核", "线程", "FLOPS", "TFLOPS", "PFLOPS", "QPS", "ms",
]
_ELECTRICAL_UNITS = [
    "W", "kW", "MW", "GW", "度", "kWh", "V", "kV", "A", "mA", "Ω", "瓦", "千瓦",
]
_MEDICAL_UNITS = [
    "mg", "ml", "mmHg", "mol", "mmol", "IU", "℃", "mg/L", "mg/dL", "mIU/mL", "μg", "ng",
]
_ACADEMIC_UNITS = ["篇", "卷", "期", "页", "章", "节", "组"]

_ALL_SUFFIX_UNITS = (
    _CURRENCY_UNITS + _LARGE_NUMBER_UNITS + _PERCENT_UNITS + _TIME_UNITS
    + _COUNT_UNITS + _LENGTH_UNITS + _WEIGHT_UNITS + _VOLUME_UNITS
    + _TECH_UNITS + _ELECTRICAL_UNITS + _MEDICAL_UNITS + _ACADEMIC_UNITS
)
_SUFFIX_UNIT_PATTERN = "|".join(re.escape(unit) for unit in dict.fromkeys(_ALL_SUFFIX_UNITS))

# 前置货币符号：数字前出现 $/¥/€/£/₩/₹/₽ 这类符号（如 "$100 million"、"¥5000"）。
_PREFIX_CURRENCY_SYMBOLS = r"[$¥€£₩₹₽]"

# 学术/统计类表达：不是"数字+单位"结构，需要单独写模式。
_ACADEMIC_STAT_PATTERNS = [
    r"[pP]\s*[<>=]\s*0?\.\d+",  # p<0.05, P=0.01
    r"[nN]\s*=\s*\d+",  # n=1000, N=50（样本量）
    r"[rR]\s*=\s*-?0?\.\d+",  # r=0.85（相关系数）
    r"R\s*[²2]\s*=\s*0?\.\d+",  # R²=0.76, R2=0.76
    r"95\s*%?\s*CI",  # 95% CI（置信区间）
    r"±\s*\d+(?:\.\d+)?",  # ±1.5（标准差/误差范围）
    r"[αα]\s*=\s*0?\.\d+",  # α=0.05（显著性水平）
]


@dataclass
class CollectorSourceStore:
    """保存 collector 子图内可回查的原始正文。

    Attributes:
        contents: source_id 到正文片段的映射；Phase 1 仅作为 session 内临时存储。
    """

    contents: dict[str, str] = field(default_factory=dict)

    def write(self, source_id: str, content: str) -> bool:
        """写入原始正文。

        Args:
            source_id: 证据片段稳定 ID。
            content: 原始正文。

        Returns:
            写入或已存在可回查内容时返回 True；输入无效时返回 False。
        """
        if not source_id:
            return False
        normalized_content = content or ""
        if source_id in self.contents:
            existing_key = normalize_content_for_dedup(self.contents[source_id])
            incoming_key = normalize_content_for_dedup(normalized_content)
            if existing_key != incoming_key:
                logger.warning(
                    "[CollectorEvidence] source_store source_id conflict. source_id=%s | keeping first content.",
                    source_id,
                )
            return True
        self.contents[source_id] = normalized_content
        return True

    def read(self, source_id: str) -> str | None:
        """按 source_id 读取原始正文。

        Args:
            source_id: 证据片段稳定 ID。

        Returns:
            找到时返回正文；不存在时返回 None。
        """
        return self.contents.get(source_id)

    def to_dict(self) -> dict[str, str]:
        """导出可写入 session state 的字典。

        Returns:
            source_id 到正文的映射副本。
        """
        return dict(self.contents)

    @classmethod
    def from_dict(cls, value: dict | None) -> "CollectorSourceStore":
        """从 session state 字典恢复 source store。

        Args:
            value: session 中保存的 source store 字典。

        Returns:
            CollectorSourceStore 实例。
        """
        if not isinstance(value, dict):
            return cls()
        return cls(contents={str(key): str(content or "") for key, content in value.items()})


def _short_hash(value: str) -> str:
    """生成短 hash，避免把 URL 或本地文件 ID 暴露到 doc_id。

    Args:
        value: 待哈希的稳定身份字符串。

    Returns:
        16 位十六进制短 hash。
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def normalize_content_for_dedup(content: Any) -> str:
    """正文去重前的统一规范化。

    Args:
        content: 原始正文，允许传入 None 或非字符串值。

    Returns:
        经过 NFKC、换行和连续空白归一化后的正文。
    """

    normalized = unicodedata.normalize("NFKC", str(content or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def build_content_dedup_hash(content: Any) -> str:
    """生成 collector/report 共用的正文去重 hash。

    Args:
        content: 原始正文。

    Returns:
        归一化正文的 SHA256 hash。
    """

    normalized = normalize_content_for_dedup(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonicalize_url(url: str) -> str:
    """归一化 URL，去掉常见跟踪参数。

    Args:
        url: 原始 URL。

    Returns:
        归一化后的 URL；无法解析时返回原值。
    """
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    kept_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS or lower_key.startswith(TRACKING_QUERY_PREFIXES):
            continue
        kept_query.append((key, value))
    kept_query = sorted(kept_query)
    normalized_path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            normalized_path,
            urlencode(kept_query),
            "",
        )
    )


def generate_doc_id(url: str, title: str, source_type: str = "web") -> str:
    """生成原始文档稳定 ID。

    Args:
        url: 文档 URL 或 localdataset URL。
        title: 文档标题。
        source_type: 来源类型，常见值为 web 或 local。

    Returns:
        带来源前缀的稳定 doc_id。
    """
    prefix = "local" if source_type == "local" or str(url).startswith("localdataset://") else "web"
    identity = canonicalize_url(url) or f"{title}|{source_type}"
    return f"{prefix}_{_short_hash(identity)}"


def generate_source_id(
    doc_id: str,
    passage_index: int | None = None,
    content: str | None = None,
) -> str:
    """生成 evidence/citation 身份 ID。

    Args:
        doc_id: 原始文档稳定 ID。
        passage_index: 显式片段序号；提供时优先用于生成稳定 source_id。
        content: 证据片段正文；同一 doc_id 下不同 content 会生成不同 source_id。

    Returns:
        source_id。未提供片段信息时保持兼容，默认等于 doc_id。
    """
    if passage_index is not None:
        return f"{doc_id}_p{passage_index}"
    normalized_content = normalize_content_for_dedup(content)
    if normalized_content:
        return f"{doc_id}_p{_short_hash(normalized_content)}"
    return doc_id


def build_content_ref(doc_id: str, stored: bool, source_id: str | None = None) -> dict[str, str]:
    """构造正文引用。

    Args:
        doc_id: 原始文档稳定 ID。
        stored: 是否已写入 source store。
        source_id: source store 中可回查的证据片段 ID。

    Returns:
        content_ref 字典。写入失败时使用 legacy_doc_infos 降级类型。
    """
    ref_type = "source_store" if stored else "legacy_doc_infos"
    content_ref = {"type": ref_type, "doc_id": doc_id}
    if source_id:
        content_ref["source_id"] = source_id
    return content_ref


def read_content_by_ref(
    content_ref: dict[str, Any] | None,
    source_store: CollectorSourceStore,
    legacy_content: str = "",
) -> str:
    """按 content_ref 回查正文。

    Args:
        content_ref: 正文引用。
        source_store: 当前 collector source store。
        legacy_content: 兼容期 `doc_infos.original_content` 兜底正文。

    Returns:
        正文内容；回查失败时返回兼容正文或空字符串。
    """
    if not isinstance(content_ref, dict):
        return legacy_content or ""
    doc_id = str(content_ref.get("doc_id") or "")
    source_id = str(content_ref.get("source_id") or doc_id)
    if content_ref.get("type") == "source_store" and source_id:
        content = source_store.read(source_id)
        if content is not None:
            return content
        logger.warning(
            "[CollectorEvidence] content_ref missing in source_store. doc_id=%s | source_id=%s",
            doc_id,
            source_id,
        )
    return legacy_content or ""


def extract_source(url: str) -> str:
    """提取文档来源标识。

    Args:
        url: 文档 URL。

    Returns:
        Web 域名或 localdataset。
    """
    if str(url).startswith("localdataset://"):
        return "localdataset"
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def split_passages(content: str, max_length: int = 500, overlap: int = 200) -> list[str]:
    """把正文切分为结构化的段落。

    依据 COINS 2025 基准实验推荐：
    - 以句子级切分为主要策略（优于固定长度/语义切分）
    - 窗口大小 max_length（默认 500 字符，约 512 token）
    - 片段间保留 overlap（默认 200 字符）的上下文重叠

    流程：
    1. 按空行分为块
    2. 表格/列表块保持完整（超长时按行切分，保留表头）
    3. 普通块：按句号切分 → 句子级贪心累积到 max_length → 片段间保留 overlap 重叠
    4. 合并短片段（< 40 字符）到上一段

    Args:
        content: 原始正文。
        max_length: 单个段落最大字符数。
        overlap: 片段间重叠字符数。

    Returns:
        已去空白的段落列表。
    """
    if not content or not content.strip():
        return []

    # Step 1: 按空行分段
    raw_blocks = re.split(r"\n\s*\n", content)
    blocks: list[str] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        blocks.append(block)

    # Step 2: 对每个块应用句子级切分
    passages: list[str] = []
    for block in blocks:
        lines = block.split("\n")

        # 表格/列表块：保持完整或按行切分
        if _is_markdown_table(block) or _is_structured_block(lines):
            if len(block) <= max_length:
                passages.append(block)
            elif _is_markdown_table(block):
                passages.extend(_split_long_table(block, max_length))
            else:
                passages.append(block)  # 列表块不拆
            continue

        # 普通文本块：句子级切分 + 滑动窗口
        sentences = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Z])", block)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            continue

        # CJK 句子间无空格，拉丁文句子间需空格分隔
        current: list[str] = []
        for sentence in sentences:
            if current and len(_join_sentences(current + [sentence])) > max_length:
                passages.append(_join_sentences(current))
                # 从末尾向前贪心取 overlap 字符的句子作为重叠
                overlap_sentences: list[str] = []
                overlap_len = 0
                for prev_sentence in reversed(current):
                    if overlap_len + len(prev_sentence) > overlap:
                        break
                    overlap_sentences.insert(0, prev_sentence)
                    overlap_len += len(prev_sentence)
                current = list(overlap_sentences) + [sentence] if overlap_sentences else [sentence]
            else:
                current.append(sentence)

        if current:
            passages.append(_join_sentences(current))

    # Step 3: 合并短片段（< 40 字符）到上一段
    merged: list[str] = []
    for passage in passages:
        passage = passage.strip()
        if not passage:
            continue
        if merged and len(passage) < 40:
            merged[-1] = merged[-1] + "\n" + passage
        else:
            merged.append(passage)

    return merged


def _is_structured_block(lines: list[str]) -> bool:
    """检测是否是表格/列表块，如果是则不应拆分。

    判定规则：超过一半的行包含表格/列表特征。
    """
    if len(lines) < 3:
        return False
    structured_count = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 表格行（含 |）
        if "|" in line:
            structured_count += 1
        # Markdown 表格分隔行
        elif re.match(r"^\|?[\s\-:|]+\|?$", line):
            structured_count += 1
        # 列表项（1. / - / * / 数字.）
        elif re.match(r"^\d+[.、)]", line) or re.match(r"^[-*•]", line):
            structured_count += 1
    total = max(1, len([line for line in lines if line.strip()]))
    return structured_count / total > 0.5


def _is_markdown_table(passage: str) -> bool:
    """检测段落是否是 Markdown 表格。

    Markdown 表格的结构：
    | 列1 | 列2 |
    | --- | --- |
    | 数据1 | 数据2 |
    """
    lines = passage.strip().split("\n")
    if len(lines) < 3:
        return False
    # 第一行必须含 |（表头行）
    if "|" not in lines[0]:
        return False
    # 第二行必须是分隔行（|---|---| 或 |:---|:---:|）
    if not re.match(r"^\|?[\s\-:|]+\|?$", lines[1].strip()):
        return False
    # 至少有一行数据行（含 |）
    return any("|" in line for line in lines[2:])


def _split_long_table(passage: str, max_length: int = 500) -> list[str]:
    """将超长 Markdown 表格按行切分，每个片段保留表头。

    确保表格结构不丢失：每个片段都包含完整的表头行和分隔行，
    数据行按 max_length 预算贪心累积。
    """
    lines = passage.strip().split("\n")
    # 提取表头行 + 分隔行（前两行）
    header_lines = lines[:2]
    data_lines = lines[2:]
    header_block = "\n".join(header_lines)
    header_len = len(header_block)

    # 如果表头本身已超限，无法安全切分，回退到硬截断
    if header_len >= max_length:
        return [passage[:max_length]]

    result: list[str] = []
    current_rows: list[str] = []
    current_len = header_len

    for row in data_lines:
        row_len = len(row) + 1  # +1 for \n
        if current_rows and current_len + row_len > max_length:
            # 当前片段已满，输出
            result.append(header_block + "\n" + "\n".join(current_rows))
            current_rows = [row]
            current_len = header_len + row_len
        else:
            current_rows.append(row)
            current_len += row_len

    if current_rows:
        result.append(header_block + "\n" + "\n".join(current_rows))

    return result if result else [passage[:max_length]]


def extract_keywords(query: str, title: str = "") -> list[str]:
    """从 query 和标题中提取轻量关键词。

    Args:
        query: 检索 query。
        title: 文档标题。

    Returns:
        去重后的关键词列表。
    """
    text = f"{query} {title}".strip()
    ascii_terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", text)
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    terms: list[str] = []
    for chunk in cjk_chunks:
        if len(chunk) <= 4:
            terms.append(chunk)
            continue
        for size in (4, 3, 2):
            for index in range(0, len(chunk) - size + 1):
                terms.append(chunk[index:index + size])
    terms.extend(ascii_terms)
    seen = set()
    output = []
    for term in terms:
        normalized = term.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(term)
    return output[:30]


def _passage_score(passage: str, keywords: list[str], max_length: int = MAX_PASSAGE_LENGTH) -> float:
    """计算段落作为 key passage 的规则分数。

    Args:
        passage: 候选段落。
        keywords: query 和标题提取出的关键词。
        max_length: 单个片段最大长度，用于评分阈值判断。

    Returns:
        规则分数，值越高表示越适合作为 key passage。
    """
    lower_passage = passage.lower()
    score = 0.0
    for keyword in keywords:
        if keyword.lower() in lower_passage:
            score += 2.0
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|％|亿|万|年|月|日|美元|元)", passage):
        score += 1.5
    if 40 <= len(passage) <= max_length:
        score += 0.5
    if len(passage) > max_length * 2:
        score -= 1.0
    return score


def _passage_has_keyword(passage: str, keywords: list[str]) -> bool:
    """判断段落是否命中 query/title 关键词。

    Args:
        passage: 候选段落。
        keywords: query 和标题提取出的关键词。

    Returns:
        只要命中任一关键词即返回 True。
    """
    lower_passage = passage.lower()
    return any(keyword.lower() in lower_passage for keyword in keywords)


def extract_key_passages(
    content: str,
    query: str,
    title: str = "",
    max_passages: int = DEFAULT_KEY_PASSAGE_COUNT,
    max_length: int = MAX_PASSAGE_LENGTH,
) -> list[str]:
    """规则抽取 key passages，不增加额外 LLM 调用。

    Args:
        content: 原始正文或 local chunk。
        query: 当前检索 query。
        title: 文档标题。
        max_passages: 最多返回片段数。
        max_length: 单个片段最大长度。

    Returns:
        关键片段列表；无命中时返回正文前段。
    """
    passages = split_passages(content, max_length=max_length)
    if not passages:
        return []
    keywords = extract_keywords(query, title)
    scored = [
        (
            _passage_score(passage, keywords, max_length),
            index,
            passage[:max_length],
            _passage_has_keyword(passage, keywords),
        )
        for index, passage in enumerate(passages)
    ]
    matched = [item for item in scored if item[3]]
    selected = sorted(matched, key=lambda item: (-item[0], item[1]))[:max_passages]
    if not selected:
        selected = [
            (0, index, passage[:max_length], False)
            for index, passage in enumerate(passages[:max_passages])
        ]
    selected = sorted(selected, key=lambda item: item[1])
    return [item[2] for item in selected]


# ============================================================================
# Coverage Evidence
#
# Coverage 通道与 key_passages 并行但职责不同：key_passages 以 query/title
# 关键词命中为中心（相关性检索），Coverage 以客观信息密度为中心（事实覆盖）。
# 它不依赖关键词，专用于兜住“原文中确实存在、但未被关键词命中的事实段落”。
# ============================================================================


@dataclass
class CoveragePassage:
    """一个由相邻段落合并而成的覆盖证据块。

    Attributes:
        text: 合并后的证据文本，内部保留段落换行。
        score: 块内成员段落的最高 Coverage Score。
        source_indices: 命中的段落序号（按原始正文切分后的序号，升序）。
        features: 块内各特征在原段落上的聚合计数。
    """

    text: str
    score: float
    source_indices: list[int]
    features: dict[str, float]


# 数字特征：复用共享的富版事实模式（数字+单位/货币前缀/学术统计），并补裸数字
# 兜底（含千分位与拉丁边界，避免 "5Very" 误判）。与日期重叠的数字由日期特征排除。
_COVERAGE_NUMBER_PATTERN = re.compile("".join([
    r"\d+(?:\.\d+)?\s*(?:" + _SUFFIX_UNIT_PATTERN + r")|",
    _PREFIX_CURRENCY_SYMBOLS + r"\s*\d+(?:\.\d+)?|",
    "|".join(_ACADEMIC_STAT_PATTERNS),
    r"|(?<![A-Za-z0-9])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![A-Za-z])",
]))
# 日期特征：2025年 / 2025年3月 / 2025年3月15日 / 2025-03-17 / 2025/03/17，
# 以及季度/半年等绝对时间段锚点（Q3、第三季度、上半年）。
_COVERAGE_DATE_PATTERN = re.compile(
    r"(?<!\d)\d{4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?"
    r"|(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|第[一二三四]季度|[上下]半年|Q[1-4]"
)
# 时间特征：相对时间表达。季度/半年属于可靠时间段锚点（见日期特征）；
# "目前/当前/近期"是话语连接词，不作为时间特征。
_COVERAGE_TIME_PATTERN = re.compile(
    r"(?:去年|今年|明年|本年度|本季度|上一季度|上季度)"
)
# 实体特征：中文机构后缀 + 英文专有名词候选（轻量规则，不引入 NER）。
# 中文侧以"前缀+机构后缀"结构信号判定；英文侧同样只认正字法结构信号——
# 词内第二处大写字母（OpenAI/NASA/iPhone/GDP/U.S）与非句首的 Title 词
# （"at Microsoft" 的 Microsoft、"Goldman Sachs" 的 Sachs）。句首首字母
# 大写是英文书写规范而非专名信号（任何词都可能出现在句首，停用词表无法
# 穷举），不作为判定依据（PR !380 评审意见：Revenue/However 误判）。
_COVERAGE_ENTITY_SUFFIXES = (
    "公司", "集团", "大学", "研究院", "研究所", "科学院", "委员会", "基金会",
    "银行", "医院", "总局", "基地", "产业园", "论坛", "峰会", "实验室", "中心", "协会",
)
_COVERAGE_CJK_ENTITY_PATTERN = re.compile(
    r"[一-鿿]{2,20}?(?:" + "|".join(_COVERAGE_ENTITY_SUFFIXES) + r")"
)
#: 英文 token：拉丁字母开头，词内允许字母/数字/&/'/-/.（覆盖 R&D、U.S.、
#: O'Brien、McDonald's 等专名内部标点）；词尾连接标点在提取时剥除。
_COVERAGE_EN_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9&.'-]*")
#: 粘连字符：token 起点紧贴这些字符说明它不是独立词——"5Very"、"foo_Bar"
#: 的后半段（字母/数字/下划线粘连）或 URL 路径段 "example.com/Products" 的
#: "Products"（斜杠粘连，网页采集常见）。CJK 字符不属于此集合（"发布了
#: OpenAI" 中 OpenAI 是独立 token）。
_COVERAGE_EN_GLUE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_/"
)
#: 句首判定：token 前跳过空白后遇到句末标点/换行/冒号/引号括号、列表
#: 引导符（- * •）或表格竖线（或文本起点）即视为句首。冒号计入（新闻
#: 标题 "Bloomberg: Markets Fall" 的 Title 词是排版风格而非专名）；逗号/
#: 分号不计入——正字法要求普通词在逗号分号后保持小写，其后保持大写的
#: 词是专名信号。\r 是行终止符，与 \n 同归断句集（"Markets\rRose" 的
#: Rose 是新行句首；若放进空白跳过集，回扫会落在前词尾字母上，修不掉）。
_COVERAGE_EN_SENTENCE_BREAK_CHARS = frozenset(
    ".!?…。！？\n\r:：|-*•·\"'“”‘’()（）[]【】《》«»{}"
)
#: 水平空白（空格/Tab/全角空格/NBSP）：句首判定的回扫跳过集与 Title 序列
#: 的间隔连续集共用。换行类（\n、\r）不属于水平空白。
_COVERAGE_EN_HORIZONTAL_WHITESPACE = frozenset(" \t　\xa0")
#: 缺空格句界：小写词尾 + 句点 + 大写（"grew.The"）几乎总是丢空格的句界
#: 而非词内大写缩写。token 字符类含句点（为 U.S. 类缩写），会把下一句的
#: 句首词吞进 token 造成"词内大写"误报；先补回空格，让句首词回到句首
#: 位置参与判定（"grew.The market" 不产实体、"U.S. market" 不受影响）。
_COVERAGE_EN_DOT_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z])\.(?=[A-Z])")
# 引用/来源特征：[1] / (Reuters, 2025) / 来源：xxx / https://...
_COVERAGE_CITATION_PATTERN = re.compile(
    r"\[\s*\d+\s*\]"
    r"|\([^)\n]{0,80}(?:19|20)\d{2}[^)\n]{0,80}\)"
    r"|(?:来源|参考|引自)[:：]\s*\S+"
    r"|https?://[^\s，,。；;）)]+"
)
# 噪声过滤：过短段落、标题行、导航文本。
_COVERAGE_MIN_PARAGRAPH_CHARS = 10
_COVERAGE_NOISE_HEADING_PATTERN = re.compile(r"^#{1,6}\s+\S+")
_COVERAGE_NAV_TOKENS = frozenset({
    "首页", "上一页", "下一页", "目录", "更多", "返回", "免责声明", "关于我们", "联系我们",
})
# 纯引用/链接列表：剥掉引用标记与网址后几乎无实质内容，避免被引用特征打高分误选。
_COVERAGE_CITATION_LIST_PATTERN = re.compile(r"\[\s*\d+\s*\]|https?://[^\s，,。；;）)]+")
_COVERAGE_HTML_TAG_PATTERN = re.compile(
    r"</?(?:script|style|div|span|p|br|li|ul|ol|a|img|td|tr|th|table|thead|tbody|"
    r"h[1-6]|section|header|footer|nav|strong|em|b|i|u|blockquote|pre|code)("
    r"\s[^<>]*)?>",
    re.IGNORECASE,
)
#: 隐藏内容块：<script>/<style> 连同载荷整体删除（渲染页面上不可见，不构成
#: "从网页可见内容提取事实"的证据来源）。先于普通标签剥离执行，防止载荷在
#: 剥壳后以纯文本残留、被 Coverage Score 选中注入下游 Prompt。未闭合的块
#: 仅删标签本身（载荷按普通文本处理，避免误删正常正文）。
_COVERAGE_HTML_HIDDEN_BLOCK_PATTERN = re.compile(
    r"<(script|style)\b[^<>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
#: HTML 注释在渲染页面上同样不可见，整体删除。
_COVERAGE_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
_COVERAGE_CONTROL_CHAR_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​‌‍﻿]"
)
# Coverage Score 权重与特征上限（§12：1.0*N + 2.0*D + 1.5*T + 1.5*E + 1.0*C）。
_COVERAGE_NUMBER_WEIGHT = 1.0
_COVERAGE_DATE_WEIGHT = 2.0
_COVERAGE_TIME_WEIGHT = 1.5
_COVERAGE_ENTITY_WEIGHT = 1.5
_COVERAGE_CITATION_WEIGHT = 1.0
_COVERAGE_NUMBER_CAP = 5
_COVERAGE_FEATURE_CAP = 3
_COVERAGE_NEAR_DEDUP_RATIO = 0.6
#: 信息结构特征：长度落在合理区间给满分，过长线性衰减。
_COVERAGE_STRUCTURE_WEIGHT = 1.0
_COVERAGE_STRUCTURE_MIN_CHARS = 40
_COVERAGE_STRUCTURE_MAX_CHARS = 800
#: 进程内缓存上限：同参数内容在重试/重生成时不重复计算，且内存有界。
_COVERAGE_CACHE_MAXSIZE = 512

# ---------------------------------------------------------------------------
# 集成预算（方案 B Phase3 + 方案2 迭代）：覆盖证据进入大纲证据时的成本闸门。
# 选段不预设硬 Top-K，由单文档/章节的字符预算兜底（方案2：预算即终止条件）。
# report/evidence.py 集成层从本处导入，避免口径复制漂移。
# ---------------------------------------------------------------------------
_COVERAGE_TOP_K_CAP = 128
_COVERAGE_MAX_CHARS_PER_DOC = 1200
_COVERAGE_MAX_TOTAL_CHARS = 6000
#: 规则收尾（方案4/3/1）默认参数。方案4 锚点级去重已内置；方案1 密度计分经
#: 真实数据 A/B 后选定为默认；方案3 邻域密度门控在预算兜底（K≈候选全集）下
#: 为空操作，默认关闭（保留参数以便在有限 K 场景评估）。
#: 锚点级近似去重阈值：两块锚点键重合率 ≥ 该值判为"同一事实的换措辞"。
#: 0.85 = 仅容忍措辞级差异（键几乎全同）；数字相同但方向/单位/量级任一
#: 维度不同的键（如 `+20%` vs `-20%`、`100公里` vs `100万台`）都会把重合
#: 率压到阈值之下，从而保留。宁可漏删（代价=token 冗余）不可误删（代价=
#: 事实丢失，与 Coverage 通道目标直接冲突）。
_COVERAGE_ANCHOR_DEDUP_RATIO = 0.85
_COVERAGE_EXPANSION_DENSITY_THRESHOLD = 0.0
_COVERAGE_SCORE_MODE = "density"
_COVERAGE_DENSITY_MIN_LEN = 40
#: 合并跨度上限：相邻高分段连续并入一个证据块的最多段数，防止雪崩式吞并。
_COVERAGE_MAX_MERGE_SPAN = 5


@dataclass(frozen=True)
class CoverageOptions:
    """覆盖抽取的高级调参（公共默认值之外的精调旋钮）。

    把 ``max_merge_span`` / ``expansion_density_threshold`` / ``score_mode``
    三个相关参数封装为具名对象，使 ``extract_coverage_passages`` 的形参
    个数控制在编码规范上限（5）以内； frozen 使其可作为 ``lru_cache`` 的
    可哈希键。

    Attributes:
        max_merge_span: 相邻段落合并进同一证据块的最多段数，防雪崩吞并。
        expansion_density_threshold: 邻域扩张的事实密度门控；0 表示不门控。
        score_mode: 计分口径，"absolute" 或 "density"。
    """

    max_merge_span: int = _COVERAGE_MAX_MERGE_SPAN
    expansion_density_threshold: float = _COVERAGE_EXPANSION_DENSITY_THRESHOLD
    score_mode: str = _COVERAGE_SCORE_MODE


#: 模块级默认选项，供未显式传参的调用方复用。
_COVERAGE_DEFAULT_OPTIONS = CoverageOptions()


def _coverage_split_passages(content: str) -> list[str]:
    """把正文切分为覆盖评分用的段落。

    注意：上游 `split_passages` 是句子级滑动窗口（整块 ≤500 字符时合并为一段），
    会破坏 coverage 依赖的"句/段粒度"候选与邻域窗口语义，因此 coverage 侧固定
    采用句末与换行级别的切分，避免跟随上游策略漂移。

    Markdown 表格例外：连续以 ``|`` 起始的行合并为原子单元再参与切分（表格识别
    复用本文件 key 通道的 `_is_markdown_table`）。逐行切分会让表头与数据行失联：
    分隔行被噪声规则丢弃造成下标断档、打断 run 合并，表头含数字时与数据分属两块
    （关联脆弱，任一块独立被裁即失去列归属），不含数字时被噪声与零特征双重丢弃
    （列语义不可恢复）。超长有效表格经 `_split_long_table` 按行切分并逐片段保留
    表头；单行 ``|`` 片段（事实行/面包屑）保持独立成段的既有行为。

    Args:
        content: 原始正文。

    Returns:
        已去空白的段落列表。
    """
    paragraphs: list[str] = []
    plain_lines: list[str] = []
    table_lines: list[str] = []

    def _flush_plain() -> None:
        if not plain_lines:
            return
        segment = "\n".join(plain_lines)
        plain_lines.clear()
        raw_parts = re.split(r"(?:\n\s*\n|\n|(?<=[。！？!?])|(?<=\.)(?=\s|$))", segment)
        paragraphs.extend(part.strip() for part in raw_parts if part and part.strip())

    def _flush_table() -> None:
        if not table_lines:
            return
        block = "\n".join(table_lines)
        single_line = len(table_lines) == 1
        table_lines.clear()
        if single_line:
            paragraphs.append(block)
        elif _is_markdown_table(block):
            paragraphs.extend(_split_long_table(block))
        else:
            paragraphs.append(block)

    for line in (content or "").split("\n"):
        if line.strip().startswith("|"):
            _flush_plain()
            table_lines.append(line.strip())
        else:
            _flush_table()
            plain_lines.append(line)
    _flush_plain()
    _flush_table()
    return paragraphs


def _normalize_coverage_content(content: str) -> str:
    """覆盖抽取前的文本标准化（不破坏段落边界与事实表达）。

    Args:
        content: 原始正文。

    Returns:
        标准化后的文本：统一换行/空白、剥离常见 HTML 标签与控制字符。
        渲染页面上不可见的隐藏内容（script/style 载荷、HTML 注释）在剥标签前
        整体删除。
    """
    text = str(content or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _COVERAGE_HTML_HIDDEN_BLOCK_PATTERN.sub("", text)
    text = _COVERAGE_HTML_COMMENT_PATTERN.sub("", text)
    text = _COVERAGE_HTML_TAG_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _COVERAGE_CONTROL_CHAR_PATTERN.sub("", text)
    return text.strip()


def _is_coverage_noise(paragraph: str) -> bool:
    """判断段落是否属于明显噪声（过短、标题、导航等）。

    Args:
        paragraph: 候选段落。

    Returns:
        True 表示应被过滤。
    """
    text = paragraph.strip()
    if len(text) < _COVERAGE_MIN_PARAGRAPH_CHARS:
        return True
    if _COVERAGE_NOISE_HEADING_PATTERN.match(text):
        return True
    if text in _COVERAGE_NAV_TOKENS:
        return True
    if text.count("|") >= 2 and len(text) - text.count("|") <= 80 and not re.search(r"\d", text):
        return True
    stripped_citations = _COVERAGE_CITATION_LIST_PATTERN.sub("", paragraph)
    if stripped_citations != paragraph and len(stripped_citations.strip()) < 6:
        return True
    return False


def _count_numbers_outside_date_spans(paragraph: str) -> int:
    """统计段落中的普通数字数量，排除落入日期区间的数字。

    Args:
        paragraph: 候选段落。

    Returns:
        普通数字个数；日期内部的年/月/日不计入。
    """
    date_spans = [(match.start(), match.end()) for match in _COVERAGE_DATE_PATTERN.finditer(paragraph)]
    count = 0
    for match in _COVERAGE_NUMBER_PATTERN.finditer(paragraph):
        if not any(start <= match.start() and match.end() <= end for start, end in date_spans):
            count += 1
    return count


def _is_english_sentence_start(text: str, start: int) -> bool:
    """判断 token 起点是否处于句首位置（文本起点或断句字符之后）。

    从 token 前一个字符向前跳过水平空白（空格/Tab/全角空格/NBSP）；
    走到文本起点或遇到 `_COVERAGE_EN_SENTENCE_BREAK_CHARS` 中的字符即为
    句首，否则非句首。
    """
    index = start - 1
    while index >= 0 and text[index] in _COVERAGE_EN_HORIZONTAL_WHITESPACE:
        index -= 1
    if index < 0:
        return True
    return text[index] in _COVERAGE_EN_SENTENCE_BREAK_CHARS


def _is_sequence_gap_blank(gap: str) -> bool:
    """Title 序列的 token 间间隔是否为纯水平空白。

    间隔含逗号/顿号/数字/换行/任何非空白字符（如 "Apple, Microsoft"、
    "Goldman 500 Sachs"）都不算序列连续——列举不是专名短语，序列应重置。
    """
    return all(char in _COVERAGE_EN_HORIZONTAL_WHITESPACE for char in gap)


def _iter_english_entities(text: str) -> list[str]:
    """按正字法结构信号提取英文专有名词候选，计数与锚点提取共用同一口径。

    只认两条结构信号（判定依据见 `_COVERAGE_EN_TOKEN_PATTERN` 处注释）：
    词内第二处大写字母（OpenAI/NASA/iPhone/U.S，与位置无关），或非句首的
    首字母大写词（"at Microsoft" 的 Microsoft、"Goldman Sachs" 的 Sachs）。
    句首 Title 词无法与句首普通词区分（"Revenue increased..." 与 "Microsoft
    announced..." 同形），判定从缺——宁可漏检（由 key 通道关键词兜底）不可
    误报（整段叙述文本涌入 coverage）。

    连续 Title 词序列（"Goldman Sachs"、"Markets Fall Again"）整体只计 1
    个实体：序列内除首词外的词保持首字母大写是专名短语与标题排版的共同
    形态，压缩计数对齐中文侧行为——中文纯文字标题（无机构后缀）得 0
    实体分，英文纯文字标题压缩后同样只贡献单个实体分，不再高于典型
    事实段。

    Returns:
        实体候选 token 列表，按出现顺序；已剥除尾部连接标点（``& ' . -``），
        长度不足 2 的 token 不产出。
    """
    if not text:
        return []
    # 缺空格句界（"grew.The"）把句点替换为换行：1:1 等长替换保持偏移一致，
    # 防止下一句的句首词被 token 字符类中的句点吞并成"词内大写"误报
    # （"grew.The market" 不产实体、"U.S. market" 不受影响）。
    prepared = _COVERAGE_EN_DOT_BOUNDARY_PATTERN.sub("\n", text)
    entities: list[str] = []
    # Title 序列状态：sequence_counted=当前连续 Title 序列是否已计过实体。
    # 词内大写 token 与小写词打断序列；句首 Title 词开启新序列并重置计数。
    # 序列连续性按 token 间**间隔文本**判定：只有纯水平空白延续序列——
    # "Goldman Sachs"（空格）、"Markets Fall Again"（空格）是序列；
    # "Apple, Microsoft, Google"（逗号列举）与 "Goldman 500 Sachs"（数字
    # 分隔）间隔含非空白字符，序列重置、各词独立参与信号判定。
    sequence_counted = False
    prev_end = -1
    for match in _COVERAGE_EN_TOKEN_PATTERN.finditer(prepared):
        gap = prepared[prev_end:match.start()] if prev_end >= 0 else ""
        if gap and not _is_sequence_gap_blank(gap):
            sequence_counted = False
        prev_end = match.end()
        token = match.group()
        while token and token[-1] in "&.'-":
            token = token[:-1]
        if len(token) < 2:
            # 单字母 token（"A"/"I"）透明跳过，不产出也不打断序列。
            continue
        start = match.start()
        # 粘连串切片（词首或词尾紧贴字母/数字/下划线/斜杠，如 "5Very"、
        # "foo_Bar"、"example.com/Products" 的后半段）不是独立词，透明跳过。
        # CJK 字符不算粘连（"发布了OpenAI" 中 OpenAI 独立）。
        if start > 0 and prepared[start - 1] in _COVERAGE_EN_GLUE_CHARS:
            continue
        if match.end() < len(prepared) and prepared[match.end()] in _COVERAGE_EN_GLUE_CHARS:
            continue
        if any(char.isupper() for char in token[1:]):
            entities.append(token)
            sequence_counted = False
            continue
        if token[0].isupper():
            if _is_english_sentence_start(prepared, start):
                sequence_counted = False
            elif not sequence_counted:
                entities.append(token)
                sequence_counted = True
            continue
        sequence_counted = False
    return entities


def _count_entities(paragraph: str) -> int:
    """统计段落中的实体特征命中数（中文机构后缀 + 英文专有名词候选）。

    中文机构名由"前缀+后缀词"构成，一个后缀词命中后紧邻的通用法定/附属
    尾词（如"股份有限公司""附属医院"）会再次命中同一个后缀模式。这类紧邻
    复用是同一机构名的残留切片，应去重以免把单个机构算成多个实体。
    """
    cjk_hits: list[str] = []
    previous_end: int | None = None
    for match in _COVERAGE_CJK_ENTITY_PATTERN.finditer(paragraph):
        if previous_end is not None and match.start() == previous_end:
            continue
        cjk_hits.append(match.group())
        previous_end = match.end()
    return len(cjk_hits) + len(_iter_english_entities(paragraph))


def _coverage_structure_score(paragraph_len: int) -> float:
    """信息结构特征：长度落在合理区间给满分，过长线性衰减，避免超长表格霸榜。"""
    if paragraph_len < _COVERAGE_STRUCTURE_MIN_CHARS:
        return 0.0
    if paragraph_len <= _COVERAGE_STRUCTURE_MAX_CHARS:
        return 1.0
    decay = (paragraph_len - _COVERAGE_STRUCTURE_MAX_CHARS) / (
        _COVERAGE_STRUCTURE_MAX_CHARS * 2
    )
    return max(0.0, 1.0 - decay)


def _coverage_features(paragraph: str) -> dict[str, float]:
    """提取段落的事实特征计数（原始值，不做封顶）。

    Args:
        paragraph: 候选段落。

    Returns:
        特征名到原始计数的映射；``structure`` 项为长度结构分，且仅对至少含
        一个事实特征的段落生效，避免纯叙述段只靠长度入选。
    """
    counts = {
        "number": float(_count_numbers_outside_date_spans(paragraph)),
        "date": float(len(_COVERAGE_DATE_PATTERN.findall(paragraph))),
        "time": float(len(_COVERAGE_TIME_PATTERN.findall(paragraph))),
        "entity": float(_count_entities(paragraph)),
        "citation": float(len(_COVERAGE_CITATION_PATTERN.findall(paragraph))),
    }
    counts["structure"] = (
        _coverage_structure_score(len(paragraph))
        if any(value > 0 for value in counts.values())
        else 0.0
    )
    return counts


def _coverage_score(
    features: dict[str, float],
    paragraph_len: int | None = None,
    score_mode: str = "absolute",
) -> float:
    """把特征计数加权为 Coverage Score（数字封顶 5，其余计数特征封顶 3）。

    score_mode="density" 时按段落长度归一化（方案1 的 A/B 开关，分母下限
    `_COVERAGE_DENSITY_MIN_LEN`）。本函数签名的默认值为 "absolute"（权重表
    单测直接调用的口径）；抽取流水线显式传入 `_COVERAGE_SCORE_MODE` 决定的
    当前模式（默认 "density"，经真实数据 A/B 后选定）。
    """
    base = (
        _COVERAGE_NUMBER_WEIGHT * min(features.get("number", 0.0), _COVERAGE_NUMBER_CAP)
        + _COVERAGE_DATE_WEIGHT * min(features.get("date", 0.0), _COVERAGE_FEATURE_CAP)
        + _COVERAGE_TIME_WEIGHT * min(features.get("time", 0.0), _COVERAGE_FEATURE_CAP)
        + _COVERAGE_ENTITY_WEIGHT * min(features.get("entity", 0.0), _COVERAGE_FEATURE_CAP)
        + _COVERAGE_CITATION_WEIGHT * min(features.get("citation", 0.0), _COVERAGE_FEATURE_CAP)
    )
    if score_mode == "density":
        length = paragraph_len if paragraph_len is not None else _COVERAGE_DENSITY_MIN_LEN
        return base / max(length, _COVERAGE_DENSITY_MIN_LEN)
    structure = min(features.get("structure", 0.0), 1.0)
    return base + _COVERAGE_STRUCTURE_WEIGHT * structure


def _date_spans(text: str) -> list[tuple[int, int]]:
    """文本中日期正则的命中区间，用于排除日期内部的普通数字。"""
    return [
        (match.start(), match.end()) for match in _COVERAGE_DATE_PATTERN.finditer(text or "")
    ]


def extract_fact_anchors(text: str) -> set[str]:
    """从文本中提取事实锚点（数字/日期/时间/实体/引用）去重集合。

    供覆盖证据的锚点级去重与离线评估基线共用；CJK 机构邻接尾词与
    `_count_entities` 同口径去重，英文实体同样复用 `_iter_english_entities`
    的正字法结构信号口径，避免两处判定漂移。
    """
    if not text:
        return set()
    spans = _date_spans(text)
    anchors: set[str] = set()

    def _collect(pattern, exclude_in_date_spans: bool = False) -> None:
        for match in pattern.finditer(text):
            if exclude_in_date_spans and any(
                start <= match.start() and match.end() <= end for start, end in spans
            ):
                continue
            anchors.add(match.group(0))

    _collect(_COVERAGE_DATE_PATTERN)
    _collect(_COVERAGE_TIME_PATTERN)
    _collect(_COVERAGE_CITATION_PATTERN)
    _collect(_COVERAGE_NUMBER_PATTERN, True)
    previous_end: int | None = None
    for match in _COVERAGE_CJK_ENTITY_PATTERN.finditer(text):
        if previous_end is not None and match.start() == previous_end:
            continue
        anchors.add(match.group(0))
        previous_end = match.end()
    anchors.update(_iter_english_entities(text))
    return anchors


def _coverage_char_bigrams(text: str) -> set[str]:
    """字符二元组集合，作为零依赖的词级近似（对语序重排鲁棒）。"""
    return {text[index:index + 2] for index in range(len(text) - 1)}


def _coverage_jaccard_similarity(first: str, second: str) -> float:
    """两段归一化文本的字符二元组 Jaccard 相似度。"""
    first_bigrams = _coverage_char_bigrams(first)
    second_bigrams = _coverage_char_bigrams(second)
    if not first_bigrams or not second_bigrams:
        return 0.0
    union = len(first_bigrams | second_bigrams)
    return len(first_bigrams & second_bigrams) / union if union else 0.0


def _anchor_dedup_key(anchor: str) -> tuple[str, str]:
    """把锚点规约为去重键：数值锚点保留原文（数字/小数点/正负号/单位/量级词
    一律保留），仅做三类字符级规整；其余（实体等）保留原文。

    规整项（不做单位等价折叠——单位枚举不完且与识别层的
    ``_SUFFIX_UNIT_PATTERN`` 形成两处真源；排版等价由字符规整覆盖）：
    千分位逗号（``1,000`` 与 ``1000`` 同键）、全半角百分号、数字与后续
    单位之间的排版空白（``3 万`` 与 ``3万`` 同键）。

    因此 ``20%`` 与 ``20个百分点`` 不同键（换措辞去重由多锚点重合率承担，
    不再依赖单位折叠）；``20bp`` 与 ``20%`` 不同键；``1.5亿元`` 与 ``15%``
    不同键——数字相同但单位/量级/写法不同的锚点不再被折叠为同一键，
    避免锚点级去重误删"数字核心相同、语义维度不同"的真实事实。
    """
    folded = anchor.replace(",", "").replace("，", "")
    folded = folded.replace("％", "%")
    folded = re.sub(r"(?<=\d)\s+", "", folded)
    if not any(char.isdigit() for char in folded):
        return ("txt", anchor)
    return ("num", folded)


def _coverage_fact_anchor_keys(text: str) -> set[tuple[str, str]]:
    """文本事实锚点的去重键集合。"""
    return {_anchor_dedup_key(anchor) for anchor in extract_fact_anchors(text)}


@functools.lru_cache(maxsize=_COVERAGE_CACHE_MAXSIZE)
def _extract_coverage_passages_cached(
    content: str,
    max_passages: int,
    neighbor_window: int,
    max_chars: int,
    options: CoverageOptions,
) -> tuple[CoveragePassage, ...]:
    """进程内有界缓存的核心抽取实现（键=全部参数）。

    Args:
        options: 高级调参（合并跨度上限、邻域密度门控、计分口径）。

    Returns:
        按原文顺序排列的覆盖证据块元组；返回对象只读，调用方不应就地修改。
    """
    max_merge_span = options.max_merge_span
    expansion_density_threshold = options.expansion_density_threshold
    score_mode = options.score_mode
    paragraphs = _coverage_split_passages(_normalize_coverage_content(content))
    candidates: list[tuple[int, str, dict[str, float], float]] = []
    for index, paragraph in enumerate(paragraphs):
        if _is_coverage_noise(paragraph):
            continue
        features = _coverage_features(paragraph)
        score = _coverage_score(features, len(paragraph), score_mode)
        if score <= 0:
            continue
        candidates.append((index, paragraph, features, score))
    if not candidates:
        return []

    candidate_indices = {candidate[0] for candidate in candidates}
    ordered = sorted(candidates, key=lambda item: (-item[3], item[0]))
    top_k = ordered[:max_passages] if max_passages > 0 else []
    if not top_k:
        return []

    # 邻域扩张（方案3 精细化）：事实密度（计数特征和 / 段长）达到阈值的段落
    # 不再拉取邻居（本身信息完整，省下预算给新段落）；未达向的段落保留 ±window
    # 上下文，兜住跨句表达的事实。
    included: set[int] = set()
    window = max(0, neighbor_window)
    for index, paragraph, features, _ in top_k:
        eff_window = window
        if expansion_density_threshold and paragraph:
            density = sum(value for key, value in features.items() if key != "structure")
            if density / max(len(paragraph), 1) >= expansion_density_threshold:
                eff_window = 0
        for neighbor_index in range(index - eff_window, index + eff_window + 1):
            if neighbor_index in candidate_indices:
                included.add(neighbor_index)

    by_index = {candidate[0]: candidate for candidate in candidates}
    runs: list[list[tuple[int, str, dict[str, float], float]]] = []
    for index in sorted(included):
        if runs and index == runs[-1][-1][0] + 1:
            span = index - runs[-1][0][0] + 1
            if span <= max_merge_span:
                runs[-1].append(by_index[index])
            else:
                runs.append([by_index[index]])
        else:
            runs.append([by_index[index]])

    blocks: list[CoveragePassage] = []
    for run in runs:
        text = "\n".join(member[1] for member in run)
        max_score = max(member[3] for member in run)
        indices = [member[0] for member in run]
        features = {
            name: float(sum(member[2][name] for member in run))
            for name in run[0][2]
        }
        blocks.append(CoveragePassage(text=text, score=max_score, source_indices=indices, features=features))

    # 两级去重：Level 1 精确（归一化文本相同），Level 2 近似（相似度阈值）。
    # 锚点键是文本的纯函数：候选块按原文、已保留块按归一化文本各算一次键集
    # 合后复用，避免对同一块在每次新候选到来时重复计算（O(n²) 次键计算 →
    # O(n) 次）。单锚点块跳过近似去重：一个共享数字不足以证明"同一事实的
    # 换措辞"（如"收入增长20%"与"成本下降20%"），误删代价大于漏删。
    unique_blocks: list[CoveragePassage] = []
    kept_normalized: list[str] = []
    kept_key_sets: list[set[tuple[str, str]]] = []
    for block in blocks:
        normalized = normalize_content_for_dedup(block.text)
        if normalized in kept_normalized:
            continue
        keys = _coverage_fact_anchor_keys(block.text)
        if len(keys) >= 2:
            near_duplicate = False
            for kept_keys in kept_key_sets:
                if not kept_keys:
                    continue
                shared = keys & kept_keys
                if shared and len(shared) / len(keys | kept_keys) >= _COVERAGE_ANCHOR_DEDUP_RATIO:
                    near_duplicate = True
                    break
            if near_duplicate:
                continue
        kept_normalized.append(normalized)
        kept_key_sets.append(_coverage_fact_anchor_keys(normalized))
        unique_blocks.append(block)

    # 字符数限制：预算非正不产出证据；否则按分数降序贪心。能整块放下的完整
    # 保留；放不下且已有更高考分的块时整块丢弃（保持证据块语义完整）；仅当
    # 分数最高的块自身都超出预算时才截断它，确保能尽量保留证据。
    if max_chars <= 0:
        return []
    budget_blocks: list[CoveragePassage] = []
    remaining = max(0, int(max_chars))
    for block in sorted(unique_blocks, key=lambda item: (-item.score, min(item.source_indices))):
        if remaining <= 0:
            break
        if len(block.text) > remaining and budget_blocks:
            continue
        truncated_block = block
        if len(block.text) > remaining:
            truncated_block = CoveragePassage(
                text=block.text[:remaining],
                score=block.score,
                source_indices=block.source_indices,
                features=dict(block.features),
            )
        budget_blocks.append(truncated_block)
        remaining -= len(truncated_block.text)

    budget_blocks.sort(key=lambda item: min(item.source_indices))
    return tuple(budget_blocks)


def extract_coverage_passages(
    content: str,
    max_passages: int = 5,
    neighbor_window: int = 1,
    max_chars: int = _COVERAGE_MAX_CHARS_PER_DOC,
    options: CoverageOptions | None = None,
) -> list[CoveragePassage]:
    """规则抽取覆盖证据段落，不增加额外 LLM 调用，不依赖 query 关键词。

    内部流程按阶段执行：标准化 → 段落切分 → 噪声过滤 → 事实特征提取 →
    Coverage Score → Top-K → Neighbor Expansion → 相邻段落合并（含跨度上限，
    达到上限另起新块）→ 去重 → 字符数限制。结果带进程内有界缓存
    （键=content+全部参数），同章节重试或重新生成时不重复计算。

    预算分两层：本函数的 ``max_chars`` 是**单文档**预算（默认
    ``_COVERAGE_MAX_CHARS_PER_DOC=1200``，生产路径即用此值）；章节级共享
    总预算 ``_COVERAGE_MAX_TOTAL_CHARS=6000`` 由 report/evidence.py 集成层
    （``_fit_coverage_to_budget``）二次裁剪，不经本参数表达。

    Args:
        content: 原始正文。
        max_passages: 进入 Top-K 的段落数上限（影响最终证据块数量）。
            独立调用的保守默认 5；生产路径按 ``_COVERAGE_TOP_K_CAP=128``
            传入（选段不预设硬 Top-K，由字符预算兜底）。
        neighbor_window: Top-K 段落的相邻扩展窗口。
        max_chars: 最终证据块的累计字符数预算（单文档口径）。
        options: 高级调参（合并跨度上限、邻域密度门控、计分口径）。
            ``None`` 时用模块级默认 ``_COVERAGE_DEFAULT_OPTIONS``。

    Returns:
        按原文顺序排列的覆盖证据块列表（每次调用返回新建对象，调用方可自由
        就地修改而不影响缓存或其他调用方）；无有效事实或预算非正时返回空表。
    """
    resolved_options = options if options is not None else _COVERAGE_DEFAULT_OPTIONS
    cached = _extract_coverage_passages_cached(
        content, max_passages, neighbor_window, max_chars, resolved_options,
    )
    return [
        CoveragePassage(
            text=item.text,
            score=item.score,
            source_indices=list(item.source_indices),
            features=dict(item.features),
        )
        for item in cached
    ]


def _is_duplicate_text(text: str, reference: str, threshold: float) -> bool:
    """判断归一化后的两段文本是否构成实质重复。

    Args:
        text: 归一化后的待判断文本。
        reference: 归一化后的参照文本。
        threshold: 近似重复的字符二元组 Jaccard 相似度阈值。

    Returns:
        True 表示两段文本相同、高度相似，或其中一段是另一段的高占比子串。
    """
    if text == reference:
        return True
    if _coverage_jaccard_similarity(text, reference) >= threshold:
        return True
    shorter, longer = (text, reference) if len(text) <= len(reference) else (reference, text)
    if shorter and shorter in longer and len(shorter) >= 0.6 * len(longer):
        return True
    return False


def exclude_passages(
    coverage_passages: list[CoveragePassage],
    key_passages: list[str],
    similarity_threshold: float = _COVERAGE_NEAR_DEDUP_RATIO,
) -> list[CoveragePassage]:
    """剔除覆盖证据中与同文档 key passages 高度重复的段落。

    覆盖证据与关键片段高度重叠时同时进入 prompt 只会增加 token 而信息冗余。
    覆盖证据块常把一条 key passage 连同相邻上下文合并进来，因此除相同/高相似
    外，还把"高占比子串"视为重复。

    Args:
        coverage_passages: Coverage 抽取结果（保持原顺序）。
        key_passages: 同一文档的关键片段列表。
        similarity_threshold: 判定近似重复的归一化相似度阈值。

    Returns:
        与 key passages 不重复的覆盖证据（保持原顺序）。
    """
    if not coverage_passages or not key_passages:
        return list(coverage_passages)
    normalized_keys = [normalize_content_for_dedup(key) for key in key_passages]
    kept: list[CoveragePassage] = []
    for passage in coverage_passages:
        normalized = normalize_content_for_dedup(passage.text)
        if any(
            _is_duplicate_text(normalized, key, similarity_threshold)
            for key in normalized_keys
        ):
            continue
        kept.append(passage)
    return kept


def build_evidence_atom(
    record: dict[str, Any],
    query: str,
    source_store: CollectorSourceStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """从搜索记录构造 evidence atom 和兼容 doc_info。

    Args:
        record: 标准化搜索记录。
        query: 当前检索 query。
        source_store: 当前 source store。

    Returns:
        `(atom, doc_info)`；atom 不包含完整正文，doc_info 保留 legacy `original_content`。
    """
    url = str(record.get("url") or "")
    title = str(record.get("title") or "Untitled")
    full_text = str(record.get("full_text") or "")
    use_full_text = record.get("full_text_status") == "available" and bool(full_text.strip())
    content = str(full_text if use_full_text else record.get("content") or "")[
        :MAX_COLLECTOR_DOC_CONTENT_LENGTH
    ]
    source_type = (
        "local"
        if str(record.get("type") or "").lower() == "text" or url.startswith("localdataset://")
        else "web"
    )
    doc_id = generate_doc_id(url=url, title=title, source_type=source_type)
    source_id = generate_source_id(doc_id, content=content)
    stored = source_store.write(source_id, content)
    if not stored:
        logger.warning(
            "[CollectorEvidence] failed to write source_store. doc_id=%s | source_id=%s",
            doc_id,
            source_id,
        )
    content_ref = build_content_ref(doc_id=doc_id, source_id=source_id, stored=stored)
    key_passages = extract_key_passages(content=content, query=query, title=title)
    date_metadata = record.get("date_metadata") or {}
    canonical_publish_time = ""
    if date_metadata.get("type") == "published":
        canonical_publish_time = str(date_metadata.get("parsed_date") or "")
        if not canonical_publish_time and date_metadata.get("precision") in {"year", "month"}:
            canonical_publish_time = str(date_metadata.get("value") or "")
    # Search APIs report a relevance score (0-1) on their normalized records;
    # expose it as scores.relevance so downstream consumers (e.g. enrichment
    # candidate ranking) can sort without degrading to insertion order.
    scores_record = record.get("scores")
    relevance_val = None
    if isinstance(scores_record, dict) and scores_record:
        evidence_scores = dict(scores_record)
        relevance_val = scores_record.get("relevance")
    else:
        evidence_scores = {}
    if relevance_val is None:
        raw_score = record.get("score")
        if isinstance(raw_score, (int, float)):
            relevance_val = raw_score
    if isinstance(relevance_val, (int, float)):
        evidence_scores["relevance"] = max(0.0, min(float(relevance_val), 1.0))
    elif "relevance" not in evidence_scores:
        evidence_scores["relevance"] = 0.0
    base = {
        "doc_id": doc_id,
        "source_id": source_id,
        "title": title,
        "url": url,
        "source": extract_source(url),
        "publish_time": canonical_publish_time or "",
        "doc_time": canonical_publish_time or "",
        "query": query,
        "key_passages": key_passages,
        "content_ref": content_ref,
        "scores": evidence_scores,
        "evidence_content_type": "full_text" if use_full_text else "abstract",
        "evidence_content_chars": len(content),
    }
    if record.get("skip_webpage_enrichment") is True:
        base["skip_webpage_enrichment"] = True
    for key in (
        "academic_source",
        "academic_source_id",
        "doi",
        "pmid",
        "pmcid",
        "arxiv_id",
        "matched_sources",
        "source_ids",
        "full_text_candidates",
        "full_text_status",
        "content_type",
        "full_text_format",
        "full_text_url",
        "full_text_truncated",
    ):
        if key in record:
            base[key] = record[key]
    doc_info = {**base, "original_content": content}
    return base, doc_info


def _truncate_text(value: Any, max_length: int) -> str:
    """截断 evidence 文本字段。

    Args:
        value: 原始字段值。
        max_length: 最大保留长度。

    Returns:
        截断后的字符串。
    """
    text = str(value or "")
    return text[:max_length]


def _compact_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """构造不含全文的紧凑文档视图。

    Args:
        doc: 完整兼容 doc_info。

    Returns:
        不含 original_content 的 evidence 视图。
    """
    result = {
        "source_id": doc.get("source_id") or doc.get("doc_id", ""),
        "doc_id": doc.get("doc_id", ""),
        "title": _truncate_text(doc.get("title", ""), 120),
        "url": _truncate_text(doc.get("url", ""), 300),
        "source": _truncate_text(doc.get("source", ""), 120),
        "publish_time": doc.get("publish_time") or doc.get("doc_time", ""),
        "query": doc.get("query", ""),
        "key_passages": [_truncate_text(passage, MAX_PASSAGE_LENGTH) for passage in doc.get("key_passages", [])],
        "content_ref": doc.get("content_ref", {}),
    }
    if doc.get("scores"):
        result["scores"] = doc["scores"]
    for key in (
        "academic_source",
        "academic_source_id",
        "doi",
        "pmid",
        "pmcid",
        "arxiv_id",
        "matched_sources",
        "source_ids",
        "evidence_content_type",
        "evidence_content_chars",
        "full_text_format",
        "full_text_url",
        "full_text_truncated",
    ):
        if key in doc:
            result[key] = doc[key]
    return result


def _compact_supervisor_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """构造 SupervisorNode 使用的紧凑 evidence 行。

    Args:
        doc: 完整兼容 doc_info。

    Returns:
        字段级截断后的 supervisor evidence 行。
    """
    compact = _compact_doc(doc)
    result = {
        "source_id": compact["source_id"],
        "doc_id": compact["doc_id"],
        "title": compact["title"],
        "source": compact["source"],
        "publish_time": compact["publish_time"],
        "key_passages": compact["key_passages"],
    }
    if doc.get("scores"):
        result["scores"] = doc["scores"]
    return result


def build_evaluation_documents(doc_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造 doc_evaluator 的短输入。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        不含 original_content 的短输入列表。
    """
    return [_compact_doc(doc) for doc in doc_infos if isinstance(doc, dict)]


def build_supervisor_evidence_table(
    doc_infos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """构造 SupervisorNode 的 compact evidence table。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        compact evidence table。
    """
    ranked_docs = [doc for doc in doc_infos if isinstance(doc, dict)]
    return [_compact_supervisor_doc(doc) for doc in ranked_docs]


def build_summary_evidence_pack(
    doc_infos: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造 SummaryNode 的 evidence pack。

    Args:
        doc_infos: 完整兼容 doc_infos。

    Returns:
        面向总结节点的轻量 evidence pack。
    """
    compact_docs = [_compact_doc(doc) for doc in doc_infos if isinstance(doc, dict)]
    return {
        "sources": compact_docs,
        "source_ids": [doc.get("source_id", "") for doc in compact_docs],
    }
