# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import List

from pydantic import BaseModel, Field, ConfigDict


class WebSearchEngineBasicRequestDTO(BaseModel):
    '''web搜索引擎基类对象'''
    space_id: str = Field(..., min_length=1, max_length=255, description="用户空间id")


class WebSearchEngineCreateRequestDTO(WebSearchEngineBasicRequestDTO):
    '''web搜索引擎请求对象'''
    model_config = ConfigDict(from_attributes=True)
    search_engine_name: str = Field(..., min_length=1, max_length=255, description="搜索引擎名称")
    search_api_key: str = Field(..., min_length=1, max_length=255, description="搜索引擎访问api_key")
    search_url: str = Field(..., min_length=1, max_length=255, description="搜索引擎url")
    extension: dict | None = Field(default=None, description="搜索引擎扩展配置")
    is_active: bool | None = Field(default=None, description="搜索引擎是否激活")


class WebSearchEngineGetRequestDTO(WebSearchEngineBasicRequestDTO):
    '''获取指定web搜索引擎请求对象'''
    web_search_engine_id: int = Field(..., description="搜索引擎id")


class WebSearchEngineListRequestDTO(WebSearchEngineBasicRequestDTO):
    '''获取搜索引擎列表请求对象'''
    pass


class WebSearchEngineDeleteRequestDTO(WebSearchEngineBasicRequestDTO):
    '''删除指定web搜索引擎请求对象'''
    web_search_engine_id: int = Field(..., description="搜索引擎id")


class WebSearchEngineUpdateRequestDTO(WebSearchEngineBasicRequestDTO):
    '''更新指定web搜索引擎对象'''
    model_config = ConfigDict(from_attributes=True)
    web_search_engine_id: int = Field(..., description="搜素引擎id")
    search_engine_name: str | None = Field(default=None, min_length=1, max_length=255, description="搜索引擎名称")
    search_api_key: str | None = Field(default=None, min_length=1, max_length=255, description="搜索引擎访问api_key")
    search_url: str | None = Field(default=None, min_length=1, max_length=255, description="搜索引擎url")
    extension: dict | None = Field(default=None, description="搜索引擎扩展配置")
    is_active: bool | None = Field(default=None, description="搜索引擎是否激活")


class WebSearchEnginePostRequestDTO(WebSearchEngineBasicRequestDTO):
    '''获取指定web搜索引擎请求对象并访问'''
    query: str = Field(default="世界上最高的山峰", description="用户搜索query")
    web_search_engine_id: int = Field(..., description="搜索引擎id")


class BasicResponseDTO(BaseModel):
    '''web搜索引擎返回对象基类'''
    model_config = ConfigDict(from_attributes=True)
    code: int = Field(default=200, description="是否成功")
    msg: str = Field(default='success', min_length=1, max_length=255, description="结果信息")


class WebSearchEngineCreateRes(BasicResponseDTO):
    '''创建web搜索引擎返回对象'''
    web_search_engine_id: int = Field(..., description="web搜索引擎id")


class WebSearchEngineGetRes(BasicResponseDTO):
    '''获取指定搜索引擎'''
    search_engine_name: str = Field(..., min_length=1, max_length=255, description="搜索引擎名称")
    search_url: str = Field(..., min_length=1, max_length=255, description="搜索引擎url")
    extension: dict | None = Field(default=None, description="搜索引擎扩展配置")
    is_active: bool | None = Field(default=None, description="搜索引擎是否激活")


class WebSearchEngineDetail(BasicResponseDTO):
    '''获取指定搜索引擎详细信息'''
    search_engine_name: str = Field(..., min_length=1, max_length=255, description="搜索引擎名称")
    search_url: str = Field(..., min_length=1, max_length=255, description="搜索引擎url")
    search_api_key: str = Field(..., min_length=1, max_length=255, description="搜索引擎访问api_key")
    extension: dict | None = Field(default=None, description="搜索引擎扩展配置")
    is_active: bool | None = Field(default=None, description="搜索引擎是否激活")


class WebSearchEngineItem(BaseModel):
    '''web搜索引擎条目'''
    model_config = ConfigDict(from_attributes=True)
    search_engine_name: str = Field(..., min_length=1, max_length=255, description="搜索引擎名称")
    search_url: str = Field(..., min_length=1, max_length=255, description="搜索引擎url")
    web_search_engine_id: int = Field(..., description="搜索引擎id")
    create_time: str = Field(..., min_length=1, max_length=255, description="模板创建时间")
    extension: dict | None = Field(default=None, description="搜索引擎扩展配置")
    is_active: bool | None = Field(default=None, description="搜索引擎是否激活")


class WebSearchEngineListRes(BasicResponseDTO):
    '''获取搜索引擎列表'''
    data: List[WebSearchEngineItem] = Field(default=[], description="搜索引擎列表")


class WebSearchEngineDeleteRes(BasicResponseDTO):
    '''删除指定搜索引擎返回对象'''
    pass


class WebSearchEngineUpdateRes(BasicResponseDTO):
    '''修改指定搜索引擎'''
    web_search_engine_id: int = Field(default=0, description="搜索引擎id")


class WebSearchEnginePostRes(BasicResponseDTO):
    '''搜索引擎返回结果'''
    search_engine_name: str = Field(..., min_length=1, max_length=255, description="搜索引擎名称")
    datas: list = Field(default_factory=list, description="搜索引擎返回结果")
