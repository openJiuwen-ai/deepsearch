#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
from typing import Dict, Type, TypeVar

from fastapi import HTTPException, status
from pydantic import BaseModel

from server.schemas.common import ResponseModel

T = TypeVar('T', bound=BaseModel)


def validate_request(request: Dict, model_class: Type[T]) -> T:
    """验证请求数据并转换为对应的模型类"""
    return model_class(**request)


def handle_response(res: ResponseModel) -> ResponseModel:
    if res.code == status.HTTP_200_OK:
        return res
    raise HTTPException(status_code=res.code, detail=res.message)
