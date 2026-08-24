# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""搜索内容噪声清洗（文本级，纯规则，零 LLM 零新增依赖）。

输入是搜索引擎/抓取服务已抽取的文本/markdown（非原始 HTML），目标是删除混入
content 字段的页面样板与抽取残留：导航菜单、相关推荐、页脚备案、cookie/订阅
横幅、脚本或 markdown 残留标记等，提高下游 10000/15000 字符截断窗口内的有效
正文占比。

三层结构：
- L0 残留规范化：实体反转义、markdown 链接还原文本、孤立 HTML 标签删除、空行压缩。
- L1 行级样板删除：按空行/标题行切块，以语言中立形态特征（行数/平均行长/链接行
  占比/块位置）判定，chrome 特征词表（中英双语）仅作辅助条件。
- L2 护栏：G1 长度护栏 / G2 删除占比保险丝 / G3 事实锚点护栏，任一触发即整体
  回退原文（宁漏勿杀，最坏情况=现状）。

设计文档：docs/superpowers/specs/2026-08-23-html-content-cleaning-design.md
"""

import re
from dataclasses import dataclass, field, fields, replace
from html import unescape
from typing import Any


@dataclass(frozen=True)
class ContentCleaningConfig:
    """搜索内容清洗配置承载对象（默认即生产值，无外部配置项）。

    由 framework 节点构造、collector 纯函数消费，algorithm 层不直接读全局 state。
    三道护栏（G1/G2/G3）保证最坏情况=原文，故默认开启、不设总开关之外的
    运维调参入口；调参直接改这里的默认值。
    """

    enabled: bool = True
    min_chars: int = 1500
    max_remove_ratio: float = 0.6
    min_keep_chars: int = 500
    min_keep_ratio: float = 0.4
    anchor_keep_ratio: float = 0.85


@dataclass
class CleaningStats:
    """单文档清洗统计（仅供模块自检与单测断言，不写入归一化文档，不参与去重/选材/写作）。

    Attributes:
        raw_chars: 清洗前字符数。
        cleaned_chars: 清洗后字符数（回退时等于 raw_chars）。
        removed_ratio: 删除字符占比（回退时为 0.0）。
        applied_rules: 命中的规则名（L0/R1/R2/R3；回退时保留诊断价值）。
        fallback_reason: 回退原因（min_keep/max_remove_ratio/anchor_keep），未回退为 None。
    """

    raw_chars: int
    cleaned_chars: int
    removed_ratio: float
    applied_rules: list[str] = field(default_factory=list)
    fallback_reason: str | None = None

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的元数据 dict。"""
        return {
            "raw_chars": self.raw_chars,
            "cleaned_chars": self.cleaned_chars,
            "removed_ratio": self.removed_ratio,
            "applied_rules": list(self.applied_rules),
            "fallback_reason": self.fallback_reason,
        }


#: dict 注入时允许覆盖的字段名（与 dataclass 字段保持一致，单源不漂移）。
_CONTENT_CLEANING_CONFIG_KEYS = frozenset(f.name for f in fields(ContentCleaningConfig))


def default_content_cleaning_config() -> ContentCleaningConfig:
    """构造默认清洗配置（默认值即 dataclass 字段默认值）。"""
    return ContentCleaningConfig()


def _is_valid_config_value(value: Any, default: Any) -> bool:
    """dict 注入值的类型校验（类型口径随字段默认值走，单源不漂移）。

    bool 字段只收 bool；int 字段收 int；float 字段收 int/float。
    数值校验均排除 bool（bool 是 int 的子类，``{"min_chars": True}`` 属类型错）。
    """
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def coerce_content_cleaning_config(raw: Any) -> ContentCleaningConfig:
    """把 agent_input 中携带的配置（ContentCleaningConfig 或 dict）归一为配置对象。

    dict 形式按字段名覆盖默认值；未知键与类型错的值一律忽略；
    无法识别时返回默认值配置。
    """
    if isinstance(raw, ContentCleaningConfig):
        return raw
    if isinstance(raw, dict):
        base = default_content_cleaning_config()
        overrides = {}
        for key, value in raw.items():
            if key not in _CONTENT_CLEANING_CONFIG_KEYS:
                continue
            if _is_valid_config_value(value, getattr(base, key)):
                overrides[key] = value
        return replace(base, **overrides)
    return default_content_cleaning_config()


# ---------------------------------------------------------------------------
# L0 残留规范化
# ---------------------------------------------------------------------------

