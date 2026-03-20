#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import json
from typing import List, Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, status, UploadFile, File, Form
from pydantic import ValidationError
from openjiuwen.core.common.logging import logger

from server.routers.common import handle_response, validate_request
import server.local_retrieval.core.manager.knowledge_base as kb_mgr
from server.schemas.knowledge_base import (
    DocumentDeleteRequest,
    DocumentListRequest,
    DocumentListResponse,
    DocumentProcessRequest,
    DocumentProcessResponse,
    DocumentStatusListResponse,
    DocumentStatusRequest,
    DocumentUpdateRequest,
    DocumentUploadBatchResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseGet,
    KnowledgeBaseListRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponseCreate,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
    KnowledgeBaseUpdateRequest,
    TaskProgressRequest,
    TaskProgressResponse,
)
from server.schemas.common import ResponseModel

knowledge_base_router = APIRouter()


@knowledge_base_router.post("/create", response_model=ResponseModel[KnowledgeBaseResponseCreate])
async def knowledge_base_create(
        request: KnowledgeBaseCreate,
):
    """
    创建新的知识库

    Args:
        request (dict): 包含创建需求的请求体数据，需符合KnowledgeBaseCreate模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，其中封装了创建成功的知识库详情及元数据。
        如果创建失败，则包含相应的错误码与提示信息。
    """
    try:
        req = validate_request(request, KnowledgeBaseCreate)
        res = kb_mgr.knowledge_base_create(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[KB_CREATE] Knowledge base created - ID: {res.data.get('id')}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[KB_CREATE] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="knowledge base create failed") from e


@knowledge_base_router.post("/delete", response_model=ResponseModel[None])
async def knowledge_base_delete(
        request: KnowledgeBaseGet,
):
    """
    删除指定知识库

    Args:
        request (dict): 包含删除需求的请求体数据，需符合KnowledgeBaseGet模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，其中封装了删除成功的知识库详情及元数据。
        如果删除失败，则包含相应的错误码与提示信息。
    """
    try:
        req = validate_request(request, KnowledgeBaseGet)
        res = await kb_mgr.knowledge_base_delete(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[KB_DELETE] Knowledge base deleted - ID: {req.kb_id}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[KB_DELETE] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="knowledge base delete failed") from e


