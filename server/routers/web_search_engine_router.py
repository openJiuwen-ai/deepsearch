# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from functools import wraps

from fastapi import APIRouter, status, Depends, HTTPException, Body
from sqlalchemy.orm import Session

from server.core.database import get_db
from server.deepsearch.common.exception.exceptions import WebSearchEngineBasicException, ValidationError
from server.deepsearch.core.manager.repositories.web_search_engine_repository import \
    WebSearchEngineRepository
from server.deepsearch.core.manager.web_search_engine_service import WebSearchEngineService
from server.schemas.web_search_engine import WebSearchEngineCreateRes, WebSearchEngineCreateRequestDTO, \
    WebSearchEngineGetRes, WebSearchEngineGetRequestDTO, WebSearchEngineListRes, WebSearchEngineListRequestDTO, \
    WebSearchEngineUpdateRes, WebSearchEngineUpdateRequestDTO, WebSearchEngineDeleteRes, \
    WebSearchEngineDeleteRequestDTO, WebSearchEnginePostRequestDTO, WebSearchEnginePostRes

DEFAULT_QUERY = "人工智能的发展"

router = APIRouter()


def get_web_search_engine_service(db: Session = Depends(get_db)) -> WebSearchEngineService:
    '''依赖注入， 获取WebSearchEngineService实例'''
    web_search_engine_repository = WebSearchEngineRepository(db)
    return WebSearchEngineService(web_search_engine_repository)


def handler_response(func):
    """ web联网增强引擎相关结果处理"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            data = func(*args, **kwargs)
            data.code = 200
            data.msg = "success"
            return data
        except Exception as e:
            if WebSearchEngineBasicException.CODE in str(e) or isinstance(e, ValidationError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
            elif isinstance(e, HTTPException):
                raise e
            else:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    return wrapper


@router.post("/", response_model=WebSearchEngineCreateRes, status_code=status.HTTP_201_CREATED)
@handler_response
def create_web_search_engine(request: WebSearchEngineCreateRequestDTO,
                             service: WebSearchEngineService = Depends(get_web_search_engine_service)):
    return service.create_web_search_engine(request)


@router.get("/{space_id}/{web_search_engine_id}", response_model=WebSearchEngineGetRes, status_code=status.HTTP_200_OK)
@handler_response
def get_web_search_engine(space_id: str, web_search_engine_id: int,
                          service: WebSearchEngineService = Depends(get_web_search_engine_service)):
    request = WebSearchEngineGetRequestDTO(space_id=space_id, web_search_engine_id=web_search_engine_id)
    return service.get_web_search_engine_by_id(request)


@router.get("/{space_id}", response_model=WebSearchEngineListRes, status_code=status.HTTP_200_OK)
@handler_response
def get_web_search_engine_list(space_id: str,
                               service: WebSearchEngineService = Depends(get_web_search_engine_service)):
    request = WebSearchEngineListRequestDTO(space_id=space_id)
    return service.get_web_search_engine_list(request=request)


@router.put("/", response_model=WebSearchEngineUpdateRes, status_code=status.HTTP_200_OK)
@handler_response
def update_web_search_engine(request: WebSearchEngineUpdateRequestDTO,
                             service: WebSearchEngineService = Depends(get_web_search_engine_service)):
    return service.update_web_search_engine(request)


@router.delete("/{space_id}/{web_search_engine_id}", response_model=WebSearchEngineDeleteRes,
               status_code=status.HTTP_200_OK)
@handler_response
def delete_web_search_engine(space_id: str, web_search_engine_id: int,
                             service: WebSearchEngineService = Depends(get_web_search_engine_service)):
    request = WebSearchEngineDeleteRequestDTO(space_id=space_id, web_search_engine_id=web_search_engine_id)
    return service.delete_web_search_engine_by_id(request)


@router.post("/{space_id}/{web_search_engine_id}", response_model=WebSearchEnginePostRes,
             status_code=status.HTTP_200_OK)
@handler_response
def post_web_search_engine(space_id: str, web_search_engine_id: int,
                           query: str = Body(default=DEFAULT_QUERY, embed=True),
                           service: WebSearchEngineService = Depends(get_web_search_engine_service)):
    request = WebSearchEnginePostRequestDTO(space_id=space_id, web_search_engine_id=web_search_engine_id,
                                            query=query)
    return service.post_web_search_engine(request)