# markdown 链接 [text](url)（负向前瞻排除图片语法 ![alt](url)，图片属正文内容，原样保留）
_MD_LINK_PATTERN = re.compile(r"(?<!!)\[([^\]\n]*)\]\([^()\n]*\)")

# 孤立 HTML 标签残留：仅限常见标签名，且要求标签前后是空白/行边界，
# 避免误伤正文中的比较式或泛型写法（如 ``a<b>c``、``List<T>``）。
_ORPHAN_TAG_PATTERN = re.compile(
    r"(?<![^\s])</?(?:div|br|hr|p|span|a|img|ul|ol|li|dl|dt|dd|table|thead|tbody|tfoot|tr|td|th|"
    r"section|article|header|footer|nav|aside|main|figure|figcaption|caption|"
    r"h[1-6]|strong|em|b|i|u|s|small|big|center|font|form|button|input|label|select|option|textarea|"
    r"video|audio|source|iframe|object|embed|script|style|link|meta|noscript)(?:\s[^<>\n]*)?/?>(?![^\s])",
    re.IGNORECASE,
)

# 连续 ≥3 个换行（含空白行）压缩为单个空行。
# [^\S\n] 匹配除换行外的行内空白（含 \r），兼容 CRLF；前导 \r? 吃掉首个换行的 \r，
# 避免 CRLF 文档压缩后残留孤立 \r。字符类与 \n 互斥、无嵌套量词，无回溯风险。
_BLANK_RUN_PATTERN = re.compile(r"\r?\n(?:[^\S\n]*\n){2,}")

# markdown 标题行（块边界）
_HEADING_LINE_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s")


def _apply_l0_pre(text: str) -> tuple[str, bool]:
    """L0 第一阶段：孤立标签删除、实体反转义、空行压缩（链接还原放到 L1 之后）。

    孤立标签删除必须在实体反转义之前：真实抽取残留是字面 ``<div>`` 等标签；
    若先反转义，正文里的转义写法（如 ``&lt;div&gt;``，HTML 教程常见）会变成
    字面标签再被误删。链接行占比是 L1 的判定特征，依赖 markdown 链接语法，
    故链接还原必须在 L1 判定之后执行；对幸存内容而言先后执行结果等价。
    """
    changed = False
    new_text = _ORPHAN_TAG_PATTERN.sub("", text)
    new_text = unescape(new_text)
    new_text = _BLANK_RUN_PATTERN.sub("\n\n", new_text)
    if new_text != text:
        changed = True
    return new_text, changed


def _restore_markdown_links(text: str) -> str:
    """L0 链接还原：[text](url) → text（图片语法 [!alt](url) 不动，裸 URL 行不动）。"""
    return _MD_LINK_PATTERN.sub(lambda m: m.group(1), text)


# ---------------------------------------------------------------------------
# L1 行级样板删除
# ---------------------------------------------------------------------------

#: chrome 特征词表（中英双语，仅作 R2 的辅助条件，双条件判定防误伤）。
_CHROME_FEATURE_WORDS = (
    "版权所有", "相关阅读", "上一篇", "下一篇", "icp备", "公网安备", "分享到", "免责声明",
    "关注我们", "违法和不良信息举报",
    "all rights reserved", "related articles", "subscribe", "cookie",
    "privacy policy", "follow us", "sign up",
)

#: 尾部特征词表（备案/版权/举报/关注我们/隐私政策类，供 R3 使用，需命中 ≥2 个）。
_TAIL_FEATURE_WORDS = (
    "版权所有", "icp备", "公网安备", "违法和不良信息举报", "免责声明", "关注我们", "隐私政策",
    "all rights reserved", "privacy policy", "cookie policy", "follow us",
)

#: 块级门槛：短于此字符数或长于彼字符数的块（多为正文容器）不参与 L1 评估。
_BLOCK_MIN_CHARS = 4
_BLOCK_MAX_CHARS = 5000

#: R3 尾部区域：块起始位置处于文档末尾 20% 区域内。
_TAIL_REGION_RATIO = 0.8

# R1 链接列表块阈值
_R1_MIN_LINES = 3
_R1_LINK_LINE_RATIO = 0.6
_R1_MAX_AVG_LINE_CHARS = 40

# R2 chrome 块阈值
_R2_LINK_LINE_RATIO = 0.4
_R2_MAX_AVG_LINE_CHARS = 25

#: 行内 markdown 链接构造（[text](url) 整体）占行长 ≥50% 判定为链接行。
_LINK_LINE_COVERAGE = 0.5


