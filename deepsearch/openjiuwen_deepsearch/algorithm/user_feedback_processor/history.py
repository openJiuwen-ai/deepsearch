# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""用户反馈改写历史记录构建工具。"""


def build_current_outline_update(current_outline, action_result: dict):
    """根据用户反馈动作结果生成需要回写的 outline。

    Args:
        current_outline: 当前 session 中的 ``search_context.current_outline``。
        action_result: 具体动作执行后的结果。

    Returns:
        更新后的 outline；当动作结果不需要回写 outline，或找不到匹配章节时返回 ``None``。
    """
    incremental_plan = action_result.get("incremental_plan")
    matched_section_id = action_result.get("matched_section_id")
    if not incremental_plan or not current_outline or not getattr(current_outline, "sections", None):
        return None

    for section in current_outline.sections:
        if str(section.id) == str(matched_section_id):
            section.plans.append(incremental_plan)
            return current_outline
    return None


def build_rewrite_history_update(
    history: list[dict] | None,
    feedback: dict,
    action_result: dict,
    current_report_content: str,
) -> list[dict] | None:
    """根据反馈动作与执行结果生成新的改写历史。

    Args:
        history: 当前 session 中的 ``rewrite_history``。
        feedback: 已解析并补齐默认值的用户反馈。
        action_result: 具体动作执行后的结果。
        current_report_content: 写入新报告前的当前报告正文。

    Returns:
        list[dict] | None: 新的 ``rewrite_history``；当 sync 未产生内容变化时返回 ``None``。
    """
    if action_result.get("sync_only", False) and action_result["new_report"] == current_report_content:
        return None

    updated_history = list(history or [])
    updated_history.append(build_rewrite_history_item(feedback, action_result))
    if action_result.get("sync_only", False):
        non_sync_history = [item for item in updated_history if item.get("action") != "sync"]
        sync_history = [item for item in updated_history if item.get("action") == "sync"]
        updated_history = non_sync_history + sync_history[-10:]
    return updated_history


def build_rewrite_history_item(feedback: dict, action_result: dict) -> dict:
    """按 action 生成单条改写历史。"""
    selected_text_clean = action_result.get("original_text_clean", feedback.get("selected_text"))
    history_item = {
        "action": feedback.get("action"),
        "rewrite_scope": feedback.get("rewrite_scope"),
        "selected_text": feedback.get("selected_text"),
        "selected_text_clean": selected_text_clean,
        "original_start_offset": action_result["original_start_offset"],
        "original_end_offset": action_result["original_end_offset"],
        "rewritten_text": action_result["rewritten_text"],
        "rewritten_start_offset": action_result["rewritten_start_offset"],
        "rewritten_end_offset": action_result["rewritten_end_offset"],
        "user_instruction": feedback.get("user_instruction", ""),
    }
    _add_action_specific_history_fields(history_item, feedback, action_result)
    return history_item


def _add_action_specific_history_fields(history_item: dict, feedback: dict, action_result: dict) -> None:
    """补充不同 action 的历史字段。"""
    action = feedback.get("action")
    if action == "new_task":
        _add_new_task_history_fields(history_item, action_result)
        return

    if "section_start_offset" in action_result:
        history_item["section_start_offset"] = action_result.get("section_start_offset")
    if "section_end_offset" in action_result:
        history_item["section_end_offset"] = action_result.get("section_end_offset")
    if "collector_summary" in action_result:
        history_item["collector_summary"] = action_result.get("collector_summary", "")


def _add_new_task_history_fields(history_item: dict, action_result: dict) -> None:
    incremental_plan = action_result.get("incremental_plan")
    history_item.update(
        {
            "section_start_offset": action_result.get("section_start_offset"),
            "section_end_offset": action_result.get("section_end_offset"),
            "section_title": action_result.get("section_title", ""),
            "matched_section_id": action_result.get("matched_section_id"),
            "match_mode": action_result.get("match_mode", "none"),
            "assessment_summary": action_result.get("assessment_summary", ""),
            "used_historical_doc_count": action_result.get("used_historical_doc_count", 0),
            "used_new_doc_count": action_result.get("used_new_doc_count", 0),
            "missing_aspects": action_result.get("missing_aspects", []),
            "incremental_plan_title": incremental_plan.title if incremental_plan else "",
        }
    )
    if "edit_strategy" in action_result:
        history_item["edit_strategy"] = action_result.get("edit_strategy", "")
    if "new_subsection_title" in action_result:
        history_item["new_subsection_title"] = action_result.get("new_subsection_title", "")
