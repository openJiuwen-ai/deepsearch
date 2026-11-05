# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""整理 Brief 报告的最终结果。"""

import logging

from openjiuwen.core.session.node import Session

from openjiuwen_deepsearch.algorithm.brief_report.markdown import format_brief_markdown, select_brief_markdown

logger = logging.getLogger(__name__)


def _select_brief_markdown(session: Session, final_result: dict) -> str:
    """从本轮 Brief 状态中选择原始 Markdown。

    Args:
        session: 工作流会话。
        final_result: 收尾前的最终结果快照。

    Returns:
        优先选择校验后的正文；非 Brief 或没有正文时返回空字符串。
    """
    policy = session.get_global_state("search_context.report_type_policy")
    report_type = policy.get("report_type") if isinstance(policy, dict) else getattr(policy, "report_type", None)
    if report_type != "brief":
        return ""
    report = session.get_global_state("search_context.current_report")
    checked, assembled = [
        report.get(name) if isinstance(report, dict) else getattr(report, name, None)
        for name in ("checked_trace_source_report_content", "report_content")
    ]
    return select_brief_markdown(
        checked=checked,
        assembled=assembled,
        response_content=final_result.get("response_content"),
        response_content_type=final_result.get("response_content_type", "text/markdown"),
    )


def prepare_brief_result(session: Session, final_result: dict, language: str | None) -> dict:
    """准备并同步 Brief 的 Markdown 输出，保留已有结果和错误。

    Args:
        session: 工作流会话，用于读取和更新状态。
        final_result: 收尾前的最终结果快照。
        language: 报告语言标识。

    Returns:
        包含 Markdown 产物的最终结果副本。
    """
    result = dict(final_result)
    try:
        result["response_markdown"] = format_brief_markdown(_select_brief_markdown(session, result), language)
    except Exception as exc:
        logger.warning("[EndNode] brief markdown output unavailable: %s", type(exc).__name__)
        result["response_markdown"] = ""
        warning = result.get("warning_info") or ""
        result["warning_info"] = "\n".join(filter(None, (warning, "brief markdown output unavailable")))
        session.update_global_state({"search_context.final_result.warning_info": result["warning_info"]})
    session.update_global_state({"search_context.final_result.response_markdown": result["response_markdown"]})
    return result
