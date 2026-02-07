# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import inspect
from functools import wraps

from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session

from server.core.database import get_db
from server.deepsearch.common.exception.exceptions import (
    ReportTemplateBasicException,
    TemplateNotFoundException,
)
from server.deepsearch.core.manager.template_manager import (
    report_template_manager,
    ImportTemplateParams,
    UpdateTemplateParams,
)
from server.schemas.report_template import (
    TemplateImportRequest,
    TemplateImportResponse,
    TemplateListResponse,
    TemplateGetResponse,
    TemplateDeleteResponse,
    TemplateUpdateRequest,
    TemplateUpdateResponse,
)

router = APIRouter()


def handler_response(func):
    """Report template unified response handler"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            # 兼容同步和异步函数
            if inspect.iscoroutinefunction(func):
                data = await func(*args, **kwargs)
            else:
                data = func(*args, **kwargs)

            data.code = status.HTTP_200_OK
            data.msg = "success"
            return data

        except TemplateNotFoundException as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            ) from e
        except ReportTemplateBasicException as e:
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


@router.post("", response_model=TemplateImportResponse)
@handler_response
async def import_template(
        req: TemplateImportRequest,
        db: Session = Depends(get_db)
):
    params = ImportTemplateParams(**req.dict())
    result = await report_template_manager.import_template(
        db=db,
        params=params
    )
    return TemplateImportResponse(template_id=result["template_id"])


@router.get("/{space_id}", response_model=TemplateListResponse)
@handler_response
def list_templates(
        space_id: str,
        db: Session = Depends(get_db)
):
    """Get template list by space_id"""
    result = report_template_manager.list_templates(db, space_id)

    return TemplateListResponse(
        data=result.get("data", [])
    )


@router.get("/{space_id}/{template_id}", response_model=TemplateGetResponse)
@handler_response
def get_template(
        space_id: str,
        template_id: int,
        db: Session = Depends(get_db)
):
    """Get template content by space_id and template_id"""
    result = report_template_manager.get_template_content(
        db=db,
        space_id=space_id,
        template_id=template_id
    )

    return TemplateGetResponse(
        template_content=result.get("template_content", "")
    )


@router.delete("/{space_id}/{template_id}", response_model=TemplateDeleteResponse)
@handler_response
def delete_template(
        space_id: str,
        template_id: int,
        db: Session = Depends(get_db)
):
    """Delete a specific template"""
    report_template_manager.delete_template(
        db=db,
        space_id=space_id,
        template_id=template_id
    )

    return TemplateDeleteResponse()


@router.put("", response_model=TemplateUpdateResponse)
@handler_response
def update_template(
        req: TemplateUpdateRequest,
        db: Session = Depends(get_db)
):
    """Update a specific template"""
    params = UpdateTemplateParams(**req.dict())
    result = report_template_manager.update_template(db=db, params=params)

    return TemplateUpdateResponse(
        template_id=result.get("template_id")
    )