def _split_blocks(text: str) -> list[dict]:
    """按空行/标题行把文本切块，保留每块在原文中的字符区间（用于无损重组）。

    Returns:
        块列表，每块为 {start, end, text}；块间分隔符不属于任何块，
        重组时按区间从原文取未被删除的部分。
    """
    blocks = []
    block_start = 0
    lines = text.split("\n")
    offset = 0
    for line in lines:
        line_end = offset + len(line)
        if not line.strip():
            if block_start < offset:
                blocks.append({"start": block_start, "end": offset, "text": text[block_start:offset]})
            block_start = line_end + 1
        elif _HEADING_LINE_PATTERN.match(line) and block_start < offset:
            # 标题行作为块边界：先收掉前一块，标题行自身并入新块
            blocks.append({"start": block_start, "end": offset, "text": text[block_start:offset]})
            block_start = offset
        offset = line_end + 1
    if block_start < len(text):
        blocks.append({"start": block_start, "end": len(text), "text": text[block_start:]})
    return blocks


def _line_link_coverage(line: str) -> float:
    """行内 markdown 链接构造（[text](url) 整体，含 URL）占该行 strip 后长度的比例。"""
    stripped = line.strip()
    if not stripped:
        return 0.0
    link_chars = sum(len(m.group(0)) for m in _MD_LINK_PATTERN.finditer(line))
    return link_chars / len(stripped)


def _block_features(block_text: str) -> dict:
    """计算块的语言中立形态特征：行数、平均行长、链接行占比、命中特征词。"""
    lines = [line for line in block_text.split("\n") if line.strip()]
    line_count = len(lines)
    avg_line_chars = sum(len(line.strip()) for line in lines) / line_count if line_count else 0
    link_line_ratio = (
        sum(1 for line in lines if _line_link_coverage(line) >= _LINK_LINE_COVERAGE) / line_count
        if line_count else 0.0
    )
    lowered = block_text.casefold()
    chrome_hits = {word for word in _CHROME_FEATURE_WORDS if word in lowered}
    tail_hits = {word for word in _TAIL_FEATURE_WORDS if word in lowered}
    return {
        "line_count": line_count,
        "avg_line_chars": avg_line_chars,
        "link_line_ratio": link_line_ratio,
        "chrome_hits": chrome_hits,
        "tail_hits": tail_hits,
    }


def _match_l1_rule(block: dict, total_chars: int) -> str | None:
    """判定块是否命中 L1 删除规则，命中返回规则名，未命中返回 None。"""
    block_chars = len(block["text"].strip())
    if block_chars < _BLOCK_MIN_CHARS or block_chars > _BLOCK_MAX_CHARS:
        return None
    features = _block_features(block["text"])
    # R1 链接列表块：导航菜单、相关推荐、友情链接
    if (features["line_count"] >= _R1_MIN_LINES
            and features["link_line_ratio"] >= _R1_LINK_LINE_RATIO
            and features["avg_line_chars"] <= _R1_MAX_AVG_LINE_CHARS):
        return "R1"
    # R2 chrome 块：双条件是误伤防线（特征词 + 高链接密度或短行）
    if (features["chrome_hits"]
            and (features["link_line_ratio"] >= _R2_LINK_LINE_RATIO
                 or features["avg_line_chars"] <= _R2_MAX_AVG_LINE_CHARS)):
        return "R2"
    # R3 尾部样板块：文档末尾 20% 区域内命中尾部特征词 ≥2 个
    if (total_chars > 0
            and block["start"] / total_chars >= _TAIL_REGION_RATIO
            and len(features["tail_hits"]) >= 2):
        return "R3"
    return None


def _apply_l1(text: str) -> tuple[str, list[str]]:
    """执行 L1 行级样板删除，返回删除后文本与命中的规则名列表。"""
    blocks = _split_blocks(text)
    if not blocks:
        return text, []
    total_chars = len(text)
    applied_rules = []
    kept_parts = []
    cursor = 0
    for block in blocks:
        rule = _match_l1_rule(block, total_chars)
        if rule is None:
            continue
        if rule not in applied_rules:
            applied_rules.append(rule)
        # 删除整块（含块前分隔空白），其余部分按原区间保留
        kept_parts.append(text[cursor:block["start"]])
        cursor = block["end"]
    if not applied_rules:
        return text, []
    kept_parts.append(text[cursor:])
    cleaned = "".join(kept_parts)
    cleaned = _BLANK_RUN_PATTERN.sub("\n\n", cleaned).strip("\n")
    return cleaned, applied_rules


