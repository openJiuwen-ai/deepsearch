# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import inspect
from functools import wraps

from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session

from server.core.database import get_db
from server.deepsearch.common.exception.exceptions import (
    PromptTemplateBasicException,
    PromptTemplateNotFoundException,
)
from server.deepsearch.core.manager.prompt_manager import (
    prompt_manager,
    ImportPromptParams,
    UpdatePromptParams,
    ResetPromptParams,
)
from server.schemas.prompt import (
    PromptImportRequest,
    PromptImportResponse,
    PromptListResponse,
    PromptGetResponse,
    PromptDeleteResponse,
    PromptUpdateRequest,
    PromptUpdateResponse,
    PromptResetRequest,
    PromptResetResponse,
)

router = APIRouter()


def handler_response(func):
    """Prompt模板统一响应处理器"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            # 兼容同步和异步函数
            if inspect.iscoroutinefunction(func):
                data = await func(*args, **kwargs)
            else:
                data = func(*args, **kwargs)

            return data

        except PromptTemplateNotFoundException as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            ) from e
        except PromptTemplateBasicException as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            ) from e
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            ) from e

    return wrapper


@router.post("", response_model=PromptImportResponse)
@handler_response
async def import_prompt(
        req: PromptImportRequest,
        db: Session = Depends(get_db)
):
    """导入Prompt模板"""
    params = ImportPromptParams(**req.dict())
    result = await prompt_manager.import_prompt(
        db=db,
        params=params
    )
    return PromptImportResponse(prompt_id=result["prompt_id"])


@router.get("/{space_id}", response_model=PromptListResponse)
@handler_response
def list_prompts(
        space_id: str,
        db: Session = Depends(get_db)
):
    """获取指定空间下的Prompt列表"""
    result = prompt_manager.list_prompts(db, space_id)

    return PromptListResponse(
        data=result.get("data", [])
    )


@router.get("/{space_id}/{prompt_id}", response_model=PromptGetResponse)
@handler_response
def get_prompt(
        space_id: str,
        prompt_id: int,
        db: Session = Depends(get_db)
):
    """获取单个Prompt详情"""
    result = prompt_manager.get_prompt(
        db=db,
        space_id=space_id,
        prompt_id=prompt_id
    )

    return PromptGetResponse(
        prompt_id=result["prompt_id"],
        space_id=result["space_id"],
        prompt_name=result["prompt_name"],
        prompt_type=result["prompt_type"],
        prompt_content=result["prompt_content"],
        default_prompt=result["default_prompt"],
        description=result["description"],
        is_active=result["is_active"],
        create_time=result["create_time"],
        update_time=result["update_time"],
    )


@router.delete("/{space_id}/{prompt_id}", response_model=PromptDeleteResponse)
@handler_response
def delete_prompt(
        space_id: str,
        prompt_id: int,
        db: Session = Depends(get_db)
):
    """删除Prompt模板"""
    prompt_manager.delete_prompt(
        db=db,
        space_id=space_id,
        prompt_id=prompt_id
    )

    return PromptDeleteResponse()


@router.put("", response_model=PromptUpdateResponse)
@handler_response
def update_prompt(
        req: PromptUpdateRequest,
        db: Session = Depends(get_db)
):
    """更新Prompt模板"""
    params = UpdatePromptParams(**req.dict())
    result = prompt_manager.update_prompt(db=db, params=params)

    return PromptUpdateResponse(
        prompt_id=result.get("prompt_id")
    )


@router.post("/reset", response_model=PromptResetResponse)
@handler_response
def reset_prompt(
        req: PromptResetRequest,
        db: Session = Depends(get_db)
):
    """重置Prompt为默认值"""
    params = ResetPromptParams(**req.dict())
    result = prompt_manager.reset_to_default(db=db, params=params)

    return PromptResetResponse(
        prompt_id=result.get("prompt_id")
    )
