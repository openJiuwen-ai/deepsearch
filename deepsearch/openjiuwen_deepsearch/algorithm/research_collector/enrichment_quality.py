# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""网页压缩 Prompt 的事实表达提取，不执行写回质量门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from openjiuwen_deepsearch.algorithm.research_collector.collector_evidence import extract_key_passages

_HEADINGS = r"(?:ABSTRACT|SUMMARY|INTRODUCTION|REFERENCES|RESULTS|DISCUSSION|CONCLUSIONS?)"
_BASE_NUMBER = r"[+\-−]?\d+(?:[.,]\d+)*(?:[eE][+\-]?\d+)?"
_SUPERSCRIPTS = "⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹"
_POWER = rf"(?:\s*\^\s*[+\-−]?\d{{1,8}}|[{_SUPERSCRIPTS}]{{1,9}})"
_NUMBER = rf"(?:{_BASE_NUMBER}\s*[×x*]\s*10{_POWER}|[+\-−]?10{_POWER}|{_BASE_NUMBER})"
_ATOMIC_UNITS = (
    r"mg\s*/\s*[Ll]|g\s*/\s*[Ll]|mmol\s*/\s*[Ll]|mol\s*/\s*[Ll]|"
    r"MHz|mHz|kHz|Hz|HZ|hz|mV|MV|µV|μV|kV|V|mA|µA|μA|A|"
    r"nm|µm|μm|mm|cm|m|kg|mg|µg|μg|g|mL|ml|L|mM|µM|μM|M|K|"
    r"°\s*[Cc]|℃|%|％|(?i:fps|bpm|mm\s*hg|ppm|minutes?|min|videos?|subjects?)|ms|"
    r"[fpnµμumkMGT]?(?:s|Hz|F|W|J)"
)
_UNIT_POWER = r"(?:\^[+\-]?\d{1,3}|[²³])?"
_UNITS = rf"(?:{_ATOMIC_UNITS}){_UNIT_POWER}(?:\s*/\s*(?:{_ATOMIC_UNITS}){_UNIT_POWER})?"
_QUANTITY = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<number>{_NUMBER})\s*(?P<unit>{_UNITS})"
    rf"(?![A-Za-z0-9/^<{_SUPERSCRIPTS}]|[\-‐‑–—][A-Za-z])"
)
_BARE_NUMBER = re.compile(rf"(?<![A-Za-z0-9_.]){_NUMBER}(?![0-9]|\.\d)")
_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z]{2,}[A-Za-z0-9_-]*|[A-Za-z]+\d[A-Za-z0-9_-]*|"
    r"[A-Za-z0-9]+-[A-Za-z0-9_-]+)(?![A-Za-z0-9])"
)
_RESOLUTION = re.compile(rf"(?<![A-Za-z0-9])\d+\s*[x×]\s*\d+(?![A-Za-z0-9^{_SUPERSCRIPTS}]|\.\d|\s*\^)")


@dataclass(frozen=True)
class FactAnchor:
    """保留原始表达及类型，避免把百分比归成无上下文整数。

    Attributes:
        text: 来源中的原始事实表达，供压缩请求使用。
        kind: quantity、number、identifier 或 description（普通复合描述词）。
        number: 数字部分；保留小数点、符号与科学记法。
        unit: 单位部分；保留区分量级的大小写。
    """

    text: str
    kind: str
    number: str = ""
    unit: str = ""