# ---------------------------------------------------------------------------
# L2 护栏
# ---------------------------------------------------------------------------

#: 事实锚点：≥4 位数字与百分数（只用长数字/百分比规避导航小数字干扰）。
_ANCHOR_PATTERN = re.compile(r"\d+(?:[.,]\d+)*%|\d{4,}(?:[.,]\d+)*")

#: 裸 URL 行（整行 strip 后就是一个 URL，如数据集链接表），G3 掩蔽时剔除。
_BARE_URL_LINE_PATTERN = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)


def _extract_anchors(text: str) -> set[str]:
    """从文本提取事实锚点集合（长数字/百分数，去千分位逗号归一化）。"""
    return {m.group(0).replace(",", "") for m in _ANCHOR_PATTERN.finditer(text)}


def _mask_urls_for_anchors(text: str) -> str:
    """G3 锚点提取前的 URL 掩蔽：markdown 链接还原为锚文本、剔除裸 URL 行。

    URL 路径段内的数字（文章 ID、日期路径）不是正文事实，不计入锚点——
    否则 R1 删除数字 URL 链接块、L0 链接还原剥掉数字 URL 都会被 G3 误判为
    事实丢失而误回退。原文与清洗后文本走同一掩蔽+提取函数，集合比较口径
    对称（千分位归一化两侧一致），且避免逐锚点子串扫描。
    """
    masked = _restore_markdown_links(text)
    return "\n".join(
        line for line in masked.split("\n") if not _BARE_URL_LINE_PATTERN.match(line)
    )


def _check_guards(raw: str, cleaned: str, config: ContentCleaningConfig) -> str | None:
    """L2 护栏检查，触发返回回退原因，未触发返回 None。"""
    if cleaned == raw:
        # 无删除则无风险，直接放行（避免 G1/G2 对未改动文本误判）
        return None
    raw_chars = len(raw)
    cleaned_chars = len(cleaned)
    # G1 长度护栏
    if cleaned_chars < config.min_keep_chars or cleaned_chars < config.min_keep_ratio * raw_chars:
        return "min_keep"
    # G2 删除占比保险丝
    if raw_chars > 0 and (raw_chars - cleaned_chars) / raw_chars > config.max_remove_ratio:
        return "max_remove_ratio"
    # G3 事实锚点护栏（URL 掩蔽口径，集合比较保留率）
    anchors = _extract_anchors(_mask_urls_for_anchors(raw))
    if anchors:
        kept = len(anchors & _extract_anchors(_mask_urls_for_anchors(cleaned)))
        if kept / len(anchors) < config.anchor_keep_ratio:
            return "anchor_keep"
    return None


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def clean_web_content(content: str, config: ContentCleaningConfig) -> tuple[str, CleaningStats]:
    """清洗 web 搜索内容中的页面样板噪声（纯函数、确定性输出）。

    Args:
        content: 搜索引擎/抓取服务已抽取的文本/markdown。
        config: 清洗配置。

    Returns:
        (清洗后文本, 清洗统计)。门控（开关关闭或原文短于 min_chars）或护栏触发
        时返回原文。
    """
    raw_chars = len(content)
    if not config.enabled or raw_chars < config.min_chars or not content.strip():
        return content, CleaningStats(
            raw_chars=raw_chars, cleaned_chars=raw_chars, removed_ratio=0.0,
        )

    applied_rules = []
    text, l0_changed = _apply_l0_pre(content)
    if l0_changed:
        applied_rules.append("L0")
    text, l1_rules = _apply_l1(text)
    applied_rules.extend(l1_rules)
    restored = _restore_markdown_links(text)
    if restored != text and "L0" not in applied_rules:
        applied_rules.append("L0")
    cleaned = restored

    fallback_reason = _check_guards(content, cleaned, config)
    if fallback_reason is not None:
        # 护栏触发：整体回退原文，保留规则命中记录用于排障
        return content, CleaningStats(
            raw_chars=raw_chars,
            cleaned_chars=raw_chars,
            removed_ratio=0.0,
            applied_rules=applied_rules,
            fallback_reason=fallback_reason,
        )

    cleaned_chars = len(cleaned)
    return cleaned, CleaningStats(
        raw_chars=raw_chars,
        cleaned_chars=cleaned_chars,
        removed_ratio=(raw_chars - cleaned_chars) / raw_chars if raw_chars else 0.0,
        applied_rules=applied_rules,
    )
