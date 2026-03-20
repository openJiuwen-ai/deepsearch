#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
from typing import Dict, Type, TypeVar, Union

from fastapi import HTTPException, status
from pydantic import BaseModel

from server.schemas.common import ResponseModel

T = TypeVar('T', bound=BaseModel)


def validate_request(request: Union[Dict, BaseModel], model_class: Type[T]) -> T:
    """验证请求数据并转换为对应的模型类"""
    # 如果 request 已经是模型实例，直接返回
    if isinstance(request, model_class):
        return request
    # 如果是字典，转换为模型实例
    if isinstance(request, dict):
        return model_class(**request)
    # 其他情况，尝试转换为字典再创建模型
    return model_class(**dict(request))


def handle_response(res: ResponseModel) -> ResponseModel:
    if res.code == status.HTTP_200_OK:
        return res
    raise HTTPException(status_code=res.code, detail=res.message)