@knowledge_base_router.post("/update", response_model=ResponseModel[None])
async def knowledge_base_update(
        request: KnowledgeBaseUpdateRequest,
):
    """
    更新知识库

    Args:
        request (dict): 包含更新需求的请求体数据，需符合KnowledgeBaseUpdateRequest模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，其中封装了更新成功的消息。
        如果更新失败，则包含相应的错误码与提示信息。
    """
    try:
        req = validate_request(request, KnowledgeBaseUpdateRequest)
        res = kb_mgr.knowledge_base_update(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[KB_UPDATE] Knowledge base updated - ID: {req.kb_id}"
            )
        return handle_response(res)
    except HTTPException:
        # 重新抛出 HTTPException，不要转换为 500
        raise
    except ValidationError as e:
        logger.error(
            f"[KB_UPDATE] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="knowledge base update failed") from e
    except Exception as e:
        logger.error(
            f"[KB_UPDATE] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@knowledge_base_router.post("/upload", response_model=ResponseModel[DocumentUploadBatchResponse])
async def knowledge_base_upload_documents(
        files: List[UploadFile] = File(..., description="要上传的文件列表（支持多文件）"),
        space_id: str = Form(..., description="空间ID"),
        kb_id: str = Form(..., description="知识库ID"),
        metadata: Optional[str] = Form(None, description="文档元数据（JSON字符串，可选）"),
):
    """
    上传文档到知识库（支持多文件上传）

    Args:
        files (List[UploadFile]): 要上传的文件列表，支持同时上传多个文件
        space_id (str): 空间ID
        kb_id (str): 知识库ID
        metadata (Optional[str]): 文档元数据，JSON字符串格式（可选）

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含上传结果：
        - success_count: 成功上传的文件数量
        - failed_count: 上传失败的文件数量
        - documents: 上传成功的文档列表（包含 doc_id, name, file_size, status）
    """
    try:
        # 验证文件列表不为空
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files provided"
            )

        # 允许的文件类型
        allowed_file_extensions = {'.pdf', '.doc', '.docx', '.txt', '.md'}

        # 验证文件类型
        invalid_files = []
        for file in files:
            if not file.filename:
                invalid_files.append("未命名文件")
                continue

            file_ext = Path(file.filename).suffix.lower()
            if file_ext not in allowed_file_extensions:
                invalid_files.append(file.filename)

        if invalid_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的文件类型: {', '.join(invalid_files)}。仅支持: {', '.join(sorted(allowed_file_extensions))}"
            )
        
        # 注意：文件大小限制在 Manager 层检查，因为需要读取文件内容后才能获取实际大小

        # 解析元数据（如果提供）
        doc_metadata = None
        if metadata:
            try:
                doc_metadata = json.loads(metadata)
            except json.JSONDecodeError:
                logger.warning(
                    "[KB_UPLOAD] Invalid metadata JSON"
                )
                # 如果元数据格式错误，继续处理，但不使用元数据

        # 调用 Manager 层处理文件上传（异步）
        res = await kb_mgr.document_upload(
            space_id=space_id,
            kb_id=kb_id,
            files=files,
            metadata=doc_metadata,
        )

        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[KB_UPLOAD] Documents uploaded - KB ID: {kb_id}, "
                f"Success: {res.data.get('success_count', 0)}, "
                f"Failed: {res.data.get('failed_count', 0)}"
            )
        else:
            logger.error(
                f"[KB_UPLOAD] Upload failed - KB ID: {kb_id}, "
                f"Error: {res.message}"
            )

        return handle_response(res)

    except HTTPException:
        raise
    except ValidationError as e:
        logger.error(
            f"[KB_UPLOAD] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document upload validation failed") from e
    except Exception as e:
        logger.error(
            f"[KB_UPLOAD] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@knowledge_base_router.post("/search", response_model=ResponseModel[KnowledgeBaseSearchResponse])
async def knowledge_base_search(
        request: KnowledgeBaseSearchRequest,
):
    """
    查询知识库（支持分页）

    Args:
        request (dict): 包含查询需求的请求体数据，需符合KnowledgeBaseSearchRequest模型定义。
            - space_id: 空间ID
            - query: 查询词（查询词完整出现在知识库名称或描述中，大小写不敏感）
            - page: 页码，从1开始（可选，默认1）
            - page_size: 每页大小（可选，默认10，最大100）

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含查询结果：
        - knowledge_bases: 匹配的知识库列表
        - total: 总记录数
        - page: 当前页码
        - page_size: 每页大小
        - total_pages: 总页数
    """
    try:
        req = validate_request(request, KnowledgeBaseSearchRequest)
        res = kb_mgr.knowledge_base_search(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                "[KB_SEARCH] Knowledge bases searched - "
                f"Query: '{req.query}', Found: {len(res.data.get('knowledge_bases', [])) if res.data else 0}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[KB_SEARCH] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="knowledge base search failed") from e
    except Exception as e:
        logger.error(
            f"[KB_SEARCH] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@knowledge_base_router.post("/list", response_model=ResponseModel[KnowledgeBaseListResponse])
async def knowledge_base_list(
        request: KnowledgeBaseListRequest,
):
    """
    获取知识库列表（支持分页）

    Args:
        request (dict): 包含查询需求的请求体数据，需符合KnowledgeBaseListRequest模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含知识库列表：
        - items: 知识库列表数组，每个元素包含：
          - name: 知识库名称
          - desc: 知识库描述
          - id: 知识库ID
          - type: 知识库类型（固定为"text"）
          - created_at: 创建时间（格式：YYYY-MM-DD）
          - updated_at: 更新时间（格式：YYYY-MM-DD）
        - total: 总记录数
        - page: 当前页码
        - size: 每页大小
    """
    try:
        req = validate_request(request, KnowledgeBaseListRequest)
    except ValidationError:
        # 验证失败时，使用默认值调用 manager 层（manager 层会返回空列表）
        req = KnowledgeBaseListRequest(
            space_id=request.get("space_id", "") if isinstance(request, dict) else "",
            page=request.get("page", 1) if isinstance(request, dict) else 1,
            size=request.get("size", 10) if isinstance(request, dict) else 10
        )

    res = kb_mgr.knowledge_base_list(req)
    if res.code == status.HTTP_200_OK:
        logger.info(
            f"[KB_LIST] Knowledge base list retrieved - Space ID: {req.space_id}, "
            f"Count: {len(res.data.get('items', []))}"
        )
    # 直接返回结果，manager 层已经确保总是返回 200 和正常数据结构
    return res


@knowledge_base_router.post("/documents/status", response_model=ResponseModel[DocumentStatusListResponse])
async def document_get_status(
        request: DocumentStatusRequest,
):
    """
    批量查询文档状态

    Args:
        request (dict): 包含查询需求的请求体数据，需符合DocumentStatusRequest模型定义。
            - space_id: 空间ID
            - kb_id: 知识库ID
            - doc_id_list: 文档ID列表（支持批量查询）

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含文档状态列表：
        - items: 文档状态列表，每个元素包含：
          - id: 文档ID
          - status: 文档状态（uploading, uploaded, processing, indexing, indexed, failed, deleted）
          - name: 文档名称（可选）
    """
    try:
        req = validate_request(request, DocumentStatusRequest)
        res = kb_mgr.document_get_status_batch(req)
        if res.code == status.HTTP_200_OK:
            items_count = len(res.data.get('items', [])) if res.data else 0
            logger.info(
                f"[DOC_STATUS] Document status retrieved - "
                f"Space ID: {req.space_id}, KB ID: {req.kb_id}, "
                f"Requested: {len(req.doc_id_list)}, Found: {items_count}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[DOC_STATUS] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="document status query failed") from e
    except Exception as e:
        logger.error(
            f"[DOC_STATUS] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@knowledge_base_router.post("/process", response_model=ResponseModel[DocumentProcessResponse])
async def knowledge_base_process_documents(
        request: DocumentProcessRequest,
):
    """
    启动文档处理流程

    Args:
        request (dict): 包含处理需求的请求体数据，需符合DocumentProcessRequest模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含处理结果：
        - task_id: 处理任务ID
        - processed_count: 已启动处理的文档数量
        - failed_count: 启动失败的文档数量
        - failed_files: 启动失败的文件ID列表
    """
    try:
        req = validate_request(request, DocumentProcessRequest)
        res = await kb_mgr.document_process(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[KB_PROCESS] Document processing started - Task ID: {res.data.get('task_id')}, "
                f"KB ID: {req.kb_id}, Processed: {res.data.get('processed_count', 0)}, "
                f"Failed: {res.data.get('failed_count', 0)}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[KB_PROCESS] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="knowledge base process failed") from e


@knowledge_base_router.post("/task/progress", response_model=ResponseModel[TaskProgressResponse])
async def task_progress(
        request: TaskProgressRequest,
):
    """
    查询文档处理任务进度

    Args:
        request (dict): 包含查询需求的请求体数据，需符合TaskProgressRequest模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含任务进度
    """
    try:
        req = validate_request(request, TaskProgressRequest)
        res = kb_mgr.task_progress(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[TASK_PROGRESS] Task progress retrieved - Task ID: {req.task_id}, "
                f"KB ID: {req.kb_id}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[TASK_PROGRESS] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task progress query failed") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[TASK_PROGRESS] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@knowledge_base_router.post("/documents/list", response_model=ResponseModel[DocumentListResponse])
async def document_list(
        request: DocumentListRequest,
):
    """
    获取知识库文档列表（支持分页）

    Args:
        request (dict): 包含查询需求的请求体数据，需符合DocumentListRequest模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，包含文档列表：
        - items: 文档列表数组，每个元素包含：
          - name: 文档名称
          - id: 文档ID
          - created_at: 创建时间（格式：YYYY-MM-DD）
          - updated_at: 更新时间（格式：YYYY-MM-DD）
        - total: 总记录数
        - page: 当前页码
        - size: 每页大小
    """
    try:
        req = validate_request(request, DocumentListRequest)
        res = kb_mgr.document_list(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[DOC_LIST] Document list retrieved - Space ID: {req.space_id}, "
                f"KB ID: {req.kb_id}, Count: {len(res.data.get('items', []))}"
            )
        return handle_response(res)
    except ValidationError as e:
        logger.error(
            f"[DOC_LIST] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="document list failed") from e


@knowledge_base_router.post("/documents/update", response_model=ResponseModel[None])
async def document_update(
        request: DocumentUpdateRequest,
):
    """
    更新文档信息（当前只支持更新文档名称）

    Args:
        request (dict): 包含更新需求的请求体数据，需符合DocumentUpdateRequest模型定义。

    Returns:
        ResponseModel[dict]: 标准化响应对象，其中封装了更新成功的消息。
        如果更新失败，则包含相应的错误码与提示信息。
    """
    try:
        req = validate_request(request, DocumentUpdateRequest)
        res = kb_mgr.document_update(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[DOC_UPDATE] Document updated - Doc ID: {req.document_id}, KB ID: {req.kb_id}"
            )
        return handle_response(res)
    except HTTPException:
        # 重新抛出 HTTPException，不要转换为 500
        raise
    except ValidationError as e:
        logger.error(
            f"[DOC_UPDATE] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="document update failed") from e
    except Exception as e:
        logger.error(
            f"[DOC_UPDATE] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@knowledge_base_router.post("/documents/delete", response_model=ResponseModel[None])
async def document_delete(
        request: DocumentDeleteRequest,
):
    """
    删除文档（支持批量删除）

    Args:
        request (dict): 包含删除需求的请求体数据，需符合DocumentDeleteRequest模型定义。
            - space_id: 空间ID
            - kb_id: 知识库ID
            - document_ids: 文档ID列表（数组）

    Returns:
        ResponseModel[dict]: 标准化响应对象，其中封装了删除成功的消息。
        如果删除失败，则包含相应的错误码与提示信息。
    """
    try:
        req = validate_request(request, DocumentDeleteRequest)
        res = await kb_mgr.document_delete(req)
        if res.code == status.HTTP_200_OK:
            logger.info(
                f"[DOC_DELETE] Documents deleted - Doc IDs: {req.document_ids}, KB ID: {req.kb_id}"
            )
        return handle_response(res)
    except HTTPException:
        # 重新抛出 HTTPException，不要转换为 500
        raise
    except ValidationError as e:
        logger.error(
            f"[DOC_DELETE] Validation failed - Errors: {e.errors()}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="document delete failed") from e
    except Exception as e:
        logger.error(
            f"[DOC_DELETE] Unexpected error - Error: {str(e)}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e
