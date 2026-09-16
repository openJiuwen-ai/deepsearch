# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""日期区间工具：时间约束软过滤的公共基础设施。

提供日期窗口严格解析、文档区间相对约束区间的四档判定，以及对应的时效分，
以及来源发表日期的容错解析。采集（research_collector）与报告（report）两侧
都依赖这些纯函数，故置于公共 utils 层，避免下层 collector 反向 import 上层
report。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import TemporalScope

_DATE_MIN = date.min
_DATE_MAX = date.max
_TIMELINESS_TABLE = {
    "compliant": 1.0,
    "partial": -0.3,
    "violation": -1.0,
    "unknown": 0.0,
}


def parse_date_window(s: str | None) -> tuple[date, date] | None:
    """严格解析 YYYY-MM-DD，返回 (start, end) 闭区间；失败返回 None。

    只接受严格 ``YYYY-MM-DD``（4位年-2位月-2位日，用连字符分隔）格式；
    不设年份窗口——历史或预测年份都合法，乱码由解析层挡。

    Args:
        s: 待解析的日期字符串，须为严格 YYYY-MM-DD 格式。

    Returns:
        解析成功时返回 ``(date, date)`` 形式的单日闭区间；输入为空、格式不符
        或无法解析时返回 None。
    """
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return None
    return (d, d)


def parse_content_window(ct: dict | None) -> tuple[date, date] | None:
    """解析 ``{start, end}`` 字典为 ``(date, date)`` 区间；任一端解析失败或倒置返回 None。

    将 passage 的 ``content_time``（Task 4 产出的 ``{"start": ..., "end": ...}``
    字典或 None）转成 ``classify_temporal`` 需要的闭区间。两端各用
    :func:`parse_date_window` 严格解析，任一端缺失或格式不符即视为未知；
    起止倒置（start 晚于 end）视为 LLM 产出异常，同样返回 None，避免被
    :func:`classify_temporal` 误判为 compliant。

    Args:
        ct: ``{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`` 字典，未知时为 None。

    Returns:
        ``(start_date, end_date)`` 闭区间；输入非 dict、缺端、解析失败或
        起止倒置时返回 None。
    """
    if not isinstance(ct, dict):
        return None
    s = parse_date_window(ct.get("start"))
    e = parse_date_window(ct.get("end"))
    if s is None or e is None:
        return None
    if s[0] > e[1]:
        return None
    return (s[0], e[1])


def _scope_window(scope: TemporalScope) -> tuple[date, date]:
    """约束区间；单边界时缺的那侧用 date.min/max（无穷）。

    Args:
        scope: 用户明确指定的时间约束。

    Returns:
        ``(start, end)`` 闭区间；缺省边界分别用 ``date.min`` / ``date.max`` 兜底。
    """
    start = scope.start_date if scope.start_date else _DATE_MIN
    end = scope.end_date if scope.end_date else _DATE_MAX
    return (start, end)


def classify_temporal(
    doc_window: tuple[date, date] | None,
    scope: TemporalScope,
) -> Literal["compliant", "partial", "violation", "unknown"]:
    """文档区间 vs 约束区间 → 四档判定。

    - compliant：文档区间完全落在约束区间内（含边界）。
    - violation：文档区间与约束区间完全不相交。
    - partial：文档区间与约束区间部分重叠。
    - unknown：未提供文档区间。

    Args:
        doc_window: 文档覆盖的闭区间，未知时为 None。
        scope: 用户明确指定的时间约束。

    Returns:
        四档判定之一。
    """
    if doc_window is None:
        return "unknown"
    d_start, d_end = doc_window
    s_start, s_end = _scope_window(scope)
    if d_start >= s_start and d_end <= s_end:
        return "compliant"
    if d_end < s_start or d_start > s_end:
        return "violation"
    return "partial"


def timeliness_score(
    status: Literal["compliant", "partial", "violation", "unknown"],
) -> float:
    """四档 → 时效分。

    Args:
        status: ``classify_temporal`` 的四档判定结果。

    Returns:
        compliant→1.0，partial→-0.3，violation→-1.0，unknown→0.0。
    """
    return _TIMELINESS_TABLE[status]


_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_published_date(value: Any) -> date | None:
    """容错解析来源发表日期为单日 ``date``；解析不出返回 None。

    只接受能确定到「日」的输入，保守不推断，不读语义含糊的裸 ``date`` 键：
    1. 严格 ``YYYY-MM-DD`` 整串；
    2. ISO 8601 前缀（如 ``2023-01-15T12:00:00Z``）取日期前缀（arxiv）；
    3. PubMed 风格 ``YYYY Mon DD``（英文月份缩写，大小写不敏感）。
    只有年/年月（如 ``2023``、``2023 Jan``）或语义含糊的值不解析。
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\D", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})\s+([A-Za-z]{3,})\s+(\d{1,2})$", text)
    if m:
        mon = _MONTH_ABBR.get(m.group(2).lower()[:3])
        if mon is not None:
            try:
                return date(int(m.group(1)), mon, int(m.group(3)))
            except ValueError:
                return None
    return None
