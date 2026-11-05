# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""从已有报告记录生成轻量进度，不保存正文或独立累计历史。"""

from dataclasses import dataclass
from typing import Any


MAX_PROCESSING_SECTIONS = 5
MAX_SECTION_TITLE_LENGTH = 160
MAX_SECTION_ID_LENGTH = 128


def _value(record: Any, name: str, default: Any = None) -> Any:
    """读取业务模型或已序列化记录中的字段。

    Args:
        record: 业务模型或字典。
        name: 字段名称。
        default: 字段缺失时的值。

    Returns:
        对应字段值。
    """
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def _items(record: Any, name: str) -> list:
    """读取列表字段，缺失或非列表时返回空列表。

    Args:
        record: 业务记录。
        name: 列表字段名。

    Returns:
        已有列表或空列表。
    """
    value = _value(record, name)
    return value if isinstance(value, list) else []


def _query_result_count(queries: list) -> int:
    """统计已有查询记录中的结果条目，不新增跨查询去重。

    Args:
        queries: 已完成查询记录。

    Returns:
        各查询已有结果条目数之和。
    """
    return sum(len(_items(query, "doc_infos")) for query in queries)


@dataclass(frozen=True)
class ProcessingSection:
    """只读章节展示信息。

    Attributes:
        section_id: 有长度上限的章节标识。
        title: 有长度上限的章节标题。
    """

    section_id: str
    title: str


class ProgressSummary:
    """按业务记录身份替换数量，避免重试和重复采集造成额外累计。"""

    def __init__(self) -> None:
        """初始化当前大纲、结果数量和章节状态。"""
        self._outline_key = None
        self._sections: dict[str, str] = {}
        self._counts: dict[tuple[str, str, str], int] = {}
        self._completed: set[str] = set()
        self._processing: set[str] = set()
        self._brief_count = 0
        self._brief = False
        self._stale_report_id = None
        self._report_id = None

    def set_outline(self, outline: Any, *, brief: bool = False) -> None:
        """同步当前大纲，版本变化时移除上一版本的观察数据。

        Args:
            outline: 已有大纲模型或字典。
            brief: 是否使用 Brief 业务记录。
        """
        sections = _items(outline, "sections")
        if outline is None or not isinstance(_value(outline, "title"), str):
            return
        entries = [(str(_value(section, "id", "")), str(_value(section, "title", "")))
                   for section in sections]
        key = (brief, _value(outline, "id", ""), _value(outline, "title"), tuple(entries))
        if key != self._outline_key:
            if self._outline_key is not None:
                self._stale_report_id = self._report_id
            self._outline_key = key
            self._counts.clear()
            self._completed.clear()
            self._processing.clear()
            self._brief_count = 0
        self._brief = brief
        self._sections = dict(entries)

    def restore_main(self, state: Any) -> None:
        """从主图现有记录恢复进度，支持交互后在其他 worker 继续。

        Args:
            state: 主图 search_context，不包含额外进度持久化字段。
        """
        policy = _value(state, "report_type_policy")
        brief_state = _value(state, "brief_state")
        if _value(policy, "report_type") == "brief":
            self.set_outline(_value(brief_state, "outline"), brief=True)
            self._brief_count = len(_items(_value(brief_state, "collection_context"), "search_results"))
            for chapter in _items(brief_state, "chapters"):
                content = _value(chapter, "raw_markdown", "")
                self.finish_section(str(_value(chapter, "section_id", "")),
                                    isinstance(content, str) and bool(content.strip()))
            return
        outline = _value(state, "current_outline")
        self.set_outline(outline)
        for section in _items(outline, "sections"):
            section_id = str(_value(section, "id", ""))
            for plan in _items(section, "plans"):
                for step in _items(plan, "steps"):
                    key = (section_id, str(_value(plan, "id", "")), str(_value(step, "id", "")))
                    self._counts[key] = _query_result_count(_items(step, "retrieval_queries"))
        report = _value(state, "current_report")
        report_id = _value(report, "id")
        if not report_id or report_id == self._stale_report_id:
            return
        # 大纲已修改但旧报告尚未替换时，不能把旧章节当作新版本已完成。
        history = _items(state, "history_outlines")
        if history and _value(history[-1], "id") != _value(outline, "id"):
            return
        self._report_id = report_id
        for chapter in _items(report, "sub_reports"):
            section_id = str(_value(chapter, "section_id", ""))
            content = _value(_value(chapter, "content"), "sub_report_content_text", "")
            if _value(chapter, "section_task") == self._sections.get(section_id):
                self.finish_section(section_id, isinstance(content, str) and bool(content.strip()))

    def record_collector(self, collector: Any, *, identity: tuple[str, str, str] | None = None) -> None:
        """替换某章节、计划和步骤的已有查询结果数量。

        Args:
            collector: 当前 collector_context。
            identity: 运行期传入的完整章节、计划、步骤标识；未指定时沿用采集记录中的标识。
        """
        if self._brief or not isinstance(_value(collector, "history_queries"), list):
            return
        key = identity if identity is not None else tuple(
            str(_value(collector, name, "")) for name in ("section_idx", "plan_idx", "step_idx")
        )
        if key[0] in self._sections:
            self._counts[key] = _query_result_count(_items(collector, "history_queries"))

    def start_section(self, section_id: str) -> None:
        """记录已开始处理的当前大纲章节。

        Args:
            section_id: 原始章节标识。
        """
        if section_id in self._sections:
            self._completed.discard(section_id)
            self._processing.add(section_id)

    def finish_section(self, section_id: str, success: bool) -> None:
        """结束章节处理，只有有效成功结果计入完成数。

        Args:
            section_id: 原始章节标识。
            success: 章节是否正常返回有效内容。
        """
        self._processing.discard(section_id)
        if section_id in self._sections:
            if success:
                self._completed.add(section_id)
            else:
                self._completed.discard(section_id)

    def snapshot(self) -> tuple[int, int | None, int, tuple[ProcessingSection, ...]]:
        """生成仅含数量和有界章节元数据的快照。

        Returns:
            资料条目数、章节总数、完成章节数和最多五个处理中的章节。
        """
        processing = tuple(
            ProcessingSection(section_id[:MAX_SECTION_ID_LENGTH], title[:MAX_SECTION_TITLE_LENGTH])
            for section_id, title in self._sections.items() if section_id in self._processing
        )[:MAX_PROCESSING_SECTIONS]
        return (
            self._brief_count if self._brief else sum(self._counts.values()),
            len(self._sections) if self._outline_key is not None else None,
            len(self._completed), processing,
        )