def clean_fact_text(text: str) -> str:
    """仅移除有格式依据的链接、脚注和章节标记，保留正文事实。

    Args:
        text: 待提取事实提示的旧证据文本。

    Returns:
        用于提取和匹配的文本，不直接替换持久化证据。
    """
    # 保留链接可见文字；下载标签和脚注跳转不是实验事实。
    def footnote_label(label: str) -> str:
        """移除脚注序号，但保留链接显示的科学事实。

        Args:
            label: 脚注链接可见文字。

        Returns:
            非序号的可见文字或空白。
        """
        visible = re.sub(r"<[^>]+>", "", label).strip()
        marker = visible.strip("[]() ")
        return " " if re.fullmatch(r"\d+|\*+|(?:ftn|fn|footnote)(?:ref)?\d+", marker, re.I) else visible

    text = re.sub(
        r"<a\b[^>]*href\s*=\s*['\"]#(?:ftn|fn|footnote)[^'\"]*['\"][^>]*>(.*?)</a>",
        lambda match: footnote_label(match[1]), text, flags=re.I | re.S,
    )
    text = re.sub(r"\[\^[^\]]+\]", " ", text)

    def visible_link(match: re.Match) -> str:
        """保留Markdown链接显示文字，识别明确的PDF下载标签。

        Args:
            match: 链接显示文字与目标地址的匹配。

        Returns:
            可见文字或空白。
        """
        label, target = match.groups()
        if re.match(r"\s*#(?:ftn|fn|footnote)", target, re.I):
            return footnote_label(label)
        nearby = text[max(0, match.start() - 100):match.end() + 100]
        scientific_pdf = re.search(r"probability\s+density|概率密度", nearby, re.I) is not None
        if (
            label.strip().casefold() == "pdf"
            and not scientific_pdf
            and re.search(r"\.pdf(?:[?#]|$)|download", target, re.I)
        ):
            return " "
        return label

    text = re.sub(r"!?\[([^\]\n]*)\]\(([^)\n]*)\)", visible_link, text)
    text = re.sub(r"https?://[^\s<>\]\)]+", " ", text, flags=re.I)
    text = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", text, flags=re.I | re.S)

    def image_alternative_text(match: re.Match) -> str:
        """提取图片替代文字，保留其中的实验条件。

        Args:
            match: HTML 图片标签的匹配。

        Returns:
            alt 属性文字；没有该属性时返回空白。
        """
        alt = re.search(r"\balt\s*=\s*['\"]([^'\"]*)['\"]", match[0], re.I)
        return alt[1] if alt else " "

    # 图片替代文字可能包含图中实验条件，不能与src/样式属性一起丢弃。
    text = re.sub(r"<img\b[^>]*>", image_alternative_text, text, flags=re.I)
    text = re.sub(r"</?h[1-6]\b[^>]*>", "\n", text, flags=re.I)
    # 只剥离标准HTML标记，避免把 p<0.05 等数学表达当HTML。
    text = re.sub(r"(?i)(\d+)<sup>([+\-−]?\d+)</sup>", r"\1^\2", text)
    text = re.sub(r"</?(?:a|sup|sub|span|strong|b|em|i)\b[^>]*>", "", text, flags=re.I)
    text = re.sub(r"</?(?:div|p|br|table|tr|td|th|img)\b[^>]*>", " ", text, flags=re.I)
    text = re.sub(
        rf"(?mi)^[ \t]*(?:\#{{1,6}}[ \t]*)?(?:[IVXLCDM]+[.)]?[ \t]+|\d+[.)][ \t]+)?{_HEADINGS}[ \t]*$",
        " ", text,
    )
    # 陌生的“II. XXX”可能是化合物编号，只清理已知章节名的前缀。
    text = re.sub(
        rf"(?mi)^([ \t]*(?:\#{{1,6}}[ \t]*)?)[IVXLCDM]+[.)][ \t]+"
        rf"(?={_HEADINGS}|METHODS?\b|MATERIALS?\b|EXPERIMENTAL\b)",
        r"\1", text,
    )
    text = re.sub(r"(?i)\b(?:sections?|chapters?)\s+[IVXLCDM]+(?:\s*(?:,|and|/)\s*[IVXLCDM]+)*\b", "section", text)
    # 搜索片段可能已将标题与正文拼成一行，只移除完整的专利章节标题。
    text = re.sub(r"\b(?:BRIEF DESCRIPTION OF THE DRAWINGS|DETAILED DESCRIPTION OF THE "
                  r"(?:INVENTION|EMBODIMENTS)|BACKGROUND OF THE INVENTION)\b", " ", text)
    # Reader 会拼接下载按钮与箭头标签；不删除科学定义中的普通 PDF 标识。
    text = re.sub(r"(?:download\s*)*Download\s+(?:free\s+)?PDF(?:\s*View\s+PDF)?"
                  r"(?:\s*chevron\\?_right)?", " ", text, flags=re.I)
    text = re.sub(r"\bView\s+PDF(?:\s*chevron\\?_right)?", " ", text, flags=re.I)
    # 只移除从1开始的连续列表序号；正文数值、单位、日期和孤立编号保持不变。
    markers = list(re.finditer(r"(?m)^[ \t]{0,3}(\d{1,3})[.)][ \t]+(?=[A-Z][A-Za-z]{2,}\b)", text))
    markers = [m for m in markers if not re.match(rf"(?:{_UNITS})(?![A-Za-z0-9])", text[m.end():])]
    removable = []
    index = 0
    while index < len(markers):
        end = index + 1
        if markers[index][1] == "1":
            while end < len(markers) and int(markers[end][1]) == end - index + 1:
                end += 1
            if end - index >= 2:
                removable.extend(markers[index:end])
        index = end
    for marker in reversed(removable):
        text = text[:marker.start()] + text[marker.end():]

    # 仅在有物理量解释的括号记法中合并排版拆分，不能把两个独立变量随意拼接。
    def join_variable(match: re.Match) -> str:
        """合并有电压/电流解释依据的变量下标。

        Args:
            match: 括号内单字母变量及大写下标。

        Returns:
            规范记法或无解释依据时的原表达。
        """
        context = text[max(0, match.start() - 80):match.start()]
        label = r"\bvoltage\b|电压" if match[1] == "V" else r"\bcurrent\b|电流"
        return f"({match[1]}{match[2]})" if re.search(f"(?:{label})\\s*$", context, re.I) else match[0]

    text = re.sub(r"\(([VI])[ \t]+([A-Z]{1,4})\)", join_variable, text)
    return text


