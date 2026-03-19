from dataclasses import dataclass
from enum import Enum


class UserFeedbackActionCategory(str, Enum):
    """用户反馈动作的大类"""

    SYNONYM_REWRITE = "synonym_rewrite"
    SUPPLEMENTARY_SEARCH = "supplementary_search"
    NEW_TASK = "new_task"
    SECTION_CHANGE = "section_change"
    FINISH = "finish"


class SynonymRewriteActionSubcategory(str, Enum):
    """同义改写小类动作。"""

    EXPAND = "expand"
    SHORTEN = "shorten"
    POLISH = "polish"


class FinishActionSubcategory(str, Enum):
    """完成任务小类动作。"""

    FINISH = "finish"


@dataclass(frozen=True)
class UserInputActionMapping:
    """将前端 action 字符串映射为统一的大类/小类定义。

    目前只有“同义改写”大类存在小类细分，因此 `action_subcategory`
    对其他大类允许为空。
    """

    action_category: UserFeedbackActionCategory
    action_subcategory: SynonymRewriteActionSubcategory | FinishActionSubcategory | None = None


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
    action_subcategory: SynonymRewriteActionSubcategory


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
    """根据前端 action 获取统一映射定义。"""

    return USER_INPUT_ACTION_MAP[action]
