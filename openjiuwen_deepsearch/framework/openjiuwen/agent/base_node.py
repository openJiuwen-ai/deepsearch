# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
from typing import Union

from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow import WorkflowComponent
from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter

from openjiuwen_deepsearch.common.exception import CustomJiuWenBaseException, CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_metrics import async_time_logger

logger = logging.getLogger(__name__)


class BaseNode(WorkflowComponent):
    """
    节点的封装类，继承自Jiuwen.core.workflow.WorkflowComponent，需要实现invoke函数。
    在本BaseNode中，统一定义了四个函数，各节点的实现类，需要实现这三个私有函数的具体逻辑
    _pre_handle：从Session上下文中获取必要字段
    _do_invoke：核心节点逻辑函数，调用具体算法，该步骤的输入输出与平台解耦，只使用python的基础数据类型
    _post_handle：把必要字段更新到Session上下文中
    * invoke：不需要子类覆写此函数，会调用_do_invoke函数；用来统一注入横切逻辑（如计时、日志、异常处理等）
    """

    def __init__(self):
        super().__init__()

    @async_time_logger("invoke")
    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        '''基础节点的invoke方法'''
        return await self._do_invoke(inputs, session, context)

    def _pre_handle(self, inputs: Input, session: Session, context: ModelContext):
        '''从Session上下文中获取必要字段'''
        raise CustomJiuWenBaseException(StatusCode.JIUWEN_BASE_EXCEPTION_NOT_SUPPORTED.code,
                                        StatusCode.JIUWEN_BASE_EXCEPTION_NOT_SUPPORTED.errmsg)

    async def _do_invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        '''核心节点逻辑函数，调用具体算法，该步骤的输入输出与平台解耦，只使用python的基础数据类型'''
        raise CustomJiuWenBaseException(StatusCode.JIUWEN_BASE_EXCEPTION_NOT_SUPPORTED.code,
                                        "_do_invoke is not supported")

    def _post_handle(self, inputs: Input, algorithm_output: object, session: Session, context: ModelContext):
        '''把必要字段更新到Session上下文中'''
        raise CustomJiuWenBaseException(StatusCode.JIUWEN_BASE_EXCEPTION_NOT_SUPPORTED.code,
                                        "_post_handle is not supported")


def init_router(current_node, next_nodes: Union[str, list[str]]):
    '''
    动态添加节点

    Args:
        current_node: 当前节点ID
        next_nodes: 下一个节点ID或节点ID列表

    Returns:
        BranchRouter: 分支路由实例
    '''
    router = BranchRouter()
    if isinstance(next_nodes, str):
        condition = f"${{{current_node}.next_node}} == {next_nodes!r}"
        router.add_branch(condition, next_nodes)
    elif isinstance(next_nodes, list):
        for next_node in next_nodes:
            condition = f"${{{current_node}.next_node}} == {next_node!r}"
            router.add_branch(condition, next_node)
    else:
        raise CustomValueException(
            StatusCode.WORKFLOW_ROUTER_INIT_TYPE_ERROR.code,
            StatusCode.WORKFLOW_ROUTER_INIT_TYPE_ERROR.errmsg
        )
    return router