def _unit_key(unit: str) -> str:
    """仅归一化无歧义的单位排版，不转换量级或物理单位。

    Args:
        unit: 匹配到的单位。

    Returns:
        兼容空格和常见单位拼写的键。
    """
    unit = re.sub(r"\s+", "", unit).replace("％", "%").replace("℃", "°C").replace("μ", "µ")
    unit = unit.replace("²", "^2").replace("³", "^3")
    lower = unit.casefold()
    if lower in {"fps", "bpm", "mmhg", "ppm"}:
        return lower
    if lower in {"min", "minute", "minutes"}:
        return "min"
    if lower in {"video", "videos", "subject", "subjects"}:
        return lower.rstrip("s")
    if lower == "°c":
        return "°C"
    return unit


def _number_key(number: str) -> str:
    """精确归一化数字形式，不做单位换算或有歧义的逗号转换。

    Args:
        number: 原始数字表达。

    Returns:
        不把98.96与9896混为一谈的匹配键。
    """
    normalized = number.replace("−", "-")
    translation = str.maketrans(_SUPERSCRIPTS, "+-0123456789")
    normalized = re.sub(rf"10([{_SUPERSCRIPTS}]+)", lambda m: "10^" + m[1].translate(translation), normalized)
    if "," in normalized:
        return normalized  # 地区小数逗号和千分位有歧义，要求保留原表达。
    power = re.fullmatch(r"(?:(.+?)\s*[×x*]\s*)?([+\-]?10)\s*\^\s*([+\-]?\d{1,8})", normalized)
    try:
        base = Decimal(power[1] or ("-1" if power[2].startswith("-") else "1")) if power else Decimal(normalized)
        if not base.is_finite():
            return normalized
        sign, digits, exponent = base.as_tuple()
        exponent += int(power[3]) if power else 0
        digits = list(digits)
        if not any(digits):
            return "0"
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        # 不用Decimal.normalize()，避免默认精度对长数字进行舍入。
        return f"{sign}:{''.join(map(str, digits))}:{exponent}"
    except (InvalidOperation, ValueError):
        return normalized


def _identifier_key(text: str) -> str:
    """规范化标识符的大小写、空格和分隔符。

    Args:
        text: 标识符表达。

    Returns:
        标识符键。
    """
    return re.sub(r"[\W_]+", "", text.replace("×", "x").casefold())


def _is_descriptive_phrase(text: str) -> bool:
    """区分普通复合词与含数字、缩写或单字母符号的技术标识。

    Args:
        text: 正则匹配到的连字符表达。

    Returns:
        各部分均为普通小写词或首字母大写词时为 True；F-tests、HYSA-QZ 等为 False。
    """
    parts = text.split("-")
    return len(parts) > 1 and all(
        re.fullmatch(r"[A-Za-z]{2,}", part) and (part.islower() or part.istitle()) for part in parts
    )


def _description_pattern(text: str) -> str:
    """构造词级排版匹配，不允许跨词拼接或省略描述条件。

    Args:
        text: 必须保留的复合描述词。

    Returns:
        兼容空格、连字符和大小写，带完整词边界的正则表达式。
    """
    return r"(?<![A-Za-z0-9])" + r"[\s_.\-‐‑–—]+".join(map(re.escape, text.split("-"))) + r"(?![A-Za-z0-9])"


