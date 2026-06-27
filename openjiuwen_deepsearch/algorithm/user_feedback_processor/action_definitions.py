# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import json
from dataclasses import dataclass
from enum import Enum


class UserFeedbackActionCategory(str, Enum):
    """用户反馈动作的大类。"""

    SYNONYM_REWRITE = "synonym_rewrite"
    SUPPLEMENTARY_SEARCH = "supplementary_search"
    NEW_TASK = "new_task"
    TRUTH_VERIFICATION = "truth_verification"
    SECTION_CHANGE = "section_change"
    SYNC = "sync"
    FINISH = "finish"


class SynonymRewriteActionSubcategory(str, Enum):
    """同义改写小类动作。"""

    EXPAND = "expand"
    SHORTEN = "shorten"
    POLISH = "polish"


class SupplementarySearchActionSubcategory(str, Enum):
    """补充搜索小类动作。"""

    SUPPLEMENTARY_SEARCH = "supplementary_search"


class NewTaskActionSubcategory(str, Enum):
    """新增任务小类动作。"""

    NEW_TASK = "new_task"


class TruthVerificationActionSubcategory(str, Enum):
    """内容真实性核验小类动作。"""

    TRUTH_VERIFICATION = "truth_verification"


class FinishActionSubcategory(str, Enum):
    """完成任务小类动作。"""

    FINISH = "finish"


class SyncActionSubcategory(str, Enum):
    """整篇报告同步小类动作。"""

    SYNC = "sync"


ResolvedActionSubcategory = (
    SynonymRewriteActionSubcategory
    | SupplementarySearchActionSubcategory
    | NewTaskActionSubcategory
    | TruthVerificationActionSubcategory
    | SyncActionSubcategory
    | FinishActionSubcategory
)


@dataclass(frozen=True)
class UserInputActionMapping:
    """将前端 action 字符串映射为统一动作大类定义。"""

    action_category: UserFeedbackActionCategory
    action_subcategory: ResolvedActionSubcategory


@dataclass(frozen=True)
class ResolvedUserAction:
    """由 ``feedback`` 解析得到的规范化动作（大类 + 小类）。"""

    action_category: UserFeedbackActionCategory
    action_subcategory: ResolvedActionSubcategory


@dataclass(frozen=True)
class UserFeedbackRewriteStreamResult:
    """成功改写后发送给前端的结构化结果。"""

    original_text: str
    original_start_offset: int
    original_end_offset: int
    rewritten_text: str
    rewritten_start_offset: int
    rewritten_end_offset: int
    action_category: UserFeedbackActionCategory
    action_subcategory: ResolvedActionSubcategory


# 前端输入动作到统一动作定义的映射表。
# 保留原始 action 字符串作为 key，避免协议细节散落在业务代码中。
USER_INPUT_ACTION_MAP: dict[str, UserInputActionMapping] = {
    # 同义改写
    "expand": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
        action_subcategory=SynonymRewriteActionSubcategory.EXPAND,
    ),
    "shorten": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
        action_subcategory=SynonymRewriteActionSubcategory.SHORTEN,
    ),
    "polish": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.SYNONYM_REWRITE,
        action_subcategory=SynonymRewriteActionSubcategory.POLISH,
    ),

    # 补充搜索
    "supplementary_search": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.SUPPLEMENTARY_SEARCH,
        action_subcategory=SupplementarySearchActionSubcategory.SUPPLEMENTARY_SEARCH,
    ),

    # 新增任务
    "new_task": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.NEW_TASK,
        action_subcategory=NewTaskActionSubcategory.NEW_TASK,
    ),

    # 内容真实性核验
    "truth_verification": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.TRUTH_VERIFICATION,
        action_subcategory=TruthVerificationActionSubcategory.TRUTH_VERIFICATION,
    ),

    # 整篇同步
    "sync": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.SYNC,
        action_subcategory=SyncActionSubcategory.SYNC,
    ),

    # 完成任务
    "finish": UserInputActionMapping(
        action_category=UserFeedbackActionCategory.FINISH,
        action_subcategory=FinishActionSubcategory.FINISH,
    ),
}

# SynonymRewriter 支持的动作集合。
SYNONYM_REWRITE_ACTIONS: frozenset[str] = frozenset(
    action
    for action, mapping in USER_INPUT_ACTION_MAP.items()
    if mapping.action_category == UserFeedbackActionCategory.SYNONYM_REWRITE
)


def resolve_user_input_action(action: str) -> UserInputActionMapping:
    """根据前端 action 获取统一映射定义。

    Args:
        action: 前端传入的动作字符串。

    Returns:
        UserInputActionMapping: 统一的动作映射定义。

    Raises:
        KeyError: 当 action 不在映射表中时抛出。
    """
    return USER_INPUT_ACTION_MAP[action]


def resolve_feedback_action(feedback: dict) -> ResolvedUserAction:
    """从 ``feedback["action"]`` 解析出 ``ResolvedUserAction``。

    Args:
        feedback: 已解析的用户反馈字典，须包含合法字符串键 ``action``。

    Returns:
        与 ``USER_INPUT_ACTION_MAP`` 一致的大类与小类枚举组合。

    Raises:
        KeyError: ``action`` 不在映射表中时，由 ``resolve_user_input_action`` 抛出。
    """

    mapping = resolve_user_input_action(feedback["action"])
    return ResolvedUserAction(
        action_category=mapping.action_category,
        action_subcategory=mapping.action_subcategory,
    )


def _is_report_feedback_payload(message: str) -> bool:
    """若为报告改写约定的 JSON（顶层含 USER_INPUT_ACTION_MAP 中的 action），则视为报告交互负载。"""
    if not message or not message.strip().startswith("{"):
        return False
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    action = data.get("action")
    return isinstance(action, str) and action in USER_INPUT_ACTION_MAP
