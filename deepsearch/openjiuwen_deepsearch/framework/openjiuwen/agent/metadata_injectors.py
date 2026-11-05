"""运行时 metadata 注入器注册表。

metadata 是通用运行时元数据通道（客户端持有回传、服务端不持久化）。
每个用途实现一个注入器并注册到 METADATA_INJECTORS：

- ``matches``：轻量指纹检测（如关键键存在），决定是否消费该 metadata；
- ``validate``：严格结构校验，非法抛 CustomValueException（server 入口用）；
  校验只看结构，不感知请求上下文；
- ``inject``：解析并构造注入结果；返回 None 表示匹配但不激活
- ``force_execution_method``：声明该用途要求的执行模式（如 brief 大纲无依赖
  信息，升级运行必须走并行图）；None 表示不约束。

StartNode 通过 apply_injectors 编排：合并全部激活注入器的 state 更新，
next_node 取首个声明值（多数注入器只补状态、不接管路由）。
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from openjiuwen_deepsearch.algorithm.brief_report.models import BriefOutline, BriefWorkflowState
from openjiuwen_deepsearch.algorithm.query_understanding.intent_recognition import resolve_report_type_policy
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.config.method import ExecutionMethod
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import ResearchIntent
from openjiuwen_deepsearch.utils.constants_utils.node_constants import NodeId

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 机制：注入器接口与编排
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetadataInjection:
    """一次注入的结果：写入 session 的状态更新 + 可选的路由接管。

    Attributes:
        state_updates: 待写入 workflow session 的全局状态键值对
            （形如 ``search_context.xxx``）。
        next_node: 注入后的下一跳节点 ID；None 表示不接管路由，
            由调用方按默认流程路由。
    """

    state_updates: Mapping[str, Any]
    next_node: str | None = None


@dataclass(frozen=True)
class MetadataInjector:
    """metadata 注入器：一个用途一个实例。

    Attributes:
        name: 注入器名称，用于日志。
        matches: 轻量指纹检测，决定是否消费该 metadata。
        validate: 严格结构校验，非法抛 CustomValueException。
        inject: 解析并构造注入结果；返回 None 表示匹配但不激活。
        force_execution_method: 该用途要求的 workflow 执行模式；
            server 在 agent 构建前读取并覆盖请求的 execution_method，
            None 表示不约束。
    """

    name: str
    matches: Callable[[Mapping[str, Any]], bool]
    validate: Callable[[Mapping[str, Any]], None]
    inject: Callable[[Mapping[str, Any], Mapping[str, Any]], MetadataInjection | None]
    force_execution_method: str | None = None


def resolve_forced_execution_method(metadata: Mapping[str, Any]) -> str | None:
    """解析 metadata 激活注入器声明的强制执行模式。

    首个声明 force_execution_method 的匹配注入器生效（first-wins，
    与 apply_injectors 的状态合并策略一致）。

    Args:
        metadata: 请求携带的 metadata。

    Returns:
        强制的执行模式（ExecutionMethod 枚举值）；无匹配或均未声明时返回 None。
    """
    if not isinstance(metadata, Mapping) or not metadata:
        return None
    for injector in METADATA_INJECTORS:
        if injector.matches(metadata) and injector.force_execution_method:
            return injector.force_execution_method
    return None


def validate_metadata(metadata: Mapping[str, Any]) -> None:
    """server 入口严格校验：所有匹配的注入器都必须通过结构校验。

    校验只看结构，不感知请求上下文；report_type 等条件下的忽略契约
    属于运行期消费逻辑，仅由 inject 阶段判断。

    Args:
        metadata: 请求携带的 metadata。
    """
    for injector in METADATA_INJECTORS:
        if injector.matches(metadata):
            injector.validate(metadata)


def apply_injectors(metadata: Any, inputs: Mapping[str, Any]) -> MetadataInjection | None:
    """编排全部激活的注入器；无任何激活时返回 None（走默认流程）。

    state_updates 合并，next_node 取首个声明值；匹配但不激活（inject 返回
    None，如 brief 模式忽略）不算错误。
    """
    if not isinstance(metadata, Mapping) or not metadata:
        return None
    merged_updates: dict[str, Any] = {}
    next_node: str | None = None
    activated = False
    for injector in METADATA_INJECTORS:
        if not injector.matches(metadata):
            continue
        injection = injector.inject(metadata, inputs)
        if injection is None:
            continue
        activated = True
        # first-wins：先注册的注入器对同名状态键保持权威，后续注入器只补充新键
        for key, value in injection.state_updates.items():
            merged_updates.setdefault(key, value)
        if next_node is None:
            next_node = injection.next_node
        logger.info("[MetadataInjectors] Applied injector=%s next_node=%s", injector.name, injection.next_node)
    if not activated:
        return None
    return MetadataInjection(state_updates=merged_updates, next_node=next_node)


# ---------------------------------------------------------------------------
# 用途实现：brief 大纲升级专业版
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedUpgradeMetadata:
    """解析后的升级 metadata。

    Attributes:
        brief_outline: brief 报告大纲（结构基准）。
        research_intent: 上次 brief 运行识别出的报告意图约束。
        language: 报告语言，缺省 zh-CN。
    """

    brief_outline: BriefOutline
    research_intent: ResearchIntent
    language: str


def parse_upgrade_metadata(metadata: object) -> ParsedUpgradeMetadata:
    """解析升级 metadata；结构不合法时抛 CustomValueException。

    metadata 为 brief 报告 final_result.metadata 的回传：brief_outline /
    research_intent 必须可通过模型校验，language 缺省 zh-CN，未知键忽略。

    Args:
        metadata: 请求携带的 metadata 对象。

    Returns:
        解析后的三键结构。

    Raises:
        CustomValueException: metadata 非对象，或 brief_outline /
            research_intent 未通过模型校验
            （PARAM_CHECK_ERROR_UPGRADE_METADATA_INVALID，错误码 200030）。
    """
    if not isinstance(metadata, dict):
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_UPGRADE_METADATA_INVALID.code,
            StatusCode.PARAM_CHECK_ERROR_UPGRADE_METADATA_INVALID.errmsg.format(e="metadata must be an object"),
        )
    try:
        brief_outline = BriefOutline.model_validate(metadata.get("brief_outline") or {})
        research_intent = ResearchIntent.model_validate(metadata.get("research_intent") or {})
    except Exception as e:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_UPGRADE_METADATA_INVALID.code,
            StatusCode.PARAM_CHECK_ERROR_UPGRADE_METADATA_INVALID.errmsg.format(e=e),
        ) from e
    language = metadata.get("language") or "zh-CN"
    return ParsedUpgradeMetadata(
        brief_outline=brief_outline,
        research_intent=research_intent,
        language=language,
    )


def _brief_outline_matches(metadata: Mapping[str, Any]) -> bool:
    """指纹检测：metadata 携带 brief 大纲升级相关键即视为本用途。"""
    return "brief_outline" in metadata or "research_intent" in metadata


def _brief_outline_validate(metadata: Mapping[str, Any]) -> None:
    """严格结构校验：复用 parse_upgrade_metadata，非法抛 CustomValueException。"""
    parse_upgrade_metadata(metadata)


def _brief_outline_inject(metadata: Mapping[str, Any], inputs: Mapping[str, Any]) -> MetadataInjection | None:
    """brief 大纲升级注入：写入上次 brief 运行的意图与大纲，路由直达大纲节点。

    Args:
        metadata: brief 报告 final_result.metadata 的回传。
        inputs: StartNode 的输入，读取 agent_config 判断运行上下文。

    Returns:
        注入结果（research_intent / report_type_policy / language /
        brief_state 四键状态 + next_node=OUTLINE）；brief 模式按契约忽略，
        返回 None 走 brief 正常流程。

    Raises:
        CustomValueException: SDK 直连非并行 Agent 时
            （PARAM_CHECK_ERROR_UPGRADE_EXECUTION_METHOD_CONFLICT，错误码
            200031）；metadata 结构非法时由 parse_upgrade_metadata 抛出
            （错误码 200030）。
    """
    agent_config = inputs.get("agent_config") or {}
    report_type = agent_config.get("report_type")
    # brief 模式按契约忽略升级 metadata，走 brief 正常流程；
    if report_type == "brief":
        return None
    # SDK 直连依赖 agent 的兜底：图在 agent 构建时固定，无法运行期切换到
    # 并行图，显式失败而非静默走依赖流水线（server 路径已在构建前改写
    # execution_method，不会触发此分支）
    actual_method = agent_config.get("execution_method") or ExecutionMethod.PARALLEL.value
    if actual_method != ExecutionMethod.PARALLEL.value:
        raise CustomValueException(
            StatusCode.PARAM_CHECK_ERROR_UPGRADE_EXECUTION_METHOD_CONFLICT.code,
            StatusCode.PARAM_CHECK_ERROR_UPGRADE_EXECUTION_METHOD_CONFLICT.errmsg.format(
                required=ExecutionMethod.PARALLEL.value,
                actual=actual_method,
            ),
        )
    parsed = parse_upgrade_metadata(metadata)
    effective_report_type = report_type or "professional"
    intent_dump = parsed.research_intent.model_dump()
    # 覆盖为本次请求的报告类型，避免残留上次 brief 运行的 report_type
    intent_dump["report_type"] = effective_report_type
    report_type_policy = resolve_report_type_policy(effective_report_type)
    return MetadataInjection(
        state_updates={
            "search_context.research_intent": intent_dump,
            "search_context.report_type_policy": report_type_policy.model_dump(),
            "search_context.language": parsed.language,
            "search_context.brief_state": BriefWorkflowState(outline=parsed.brief_outline).model_dump(),
        },
        next_node=NodeId.OUTLINE.value,
    )


brief_outline_injector = MetadataInjector(
    name="brief_outline_upgrade",
    matches=_brief_outline_matches,
    validate=_brief_outline_validate,
    inject=_brief_outline_inject,
    # brief 大纲只有 goal/research_steps，无依赖结构，升级运行必须走并行图
    force_execution_method=ExecutionMethod.PARALLEL.value,
)


# ---------------------------------------------------------------------------
# 注册表：新用途在此追加
# ---------------------------------------------------------------------------

METADATA_INJECTORS: list[MetadataInjector] = [
    brief_outline_injector,
]