def extract_fact_anchors(text: str) -> list[FactAnchor]:
    """提取清洗后的数值、单位和标识，按类型去重。

    Args:
        text: 来源关键片段或压缩正文。

    Returns:
        保留原始表达的事实锚点列表。
    """
    text = clean_fact_text(text)
    occupied: list[tuple[int, int]] = []
    output: dict[tuple[str, str, str], FactAnchor] = {}
    # 分辨率须完整保留，不能只留下宽度数字。
    for match in _RESOLUTION.finditer(text):
        anchor = FactAnchor(match.group(), "identifier")
        output[(anchor.kind, _identifier_key(anchor.text), "")] = anchor
        occupied.append(match.span())
    resolutions = list(occupied)
    resolution_index = 0
    for match in _QUANTITY.finditer(text):
        while resolution_index < len(resolutions) and resolutions[resolution_index][1] <= match.start():
            resolution_index += 1
        if resolution_index < len(resolutions) and resolutions[resolution_index][0] < match.end():
            continue
        anchor = FactAnchor(match.group().strip(), "quantity", match["number"], match["unit"])
        output[(anchor.kind, _number_key(anchor.number), _unit_key(anchor.unit))] = anchor
        occupied.append(match.span())
    occupied.sort()
    for pattern, kind in ((_BARE_NUMBER, "number"), (_IDENTIFIER, "identifier")):
        occupied_index = 0
        for match in pattern.finditer(text):
            while occupied_index < len(occupied) and occupied[occupied_index][1] <= match.start():
                occupied_index += 1
            if occupied_index < len(occupied) and occupied[occupied_index][0] < match.end():
                continue
            anchor_kind = "description" if kind == "identifier" and _is_descriptive_phrase(match.group()) else kind
            anchor = FactAnchor(match.group(), anchor_kind, match.group() if kind == "number" else "")
            key = _number_key(anchor.number) if kind == "number" else _identifier_key(anchor.text)
            output[(anchor_kind, key, "")] = anchor
    return list(output.values())


def required_fact_anchors(original_doc: dict) -> list[FactAnchor]:
    """生成压缩请求中的旧事实提示清单。

    Args:
        original_doc: 已有搜索证据。

    Returns:
        去重的锚点；关键片段无效或为空时沿用规则抽取。
    """
    raw = original_doc.get("key_passages")
    passages = [p for p in raw if isinstance(p, str) and p.strip()] if isinstance(raw, list) else []
    if not passages:
        passages = extract_key_passages(
            str(original_doc.get("original_content") or ""),
            str(original_doc.get("query") or ""), str(original_doc.get("title") or ""),
        )
    unique = {}
    for passage in passages:
        for anchor in extract_fact_anchors(passage):
            key = (anchor.kind, _identifier_key(anchor.text)) if anchor.kind in {"identifier", "description"} else (
                anchor.kind, _number_key(anchor.number), _unit_key(anchor.unit),
            )
            unique.setdefault(key, anchor)
    return list(unique.values())


def required_description_contexts(
    original_doc: dict, anchors: list[FactAnchor] | None = None,
) -> list[dict]:
    """为复合描述词关联已有关键片段，避免模型将其当作无上下文标识列表。

    Args:
        original_doc: 已有搜索证据。
        anchors: 已提取的事实提示清单；未提供时独立提取。

    Returns:
        描述表达与原关键片段索引；缺少可关联片段时引用 existing_content，不重复正文。
    """
    if anchors is None:
        anchors = required_fact_anchors(original_doc)
    descriptions = [anchor for anchor in anchors if anchor.kind == "description"]
    if not descriptions:
        return []
    raw = original_doc.get("key_passages")
    passages = [
        (i, clean_fact_text(p)) for i, p in enumerate(raw) if isinstance(p, str)
    ] if isinstance(raw, list) else []
    result = []
    for anchor in descriptions:
        pattern = re.compile(_description_pattern(anchor.text), re.I)
        indexes = [i for i, passage in passages if pattern.search(passage)]
        result.append({
            "expression": anchor.text,
            "key_passage_indexes": indexes,
            "context_source": "document.key_passages" if indexes else "existing_content",
        })
    return result
