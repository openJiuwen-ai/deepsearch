#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import os
import re
import uuid
import time
import inspect
import asyncio
import json
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Union, Tuple, Optional
from fastapi import status, UploadFile
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser
from openjiuwen.core.retrieval.indexing.processor.chunker.chunking import TextChunker
from openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor import TripleExtractor
from openjiuwen.core.retrieval.indexing.indexer.milvus_indexer import MilvusIndexer
from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore
from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
from openjiuwen.core.retrieval.common.config import (
    KnowledgeBaseConfig,
    EmbeddingConfig,
    VectorStoreConfig,
    IndexConfig,
)
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding

from openjiuwen_deepsearch.framework.openjiuwen.llm.llm_model_factory import (
    LLMModelFactory,
)

from server.core.database import SessionLocal, milliseconds
from server.local_retrieval.core.manager.repositories.knowledge_base_repository import (
    knowledge_base_repository,
)
from server.schemas.knowledge_base import (
    EmbedModelConfig,
    LLMConfig,
    KnowledgeBaseCreate,
    KnowledgeBaseResponseCreate,
    KnowledgeBaseGet,
    KnowledgeBaseUpdateRequest,
    KnowledgeBaseInfo,
    DocumentUploadResponse,
    DocumentUploadBatchResponse,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
    KnowledgeBaseListRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseListItem,
    DocumentStatusRequest,
    DocumentStatusResponse,
    DocumentStatusListResponse,
    DocumentProcessRequest,
    DocumentProcessResponse,
    DocumentListRequest,
    DocumentListResponse,
    DocumentListItem,
    DocumentUpdateRequest,
    DocumentDeleteRequest,
    TaskProgressRequest,
    TaskProgressResponse,
    TaskProgressItem,
)
from server.schemas.common import ResponseModel
from server.local_retrieval.models.knowledge_base_document import DocumentStatus
from server.core.manager.model_manager.utils import SecurityUtils
from server.local_retrieval.core.object.aioboto_storage_client import AioBotoClient


class OBSDocumentManager:
    """
    Manages OBS documents and uploads/downloads them to/from OBS
    """

    backend_dir = Path(__file__).resolve().parent.parent.parent.parent.parent

    def __init__(self, bucket: Optional[str] = None):
        self.bucket = bucket or os.getenv("OBS_BUCKET")
        if not self.bucket:
            logger.warning("[OBS] OBS_BUCKET not set, skipping upload_document")

        if AioBotoClient is None:
            logger.warning("[OBS] AioBotoClient not available, OBS operations will be no-op")
            self.obs_client = None
            return

        server = os.getenv("OBS_SERVER")
        access_key_id = SecurityUtils.get_decrypted_secret(
            "OBS_ACCESS_KEY_ID",
            os.getenv("OBS_SECRET_KEY", None),
        )
        secret_access_key = SecurityUtils.get_decrypted_secret(
            "OBS_SECRET_ACCESS_KEY",
            os.getenv("OBS_SECRET_ACCESS_KEY", None),
        )
        self.obs_client = AioBotoClient(
            server=server,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )

    @staticmethod
    def obs_name(space_id: str, kb_id: str, file_name: str) -> str:
        return f"{space_id}/{kb_id}/{file_name}"

    @classmethod
    def local_path(cls, space_id: str, kb_id: str, file_name: str) -> Path:
        storage_path = cls.backend_dir / "data" / "knowledge_base" / space_id / kb_id
        storage_path.mkdir(parents=True, exist_ok=True)
        return storage_path / file_name

    async def delete_document(self, object_name: str):
        if not self.bucket or not self.obs_client:
            return
        await self.obs_client.delete_object(self.bucket, object_name)

    async def download_document(
        self,
        object_name: str,
        file_path: str | Path,
    ):
        if not self.bucket or not self.obs_client:
            return
        file_path = Path(file_path)

        # Create file path directory if it does not exist
        file_dir = file_path.parent
        if not os.path.isdir(file_dir):
            file_dir.mkdir(parents=True, exist_ok=True)

        await self.obs_client.download_file(self.bucket, object_name, file_path)

    async def upload_document(
        self,
        object_name: str,
        file_path: str | Path,
    ):
        if not self.bucket or not self.obs_client:
            return
        await self.obs_client.upload_file(self.bucket, object_name, file_path)

    async def download_if_updated(
        self,
        object_name: str,
        file_path: str,
    ):
        if not self.bucket or not self.obs_client:
            return
        listed_objects = await self.obs_client.list_objects(
            self.bucket, object_prefix=object_name, max_objects=1
        )
        if not listed_objects:
            logger.info("No matching objects found on OBS, skipping download.")
            return

        obs_last_modified = listed_objects[0].get("LastModified")
        if not obs_last_modified:
            logger.info("OBS object missing LastModified, skipping download.")
            return

        # If local file does not exist, download directly
        if not os.path.exists(file_path):
            await self.download_document(object_name, file_path)
            logger.info("Local file missing, downloaded from OBS.")
            return

        # Check local file mtime
        local_mtime = os.path.getmtime(file_path)
        local_modified = datetime.fromtimestamp(local_mtime, tz=timezone.utc)

        if obs_last_modified <= local_modified:
            logger.info("Local file is up to date, skipping download.")
            return

        await self.download_document(object_name, file_path)
        logger.info("Downloaded updated file.")


class LocalSimpleKnowledgeBase(SimpleKnowledgeBase):
    async def add_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Add documents to the knowledge base"""
        if not self.chunker:
            raise ValueError("chunker is required for add_documents")
        if not self.index_manager:
            raise ValueError("index_manager is required for add_documents")

        # Chunk documents
        chunks = self.chunker.chunk_documents(documents)
        logger.info(f"Chunked {len(documents)} documents into {len(chunks)} chunks")

        # Build index
        index_config = IndexConfig(
            index_name=f"ds_kb_{self.config.kb_id}_chunks",
            index_type=self.config.index_type,
        )

        success = await self.index_manager.build_index(
            chunks=chunks,
            config=index_config,
            embed_model=self.embed_model,
        )

        if not success:
            raise RuntimeError("Failed to build index")

        # Return document ID list
        doc_ids = [doc.id_ for doc in documents]
        logger.info(f"Successfully added {len(doc_ids)} documents to knowledge base")
        return doc_ids


class LocalGraphKnowledgeBase(GraphKnowledgeBase):
    async def add_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """Add documents to the knowledge base (including chunk index and triple index)"""
        if not self.chunker:
            raise ValueError("chunker is required for add_documents")
        if not self.index_manager:
            raise ValueError("index_manager is required for add_documents")

        # Chunk documents
        chunks = self.chunker.chunk_documents(documents)
        logger.info(f"Chunked {len(documents)} documents into {len(chunks)} chunks")

        # Build chunk index
        chunk_index_config = IndexConfig(
            index_name=f"ds_kb_{self.config.kb_id}_chunks",
            index_type=self.config.index_type,
        )

        success = await self.index_manager.build_index(
            chunks=chunks,
            config=chunk_index_config,
            embed_model=self.embed_model,
        )

        if not success:
            raise RuntimeError("Failed to build chunk index")

        # If graph indexing is enabled, extract triples and build triple index
        if self.config.use_graph and self.extractor:
            logger.info("Extracting triples for graph index...")
            triples = await self.extractor.extract(chunks)

            if triples:
                logger.info(f"Extracted {len(triples)} triples")

                # Build triple index
                triple_index_config = IndexConfig(
                    index_name=f"ds_kb_{self.config.kb_id}_triples",
                    index_type=self.config.index_type,
                )

                # Convert triples to TextChunk format for indexing
                triple_chunks = []
                for i, triple in enumerate(triples):
                    # Convert triple to text format
                    triple_text = f"{triple.subject} {triple.predicate} {triple.object}"
                    chunk = TextChunk(
                        id_=f"triple_{i}",
                        text=triple_text,
                        doc_id=triple.metadata.get("doc_id", ""),
                        metadata={
                            **triple.metadata,
                            "triple": json.dumps([triple.subject, triple.predicate, triple.object]),
                            "confidence": triple.confidence if triple.confidence else 0,
                            "chunk_index": i,
                        },
                    )
                    triple_chunks.append(chunk)

                success = await self.index_manager.build_index(
                    chunks=triple_chunks,
                    config=triple_index_config,
                    embed_model=self.embed_model,
                )

                if not success:
                    logger.error("Failed to build triple index")
                else:
                    logger.info(f"Built triple index with {len(triple_chunks)} triples")

        # Return document ID list
        doc_ids = [doc.id_ for doc in documents]
        logger.info(f"Successfully added {len(doc_ids)} documents to knowledge base")
        return doc_ids


def _extract_full_error_message(error: Exception) -> str:
    """提取完整的错误信息，包括异常链中的所有错误
    
    用于提取 openjiuwen 包抛出的异常信息，因为 openjiuwen 包内部可能捕获异常后
    使用 cause 参数重新抛出，形成异常链。
    
    Args:
        error: 异常对象
        
    Returns:
        完整的错误信息字符串，包含所有异常链中的错误
    """
    error_parts = []
    current_error = error
    
    # 遍历异常链，收集所有错误信息
    while current_error is not None:
        error_str = str(current_error)
        if error_str:
            error_parts.append(error_str)
        
        # 检查是否有 __cause__ (异常链)
        if hasattr(current_error, '__cause__') and current_error.__cause__:
            current_error = current_error.__cause__
        # 检查是否有 __context__ (异常上下文)
        elif hasattr(current_error, '__context__') and current_error.__context__:
            current_error = current_error.__context__
        else:
            break
    
    # 如果只有一个错误，直接返回
    if len(error_parts) == 1:
        return error_parts[0]
    
    # 如果有多个错误，用 " -> " 连接
    return " -> ".join(error_parts)


def _format_error_message_for_frontend(error_msg: str) -> str:
    """格式化错误信息供前端显示
    
    改写规则：
    1. 固定错误消息保持不变
    2. 带前缀的错误：去掉前缀、状态码、箭头（替换为分号）
    3. 在 "reason" 之前截断（如果存在）
    4. 确保首字母大写
    
    Args:
        error_msg: 原始错误信息
        
    Returns:
        格式化后的错误信息
    """
    if not error_msg:
        return error_msg
    
    # 固定错误消息列表（保持不变）
    fixed_messages = {
        "Document not found",
        "Document status invalid",
        "File path not found",
        "Failed to update document status",
        "Document validation failed",
        "Processing failed with unknown error",
        "Failed to update status to INDEXED",
    }
    
    # 如果是固定错误消息，直接返回（首字母已大写）
    if error_msg in fixed_messages:
        return error_msg
    
    # 需要改写的错误信息
    result = error_msg
    
    # 1. 去掉前缀
    prefixes = [
        "File parsing failed: ",
        "Index building failed: ",
        "Failed to update status to INDEXING: ",
    ]
    for prefix in prefixes:
        if result.startswith(prefix):
            result = result[len(prefix):]
            break
    
    # 2. 去掉状态码 [155xxx]
    result = re.sub(r'\[\d+\]\s*', '', result)
    
    # 3. 去掉箭头 -> 和前后空格，用分号替换
    result = re.sub(r'\s*->\s*', '; ', result)
    
    # 4. 在 "reason" 之前截断（如果存在）
    # 匹配 ", reason:" 或 ",reason:" 或 " reason:" 等变体
    reason_pattern = r',\s*reason\s*:'
    match = re.search(reason_pattern, result, re.IGNORECASE)
    if match:
        result = result[:match.start()].strip()
    
    # 5. 清理多余的空格
    result = ' '.join(result.split())
    
    # 6. 确保首字母大写
    if result:
        result = result[0].upper() + result[1:] if len(result) > 1 else result.upper()
    
    return result


def _create_llm_client(llm_config: LLMConfig):
    """从请求配置创建 LLM 客户端

    Args:
        llm_config: 请求中的 LLM 配置

    Returns:
        Tuple[LLM客户端实例, model_name]: (LLM客户端, 模型名称)
    """
    logger.info(
        f"[LLM_CLIENT] Creating LLM client from request config - "
        f"Model: {llm_config.model_name}, Type: {llm_config.model_type}"
    )
    api_key = (
        llm_config.api_key.decode("utf-8")
        if isinstance(llm_config.api_key, (bytes, bytearray))
        else str(llm_config.api_key)
    )
    timeout = 120  # 图增强索引使用较长超时
    llm_client = LLMModelFactory().get_model(
        model_provider=llm_config.model_type,
        api_key=api_key or "",
        api_base=llm_config.base_url or "",
        timeout=timeout,
    )
    logger.info(
        f"[LLM_CLIENT] LLM client created - Model: {llm_config.model_name}"
    )
    return llm_client, llm_config.model_name


def _create_embed_model(embed_model_config: EmbedModelConfig) -> APIEmbedding:
    """从请求配置创建 Embedding 模型

    Args:
        embed_model_config: 请求中的 Embedding 模型配置

    Returns:
        APIEmbedding 实例
    """
    logger.info(
        f"[EMBED_MODEL] Creating embed model from request config - "
        f"Model: {embed_model_config.model_name}"
    )
    api_key = (
        embed_model_config.api_key.decode("utf-8")
        if isinstance(embed_model_config.api_key, (bytes, bytearray))
        else str(embed_model_config.api_key)
    )
    embed_config = EmbeddingConfig(
        model_name=embed_model_config.model_name,
        api_key=api_key,
        base_url=embed_model_config.base_url,
    )
    embed_model = APIEmbedding(
        config=embed_config,
        timeout=embed_model_config.timeout,
        max_retries=embed_model_config.max_retries,
        max_batch_size=embed_model_config.max_batch_size,
    )
    logger.debug("[EMBED_MODEL] Embed model created from request config successfully")
    return embed_model


def _config_dict_to_embed_and_llm(config: dict) -> tuple[EmbedModelConfig, LLMConfig]:
    """从知识库 config 字典解析出 EmbedModelConfig 和 LLMConfig（用于文档处理等）。

    config 中应有 "embed_model_config" 和 "llm_config" 键，且 api_key 为 UTF-8 字符串。
    """
    if not config:
        raise ValueError("知识库 config 为空，无法读取 embed_model_config/llm_config")
    embed_dict = config.get("embed_model_config")
    llm_dict = config.get("llm_config")
    if not embed_dict:
        raise ValueError("知识库 config 中缺少 embed_model_config")
    if not llm_dict:
        raise ValueError("知识库 config 中缺少 llm_config")

    embed_model_config = EmbedModelConfig(
        model_name=embed_dict.get("model_name", ""),
        api_key=embed_dict.get("api_key") or "",
        base_url=embed_dict.get("base_url", ""),
        max_batch_size=int(embed_dict.get("max_batch_size", 1)),
        timeout=int(embed_dict.get("timeout", 60)),
        max_retries=int(embed_dict.get("max_retries", 3)),
    )
    llm_config = LLMConfig(
        model_name=llm_dict.get("model_name", ""),
        model_type=llm_dict.get("model_type", "openai"),
        base_url=llm_dict.get("base_url", ""),
        api_key=llm_dict.get("api_key") or "",
        hyper_parameters=llm_dict.get("hyper_parameters") or {},
        extension=llm_dict.get("extension") or {},
    )
    return embed_model_config, llm_config


# ==================== 异常处理装饰器 ====================


def with_exception_handling(func):
    """异常处理装饰器，支持同步和异步函数"""
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[KNOWLEDGE_BASE] Error in {func.__name__}: {str(e)}", exc_info=True)
                return ResponseModel(
                    code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    message=f"Internal server error: {str(e)}",
                )

        return async_wrapper

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"[KNOWLEDGE_BASE] Error in {func.__name__}: {str(e)}", exc_info=True)
            return ResponseModel(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message=f"Internal server error: {str(e)}",
            )

    return wrapper


def _make_json_serializable_dict(d: dict) -> dict:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (bytes, bytearray)):
            out[k] = v.decode("utf-8") if v else ""
        elif isinstance(v, dict):
            out[k] = _make_json_serializable_dict(v)
        else:
            out[k] = v
    return out


def _build_kb_stored_config(
    embed_model_config: EmbedModelConfig,
    llm_config: LLMConfig,
    extra: Optional[Dict[str, Any]] = None,
) -> dict:
    """与创建逻辑一致：合并扩展 config 并写入 embed / llm 配置"""
    config: Dict[str, Any] = dict(extra) if extra else {}
    config["embed_model_config"] = _make_json_serializable_dict(embed_model_config.model_dump())
    config["llm_config"] = _make_json_serializable_dict(llm_config.model_dump())
    return config


@with_exception_handling
def knowledge_base_create(req: KnowledgeBaseCreate) -> ResponseModel:
    """创建新的知识库"""
    start_time = time.time()

    logger.info(f"[KB_CREATE] Creating knowledge base - Name: {req.name}")

    # 1. 检查知识库名称是否已存在（区分大小写）
    name_exists_result = knowledge_base_repository.knowledge_base_check_name_exists(
        space_id=req.space_id, name=req.name
    )
    if name_exists_result.code != status.HTTP_200_OK:
        logger.error(
            f"[KB_CREATE] Failed to check name existence - Error: {name_exists_result.message}"
        )
        return ResponseModel(
            code=name_exists_result.code,
            message=name_exists_result.message,
        )
    if name_exists_result.data:
        logger.warning(
            f"[KB_CREATE] Knowledge base name already exists - Name: {req.name}, Space: {req.space_id}"
        )
        return ResponseModel(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"知识库名称 '{req.name}' 已存在",
        )

    # 2. Checking index connection
    index_conn = _check_index_connection()
    if index_conn is not None:
        return index_conn

    # 3. 生成知识库ID（使用去掉连字符的 UUID，保证仅字母数字，Milvus 索引名合规）
    kb_id = uuid.uuid4().hex
    logger.info(f"[KB_CREATE] Generated KB ID: {kb_id}")

    # 4. 将 embed_model_config、llm_config 序列化并合并到 config
    config = _build_kb_stored_config(
        req.embed_model_config, req.llm_config, req.config
    )

    # 5. 准备知识库数据
    kb_data = {
        "space_id": req.space_id,
        "kb_id": kb_id,
        "name": req.name,
        "description": req.description,
        "config": config,
        "create_time": milliseconds(),
        "update_time": milliseconds(),
    }

    # 6. 保存到数据库
    create_result = knowledge_base_repository.knowledge_base_create(kb_data)

    if create_result.code != status.HTTP_200_OK:
        logger.error(
            f"[KB_CREATE] Database save failed - ID: {kb_id}, Error: {create_result.message}"
        )
        return ResponseModel(
            code=create_result.code,
            message=create_result.message,
        )

    # 7. 准备响应数据
    response_data = KnowledgeBaseResponseCreate(id=kb_id)

    logger.info(
        f"[KB_CREATE] Knowledge base created - ID: {kb_id}, Duration: {time.time() - start_time:.3f}s"
    )

    # 8. 返回创建结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="create knowledge base success",
        data=response_data.model_dump(by_alias=False),
    )


@with_exception_handling
async def knowledge_base_delete(req: KnowledgeBaseGet) -> ResponseModel:
    """删除知识库"""
    start_time = time.time()

    logger.info(f"[KB_DELETE] Deleting knowledge base - KB ID: {req.kb_id}")

    # 1. 检查知识库是否存在
    get_result = knowledge_base_repository.knowledge_base_get(req)
    if get_result.code == status.HTTP_404_NOT_FOUND:
        logger.warning(f"[KB_DELETE] Knowledge base not found - ID: {req.kb_id}")
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 3. 删除知识库
    delete_result = knowledge_base_repository.knowledge_base_delete(req)

    if delete_result.code != status.HTTP_200_OK:
        logger.error(f"[KB_DELETE] Delete failed - ID: {req.kb_id}, Error: {delete_result.message}")
        return ResponseModel(
            code=delete_result.code,
            message=delete_result.message,
        )

    logger.info(
        f"[KB_DELETE] Knowledge base deleted - ID: {req.kb_id}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    # 4. 删除本地知识库文件
    kb_storage_path = _get_storage_path(req.space_id, req.kb_id)
    try:
        if kb_storage_path.exists():
            # 删除整个知识库目录及其所有内容
            import shutil

            shutil.rmtree(kb_storage_path)
            logger.info(
                f"[KB_DELETE] Local knowledge base directory deleted - Path: {kb_storage_path}"
            )
        else:
            logger.warning(
                f"[KB_DELETE] Local knowledge base directory not found - Path: {kb_storage_path}"
            )
    except Exception as e:
        # 知识库记录已删除，但本地文件删除失败，记录错误但返回成功
        logger.error(
            f"[KB_DELETE] Failed to delete local knowledge base directory - Path: {kb_storage_path}, Error: {str(e)}",
            exc_info=True,
        )

    # 5. 删除 Milvus 向量索引（循环删除每个文档的索引）
    try:
        index_result = await _delete_kb_indices(req.kb_id, req.space_id)
        if index_result["success_count"] > 0:
            logger.info(
                f"[KB_DELETE] Indices successfully deleted - KB ID: {req.kb_id}, "
                f"Success: {index_result['success_count']}, Failed: {index_result['failed_count']}"
            )
        if index_result["errors"]:
            logger.warning(
                f"[KB_DELETE] Some indices failed to delete - KB ID: {req.kb_id}, "
                f"Errors: {index_result['errors']}"
            )
    except Exception as e:
        # Milvus 删除失败不影响整体删除结果
        logger.error(
            f"[KB_DELETE] Failed to delete indices - KB ID: {req.kb_id}, Error: {str(e)}",
            exc_info=True,
        )

    # 6. 返回删除结果
    return ResponseModel(
        code=status.HTTP_200_OK, message="delete knowledge base success", data=None
    )


@with_exception_handling
def knowledge_base_update(req: KnowledgeBaseUpdateRequest) -> ResponseModel:
    """更新知识库"""
    start_time = time.time()

    logger.info(
        f"[KB_UPDATE] Updating knowledge base - KB ID: {req.kb_id}, "
        f"Name: {req.name}, Desc: {repr(req.desc)}"
    )

    # 1. 检查知识库是否存在
    kb_get = KnowledgeBaseGet(space_id=req.space_id, kb_id=req.kb_id)
    get_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if get_result.code == status.HTTP_404_NOT_FOUND or not get_result.data:
        logger.warning(f"[KB_UPDATE] Knowledge base not found - ID: {req.kb_id}")
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 获取当前知识库的信息
    current_kb = get_result.data
    current_name = current_kb.get("name", "")
    current_desc = current_kb.get("description", "")
    logger.info(
        f"[KB_UPDATE] Current description: {repr(current_desc)}, New description: {repr(req.desc)}"
    )

    # 2. 如果名称改变，检查新名称是否已存在（排除当前知识库，区分大小写）
    if req.name != current_name:
        name_exists_result = knowledge_base_repository.knowledge_base_check_name_exists(
            space_id=req.space_id, name=req.name, exclude_kb_id=req.kb_id
        )
        if name_exists_result.code != status.HTTP_200_OK:
            logger.error(
                f"[KB_UPDATE] Failed to check name existence - Error: {name_exists_result.message}"
            )
            return ResponseModel(
                code=name_exists_result.code,
                message=name_exists_result.message,
            )
        if name_exists_result.data:
            logger.warning(
                f"[KB_UPDATE] Knowledge base name already exists - Name: {req.name}, "
                f"Space: {req.space_id}, KB ID: {req.kb_id}"
            )
            return ResponseModel(
                code=status.HTTP_400_BAD_REQUEST,
                message=f"知识库名称 '{req.name}' 已存在",
            )

    # 3. 合并配置并更新知识库（与创建时写入 DB 的 config 结构一致）
    description_value = req.desc if req.desc else None
    stored_config = _build_kb_stored_config(
        req.embed_model_config, req.llm_config, req.config
    )
    update_result = knowledge_base_repository.knowledge_base_update(
        space_id=req.space_id,
        kb_id=req.kb_id,
        name=req.name,
        description=description_value,
        config=stored_config,
    )

    if update_result.code != status.HTTP_200_OK:
        logger.error(f"[KB_UPDATE] Update failed - ID: {req.kb_id}, Error: {update_result.message}")
        return ResponseModel(
            code=update_result.code,
            message=update_result.message,
        )

    logger.info(
        f"[KB_UPDATE] Knowledge base updated - ID: {req.kb_id}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    # 4. 返回更新结果
    return ResponseModel(
        code=status.HTTP_200_OK, message="update knowledge base message success", data=None
    )


def _get_storage_path(space_id: str, kb_id: str) -> Path:
    """获取知识库文件存储路径"""
    # 获取后端目录的绝对路径
    backend_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
    storage_path = backend_dir / "data" / "knowledge_base" / space_id / kb_id
    storage_path.mkdir(parents=True, exist_ok=True)
    return storage_path


def _get_file_type(filename: str) -> str:
    """根据文件名获取文件类型"""
    return Path(filename).suffix.lower().lstrip(".")


def _get_mime_type(file_type: str) -> str:
    """根据文件类型获取 MIME 类型"""
    mime_types = {
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
        "md": "text/markdown",
        "xls": "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ppt": "application/vnd.ms-powerpoint",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return mime_types.get(file_type.lower(), "application/octet-stream")


def _detect_real_file_type(file_path: str) -> str:
    """检测文件的真实格式（通过文件头魔数）。

    Args:
        file_path: 文件路径

    Returns:
        检测到的真实文件扩展名，如 '.docx', '.doc', '.pdf' 等
        如果无法识别则返回原扩展名
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)

        # ZIP 格式（包括 .docx, .xlsx, .pptx 等 Office 2007+ 格式）
        if header[:4] == b"PK\x03\x04":
            return ".docx"

        # 旧版 DOC 格式（OLE Compound Document）
        if header[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return ".doc"

        # PDF 格式
        if header[:4] == b"%PDF":
            return ".pdf"

    except Exception as e:
        logger.warning(f"[PARSE] Failed to detect file type: {file_path}, Error: {e}")

    # 无法识别时返回原扩展名
    return Path(file_path).suffix.lower()


def _get_corrected_file_path(original_path: str) -> str:
    """根据文件真实格式返回正确扩展名的文件路径。

    如果文件扩展名与真实格式不符，会创建一个正确扩展名的临时副本文件。

    Args:
        original_path: 原始文件路径

    Returns:
        正确扩展名的文件路径（可能是临时副本）
    """
    original_ext = Path(original_path).suffix.lower()
    real_ext = _detect_real_file_type(original_path)

    # 扩展名一致，直接返回
    if original_ext == real_ext:
        return original_path

    # 特别处理：.doc 文件实际是 .docx 格式
    if original_ext == ".doc" and real_ext == ".docx":
        logger.info(
            f"[PARSE] File format mismatch detected - "
            f"Extension: {original_ext}, Real format: {real_ext}, "
            f"Path: {original_path}"
        )

        # 创建带正确扩展名的临时副本文件
        original_path_obj = Path(original_path)
        corrected_path = original_path_obj.with_suffix(real_ext)

        # 如果临时副本不存在，创建它
        if not corrected_path.exists():
            try:
                import shutil

                shutil.copy2(original_path, corrected_path)
                logger.info(
                    f"[PARSE] Created temporary file with correct extension: {corrected_path}"
                )
            except Exception as e:
                logger.warning(
                    f"[PARSE] Failed to create temporary file with correct extension: {str(e)}. "
                    f"Using original path: {original_path}"
                )
                return original_path

        return str(corrected_path)

    return original_path


async def _parse_file(
    doc_path: str, parsing_strategy, doc_id: str, file_name: str = None
) -> List[Document]:
    """调用新的知识库系统解析文件，返回Document列表"""
    logger.debug(
        f"[PARSE] Parsing file - Path: {doc_path}, "
        f"Strategy type: {parsing_strategy.strategy_type}"
    )

    if not doc_path:
        raise ValueError("File path is empty")

    # 检测并修正文件扩展名
    corrected_path = _get_corrected_file_path(doc_path)
    temp_file_created = False

    if corrected_path != doc_path:
        logger.info(
            f"[PARSE] Using corrected file path - "
            f"Original: {doc_path}, Corrected: {corrected_path}"
        )
        temp_file_created = True

    try:
        # 使用新的 AutoFileParser 解析文件
        parser = AutoFileParser()
        documents = await parser.parse(
            doc=corrected_path, doc_id=doc_id, file_name=file_name or Path(corrected_path).name
        )

        if not documents:
            raise ValueError(f"No content parsed from file: {doc_path}")

        for document in documents:
            if document.metadata is None:
                document.metadata = {}
            document.metadata["doc_id"] = document.id_

        logger.debug(f"[PARSE] Parsed file - Path: {doc_path}, Documents: {len(documents)}")
        return documents
    finally:
        # 清理临时文件
        if temp_file_created:
            try:
                corrected_path_obj = Path(corrected_path)
                if corrected_path_obj.exists() and corrected_path_obj != Path(doc_path):
                    corrected_path_obj.unlink()
                    logger.debug(f"[PARSE] Cleaned up temporary file: {corrected_path}")
            except Exception as e:
                logger.warning(
                    f"[PARSE] Failed to clean up temporary file {corrected_path}: {str(e)}"
                )


def _resolve_chunking_config(segmentation_strategy) -> tuple[int, float, Dict[str, bool]]:
    """提取分段配置，兼容前端字段命名"""
    cfg = segmentation_strategy.strategy_config or {}
    chunk_size = int(cfg.get("max_tokens") or cfg.get("chunk_size") or 512)
    overlap_percent = float(cfg.get("chunk_overlap_percent") or cfg.get("chunk_overlap") or 0)
    preprocess_options = {
        "normalize_whitespace": bool(
            cfg.get("remove_extra_spaces") or cfg.get("normalize_whitespace") or False
        ),
        "remove_url_email": bool(
            cfg.get("remove_urls_emails") or cfg.get("remove_url_email") or False
        ),
    }
    return chunk_size, overlap_percent, preprocess_options


def _create_chunker(segmentation_strategy, embed_model=None) -> TextChunker:
    """创建 Chunker 实例"""
    chunk_size, overlap_percent, preprocess_options = _resolve_chunking_config(
        segmentation_strategy
    )

    # 根据 strategy_type 确定 chunk_unit
    # strategy_type="1" 表示自动分段，使用字符分块
    # strategy_type="2" 表示自定义，需要检查配置
    chunk_unit = "char"  # 默认使用字符分块
    strategy_config = segmentation_strategy.strategy_config or {}
    if "chunk_unit" in strategy_config:
        chunk_unit = strategy_config.get("chunk_unit", "char")

    # 计算 chunk_overlap（绝对值，不是百分比）
    chunk_overlap = int(chunk_size * (overlap_percent / 100)) if overlap_percent > 0 else 0

    logger.debug(
        f"[CHUNK] Creating chunker - Chunk size: {chunk_size}, Overlap: {chunk_overlap} ({overlap_percent}%), "
        f"Unit: {chunk_unit}, Preprocess: {preprocess_options}"
    )

    # 如果使用 token 分块，需要提供 embed_model
    chunker = TextChunker(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_unit=chunk_unit,
        embed_model=embed_model if chunk_unit == "token" else None,
        preprocess_options=preprocess_options if any(preprocess_options.values()) else None,
    )

    return chunker


def _check_milvus_connection() -> Tuple[bool, str]:
    """检查 Milvus 连接性

    Returns:
        tuple[bool, str]: (是否连接成功, 错误信息)
    """
    try:
        from pymilvus import connections, utility

        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_token = os.getenv("MILVUS_TOKEN", None)

        # 尝试连接 Milvus
        alias = "kb_connection_test"
        try:
            # 如果连接已存在，先断开
            if connections.has_connection(alias):
                connections.disconnect(alias)
        except Exception as e:
            logger.warning(f"[MILVUS] Failed to disconnect connection: {alias}, Error: {str(e)}")

        # 建立新连接
        connections.connect(
            alias=alias, host=milvus_host, port=int(milvus_port), token=milvus_token
        )

        # 验证连接是否有效（尝试列出集合）
        try:
            _ = utility.list_collections(using=alias)
        except Exception as e:
            try:
                connections.disconnect(alias)
            except Exception as disconnect_error:
                logger.warning(
                    f"[MILVUS] Failed to disconnect connection: {alias}, Error: {str(disconnect_error)}"
                )
            return False, f"无法访问 Milvus 服务: {str(e)}"

        # 断开测试连接
        try:
            connections.disconnect(alias)
        except Exception as e:
            logger.warning(f"[MILVUS] Failed to disconnect connection: {alias}, Error: {str(e)}")
        return True, ""

    except ImportError:
        return False, "无法连接到 Milvus: 未安装 pymilvus 库"
    except Exception as e:
        error_msg = str(e)
        # 清理连接
        try:
            alias = "kb_connection_test"
            from pymilvus import connections

            if connections.has_connection(alias):
                connections.disconnect(alias)
        except Exception as disconnect_error:
            logger.warning(
                f"[MILVUS] Failed to disconnect connection: {alias}, Error: {str(disconnect_error)}"
            )
        return False, f"Milvus 连接失败: {error_msg}"


def _check_index_connection() -> Union[ResponseModel, None]:
    """
    Function for wrapping index connection type
    based on the `INDEX_MANAGER_TYPE` variable set in `.env`.
    Returns:
        _type_: `Union[ResponseModel, None]`
    """
    index_manager_type = os.getenv("INDEX_MANAGER_TYPE", "milvus")
    if index_manager_type == "milvus":
        logger.info(f"[KB_CREATE] Checking Milvus connection...")
        milvus_connected, milvus_error = _check_milvus_connection()
        if not milvus_connected:
            logger.error(f"[KB_CREATE] Milvus connection check failed - Error: {milvus_error}")
            return ResponseModel(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message=(
                    f"无法连接到 Milvus 服务，请检查 Milvus 配置和连接状态。"
                    f"错误信息: {milvus_error}"
                ),
            )
        logger.info(f"[KB_CREATE] Milvus connection check passed")
        return None
    else:
        # No index connection check is required by any other index type.
        return None


def _create_index_manager(collection_name: str) -> MilvusIndexer:
    """
    Creates Milvus index manager based on the `INDEX_MANAGER_TYPE` variable set in `.env`.
    Returns:
        MilvusIndexer
    """
    index_manager_type = os.getenv("INDEX_MANAGER_TYPE", "milvus")
    if index_manager_type == "milvus":
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_token = os.getenv("MILVUS_TOKEN", None)

        # 组合 Milvus URI (格式: http://host:port 或 tcp://host:port)
        # 默认使用 http:// 协议
        milvus_uri = f"http://{milvus_host}:{milvus_port}"

        vector_store_config = VectorStoreConfig(
            collection_name=collection_name,
        )
        return MilvusIndexer(config=vector_store_config, milvus_uri=milvus_uri, milvus_token=milvus_token)
    raise ValueError(
        f"Un-supported {index_manager_type=} for env variable INDEX_MANAGER_TYPE"
    )


async def _delete_kb_indices(kb_id: str, space_id: str) -> dict:
    """删除知识库下所有文档的 chunks 和 triples 索引

    获取知识库下的所有文档，然后循环删除每个文档的 chunks 和 triples 索引数据
    """
    result = {"success_count": 0, "failed_count": 0, "errors": []}

    try:
        # 获取知识库下的所有文档（分页获取，每页最多100条）
        all_documents = []
        page = 1
        page_size = 100

        while True:
            doc_list_result = knowledge_base_repository.document_list(
                space_id=space_id, kb_id=kb_id, page=page, size=page_size
            )

            if doc_list_result.code != status.HTTP_200_OK or not doc_list_result.data:
                break

            items = doc_list_result.data.get("items", [])
            if not items:
                break

            all_documents.extend(items)

            # 如果返回的数量小于 page_size，说明已经是最后一页
            if len(items) < page_size:
                break

            page += 1

        if not all_documents:
            logger.debug(f"[KB_DELETE] No documents to delete indices for KB {kb_id}")
            return result

        documents = all_documents
        logger.info(f"[KB_DELETE] Deleting indices for {len(documents)} documents in KB {kb_id}")

        # 创建索引管理器
        chunk_index = f"ds_kb_{kb_id}_chunks"
        triple_index = f"ds_kb_{kb_id}_triples"
        index_manager = _create_index_manager(collection_name=chunk_index)

        # 循环删除每个文档的索引
        for doc in documents:
            doc_id = doc.get("doc_id") or doc.get("id")
            if not doc_id:
                continue

            try:
                # 删除 chunks 索引
                await _delete_document_from_index(
                    index_manager=index_manager,
                    index_name=chunk_index,
                    doc_id=doc_id,
                    kb_id=kb_id,
                    index_type="chunks",
                )

                # 删除 triples 索引（如果有图增强）
                await _delete_document_from_index(
                    index_manager=index_manager,
                    index_name=triple_index,
                    doc_id=doc_id,
                    kb_id=kb_id,
                    index_type="triples",
                )

                result["success_count"] += 1
            except Exception as e:
                result["failed_count"] += 1
                result["errors"].append(f"Doc {doc_id}: {str(e)}")
                logger.warning(f"[KB_DELETE] Failed to delete index for doc {doc_id}: {e}")

        logger.info(
            f"[KB_DELETE] Index deletion completed - KB: {kb_id}, "
            f"Success: {result['success_count']}, Failed: {result['failed_count']}"
        )

    except Exception as e:
        error_msg = f"Failed to delete KB indices: {str(e)}"
        result["errors"].append(error_msg)
        logger.error(f"[KB_DELETE] {error_msg}", exc_info=True)

    return result


def _create_vector_store(collection_name: str) -> MilvusVectorStore:
    """
    Creates Milvus vector store based on the `INDEX_MANAGER_TYPE` variable set in `.env`.

    Args:
        collection_name: 集合名称

    Returns:
        MilvusVectorStore
    """
    index_manager_type = os.getenv("INDEX_MANAGER_TYPE", "milvus")

    if index_manager_type == "milvus":
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        milvus_token = os.getenv("MILVUS_TOKEN", None)

        # 组合 Milvus URI (格式: http://host:port 或 tcp://host:port)
        # 默认使用 http:// 协议
        milvus_uri = f"http://{milvus_host}:{milvus_port}"

        vector_store_config = VectorStoreConfig(
            collection_name=collection_name,
        )
        return MilvusVectorStore(
            config=vector_store_config, milvus_uri=milvus_uri, milvus_token=milvus_token
        )

    raise ValueError(
        f"Un-supported {index_manager_type=} for env variable INDEX_MANAGER_TYPE"
    )


async def _delete_document_from_index(
    index_manager: MilvusIndexer,
    index_name: str,
    doc_id: str,
    kb_id: str,
    index_type: str = "chunks",
) -> bool:
    """从索引中删除指定 doc_id 的数据

    Args:
        index_manager: MilvusIndexer
        index_name: 索引名称
        doc_id: 文档ID
        kb_id: 知识库ID
        index_type: 索引类型（"chunks" 或 "triples"），用于日志

    Returns:
        bool: 是否成功删除（如果索引不存在或数据不存在，返回 True）
    """
    try:
        # 检查索引是否存在
        index_exists = await index_manager.index_exists(index_name)
        if not index_exists:
            logger.debug(
                f"[DOC_DELETE] {index_type.capitalize()} index does not exist: {index_name}"
            )
            return True

        # Using `delete_index` from the provided `index_manager`
        deleted = await index_manager.delete_index(doc_id=doc_id, index_name=index_name)

        if deleted:
            logger.info(
                f"[DOC_DELETE] Deleted {index_type} from index - Index: {index_name}, Doc ID: {doc_id}"
            )
        else:
            logger.debug(
                f"[DOC_DELETE] No {index_type} found for doc_id: {doc_id} in index: {index_name}"
            )

        return True

    except Exception as delete_error:
        error_msg = str(delete_error)
        # 如果数据不存在，不算错误
        if "not exist" in error_msg.lower() or "not found" in error_msg.lower():
            logger.debug(
                f"[DOC_DELETE] No {index_type} found for doc_id: {doc_id} in index: {index_name}"
            )
            return True
        else:
            logger.warning(
                f"[DOC_DELETE] Failed to delete {index_type} - Doc ID: {doc_id}, KB ID: {kb_id}, Error: {delete_error}"
            )
            return False


async def _index_documents(
    documents: List[Document],
    indexing_strategy,
    segmentation_strategy,
    space_id: str,
    kb_id: str,
    doc_id: str,
    process_info: dict,
    llm_config: Optional[LLMConfig] = None,
    embed_model_config: Optional[EmbedModelConfig] = None,
) -> dict:

    # 1. 更新状态为INDEXING
    update_indexing_result = knowledge_base_repository.document_update_status(
        space_id=space_id,
        kb_id=kb_id,
        doc_id=doc_id,
        doc_status=DocumentStatus.INDEXING.value,
        process_info={**process_info, "parsing_completed": True, "document_count": len(documents)},
    )

    if update_indexing_result.code != status.HTTP_200_OK:
        raise Exception(f"Failed to update status to INDEXING: {update_indexing_result.message}")

    logger.info(f"[INDEX] Document status updated to INDEXING - Doc ID: {doc_id}")

    # 2. 加载配置
    use_graph = bool(getattr(indexing_strategy, "enable_graph_enhancement", False))
    chunk_index = f"ds_kb_{kb_id}_chunks"
    triple_index = f"ds_kb_{kb_id}_triples" if use_graph else None

    logger.info(
        f"[INDEX] Indexing documents - KB ID: {kb_id}, Doc ID: {doc_id}, "
        f"Documents: {len(documents)}, Use graph: {use_graph}, "
        f"Chunk index: {chunk_index}, Triple index: {triple_index}"
    )

    # 3. 创建模型客户端（仅使用请求配置，不从数据库读取）
    if embed_model_config is None:
        raise ValueError("embed_model_config is required for document indexing")
    embed_model = _create_embed_model(embed_model_config)

    llm_client = None
    model_name = None
    if use_graph:
        if llm_config is None:
            raise ValueError(
                "llm_config is required when enable_graph_enhancement is True"
            )
        llm_client, model_name = _create_llm_client(llm_config)
        logger.info(f"[INDEX] LLM client created - Model: {model_name}")
        if not llm_client:
            raise ValueError("llm_client is required when use_graph=True")

    # 5. 创建组件
    # 5.1 创建分块器（用于 add_documents 内部自动分块）
    strategy_config = segmentation_strategy.strategy_config or {}
    chunk_unit = strategy_config.get("chunk_unit", "char")
    chunker = _create_chunker(
        segmentation_strategy, embed_model=embed_model if chunk_unit == "token" else None
    )

    # 5.2 创建索引管理器
    index_manager = _create_index_manager(collection_name=chunk_index)

    # 5.3 创建向量存储
    vector_store = _create_vector_store(
        collection_name=chunk_index,
    )

    # 5.4 创建三元组提取器（如果使用图索引）
    extractor = None
    if use_graph and llm_client:
        extractor = TripleExtractor(
            llm_client=llm_client,
            model_name=model_name,
        )

    # 6. 创建知识库配置
    kb_config = KnowledgeBaseConfig(
        kb_id=kb_id,
        index_type="vector",
        use_graph=use_graph,
        chunk_size=chunker.chunk_size,
        chunk_overlap=chunker.chunk_overlap,
    )

    # 7. 创建知识库实例
    if use_graph:
        knowledge_base = LocalGraphKnowledgeBase(
            config=kb_config,
            vector_store=vector_store,
            embed_model=embed_model,
            parser=None,
            chunker=chunker,
            extractor=extractor,
            index_manager=index_manager,
            llm_client=llm_client,
        )
    else:
        knowledge_base = LocalSimpleKnowledgeBase(
            config=kb_config,
            vector_store=vector_store,
            embed_model=embed_model,
            parser=None,
            chunker=chunker,
            index_manager=index_manager,
            llm_client=llm_client,
        )

    # 8. 调用 add_documents 构建索引（会自动进行分块和索引构建）
    try:
        doc_ids = await knowledge_base.add_documents(documents)

        if not doc_ids:
            raise RuntimeError("Index build failed: no document IDs returned")

        # 获取实际创建的chunk数量
        chunk_count = 0
        try:
            # 尝试通过分块器估算chunk数量
            # 注意：这里只是估算，实际数量可能因为分块策略而有所不同
            total_text_length = sum(len(doc.text) for doc in documents)
            if chunker.chunk_size > 0:
                # 粗略估算：总文本长度 / chunk_size（不考虑重叠）
                estimated_chunks = max(1, total_text_length // chunker.chunk_size)
                chunk_count = estimated_chunks
                logger.debug(
                    f"[INDEX] Estimated chunk count: {chunk_count} "
                    f"(text length: {total_text_length}, chunk_size: {chunker.chunk_size})"
                )
        except Exception as e:
            logger.warning(f"[INDEX] Failed to estimate chunk count: {str(e)}")
            # 如果估算失败，使用文档数量作为fallback
            chunk_count = len(documents)

        logger.debug(
            f"[INDEX] Indexing completed - KB ID: {kb_id}, Doc ID: {doc_id}, "
            f"Chunk index: {chunk_index}, Triple index: {triple_index}, "
            f"Estimated chunks: {chunk_count}"
        )

        return {
            "chunk_index": chunk_index,
            "triple_index": triple_index,
            "chunk_count": chunk_count,
        }
    finally:
        # 清理资源
        try:
            await knowledge_base.close()
        except Exception as e:
            logger.warning(f"[INDEX] Failed to close knowledge base: {str(e)}")


async def process_single_document(
    space_id: str,
    kb_id: str,
    doc_id: str,
    file_path: str,
    parsing_strategy,
    segmentation_strategy,
    indexing_strategy,
    process_info: dict,
    file_name: Optional[str] = None,
    llm_config: Optional[LLMConfig] = None,
    embed_model_config: Optional[EmbedModelConfig] = None,
    obs_name: Optional[str] = None,
):
    """在后台异步处理单个文档"""
    try:
        logger.info(
            f"[DOC_PROCESS_BG] Starting background processing - Doc ID: {doc_id}, KB ID: {kb_id}"
        )

        # 1. 解析文件
        try:
            if not file_name:
                file_name = Path(file_path).name
            if not os.path.exists(file_path) and obs_name and os.getenv("OBS_BUCKET"):
                logger.info(
                    f'[DOC_PROCESS_BG] Local file missing, downloading from OBS - "{obs_name}" -> "{file_path}"'
                )
                obs_manager = OBSDocumentManager()
                await obs_manager.download_document(object_name=obs_name, file_path=file_path)
            
        except Exception as parse_error:
            # 提取 openjiuwen 包的完整错误信息（可能包含异常链）
            full_error_msg = _extract_full_error_message(parse_error)
            error_message = f"OBS download failed: {full_error_msg}"
            logger.error(
                f"[DOC_PROCESS_BG] OBS file download failed - {file_name=}, {obs_name=}, Error: {error_message}",
                exc_info=True,
            )
            raise Exception(error_message) from parse_error

        try:
            documents = await _parse_file(file_path, parsing_strategy, doc_id, file_name=file_name)
        except Exception as parse_error:
            # 提取 openjiuwen 包的完整错误信息（可能包含异常链）
            full_error_msg = _extract_full_error_message(parse_error)
            error_message = f"File parsing failed: {full_error_msg}"
            logger.error(
                f"[DOC_PROCESS_BG] File parsing failed - Doc ID: {doc_id}, KB ID: {kb_id}, Error: {error_message}",
                exc_info=True,
            )
            raise Exception(error_message) from parse_error

        # 2. 索引文档（内部会进行分块和索引构建，并更新状态为INDEXING）
        try:
            index_result = await _index_documents(
                documents=documents,
                indexing_strategy=indexing_strategy,
                segmentation_strategy=segmentation_strategy,
                space_id=space_id,
                kb_id=kb_id,
                doc_id=doc_id,
                process_info=process_info,
                llm_config=llm_config,
                embed_model_config=embed_model_config,
            )
        except Exception as index_error:
            # 提取 openjiuwen 包的完整错误信息（可能包含异常链）
            full_error_msg = _extract_full_error_message(index_error)
            error_message = f"Index building failed: {full_error_msg}"
            logger.error(
                f"[DOC_PROCESS_BG] Index building failed - Doc ID: {doc_id}, KB ID: {kb_id}, Error: {error_message}",
                exc_info=True,
            )
            raise Exception(error_message) from index_error

        # 4. 更新文档状态为INDEXED，同时更新索引字段
        final_process_info = {
            **process_info,
            "chunking_completed": True,
            "indexing_completed": True,
            "index_result": index_result,
        }

        update_indexed_result = knowledge_base_repository.document_update_status(
            space_id=space_id,
            kb_id=kb_id,
            doc_id=doc_id,
            doc_status=DocumentStatus.INDEXED.value,
            process_info=final_process_info,
            es_index_name=index_result.get("chunk_index"),
            chunk_count=index_result.get("chunk_count"),
        )

        if update_indexed_result.code != status.HTTP_200_OK:
            raise Exception("Failed to update status to INDEXED")

        logger.info(
            f"[DOC_PROCESS_BG] Document indexing completed - Doc ID: {doc_id}, "
            f"Chunk index: {index_result.get('chunk_index')}, "
            f"Chunks: {index_result.get('chunk_count')}, KB ID: {kb_id}"
        )

    except Exception as e:
        # 提取错误信息
        # 注意：e 是我们新创建的异常，它的消息已经包含了原始错误信息
        # 不需要遍历异常链，因为我们在创建异常时已经提取了完整的错误信息
        error_message = str(e)
        logger.error(
            f"[DOC_PROCESS_BG] Document processing failed - Doc ID: {doc_id}, "
            f"KB ID: {kb_id}, Error: {error_message}",
            exc_info=True,
        )

        # 更新状态为FAILED，记录完整的错误信息
        try:
            knowledge_base_repository.document_update_status(
                space_id=space_id,
                kb_id=kb_id,
                doc_id=doc_id,
                doc_status=DocumentStatus.FAILED.value,
                process_info={
                    **process_info,
                    "error": error_message,
                    "failed_time": milliseconds(),
                },
            )
        except Exception as update_error:
            logger.error(
                f"[DOC_PROCESS_BG] Failed to update status to FAILED - Doc ID: {doc_id}, "
                f"Error: {str(update_error)}"
            )


async def _process_documents_sequentially(
    space_id: str,
    kb_id: str,
    documents: list[dict],
    parsing_strategy,
    segmentation_strategy,
    indexing_strategy,
    task_id: str,
    process_info_base: dict,
    llm_config: Optional[LLMConfig] = None,
    embed_model_config: Optional[EmbedModelConfig] = None,
):
    """串行处理多个文档（后台任务）"""
    logger.info(
        f"[DOC_PROCESS_SEQ] Starting sequential processing - Task ID: {task_id}, "
        f"KB ID: {kb_id}, Total documents: {len(documents)}"
    )

    for idx, doc_info in enumerate(documents, 1):
        doc_id = doc_info.get("doc_id")
        file_path = doc_info.get("file_path")
        doc_name = doc_info.get("name")
        obs_name = doc_info.get("obs_name")
        try:
            logger.info(
                f"[DOC_PROCESS_SEQ] Processing document {idx}/{len(documents)} - "
                f"Doc ID: {doc_id}, Task ID: {task_id}"
            )

            # 使用基础 process_info，确保包含 task_id
            process_info = {
                **process_info_base,
                "task_id": task_id,
                "current_index": idx,
                "total_count": len(documents),
            }

            # 处理单个文档
            await process_single_document(
                space_id=space_id,
                kb_id=kb_id,
                doc_id=doc_id,
                file_path=file_path,
                parsing_strategy=parsing_strategy,
                segmentation_strategy=segmentation_strategy,
                indexing_strategy=indexing_strategy,
                process_info=process_info,
                file_name=doc_name,
                llm_config=llm_config,
                embed_model_config=embed_model_config,
                obs_name=obs_name,
            )

            logger.info(
                f"[DOC_PROCESS_SEQ] Completed document {idx}/{len(documents)} - "
                f"Doc ID: {doc_id}, Name: {doc_name}, Task ID: {task_id}"
            )

        except Exception as e:
            logger.error(
                f"[DOC_PROCESS_SEQ] Failed to process document {idx}/{len(documents)} - "
                f"Doc ID: {doc_id}, Task ID: {task_id}, Error: {str(e)}",
                exc_info=True,
            )
            continue

    logger.info(
        f"[DOC_PROCESS_SEQ] Sequential processing completed - Task ID: {task_id}, "
        f"KB ID: {kb_id}, Total documents: {len(documents)}"
    )


async def document_upload(
    space_id: str,
    kb_id: str,
    files: List[UploadFile],
    metadata: Dict[str, Any] | None,
) -> ResponseModel:
    """上传文档到知识库（支持多文件）

    注意：此函数是异步的，异常处理在 Router 层完成
    """
    start_time = time.time()

    logger.info(
        f"[DOC_UPLOAD] Uploading documents - KB ID: {kb_id}, Files: {len(files)}"
    )

    # 1. 验证知识库是否存在
    kb_get = KnowledgeBaseGet(space_id=space_id, kb_id=kb_id)
    kb_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if kb_result.code != status.HTTP_200_OK or not kb_result.data:
        logger.warning(f"[DOC_UPLOAD] Knowledge base not found - KB ID: {kb_id}")
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 2. 获取存储路径
    storage_path = _get_storage_path(space_id, kb_id)

    # 3. 解析 metadata 中的 doc_list，并获取当前知识库文档ID列表
    metadata_doc_list: list[str] = []
    if isinstance(metadata, dict):
        raw_doc_list = metadata.get("doc_list")
        if isinstance(raw_doc_list, list):
            metadata_doc_list = [str(doc_id) for doc_id in raw_doc_list if doc_id]

    existing_doc_ids: set[str] = set()
    doc_id_list_result = knowledge_base_repository.document_id_list(space_id=space_id, kb_id=kb_id)
    if doc_id_list_result.code == status.HTTP_200_OK:
        existing_doc_ids = set(doc_id_list_result.data or [])
    else:
        logger.warning(
            f"[DOC_UPLOAD] Failed to get document id list - KB ID: {kb_id}, "
            f"Error: {doc_id_list_result.message}"
        )

    # 4. 如果 metadata 中存在 doc_list，删除不在 doc_list 中的知识库文档
    if metadata_doc_list:
        metadata_doc_id_set = set(metadata_doc_list)
        delete_doc_ids = [doc_id for doc_id in existing_doc_ids if doc_id not in metadata_doc_id_set]
        for doc_id in delete_doc_ids:
            doc_get_result = knowledge_base_repository.document_get(
                space_id=space_id, kb_id=kb_id, doc_id=doc_id
            )
            if doc_get_result.code != status.HTTP_200_OK or not doc_get_result.data:
                logger.warning(
                    f"[DOC_UPLOAD] Document not found for delete - Doc ID: {doc_id}, KB ID: {kb_id}"
                )
                continue

            file_path = doc_get_result.data.get("file_path")
            delete_result = knowledge_base_repository.document_delete(
                space_id=space_id, kb_id=kb_id, doc_id=doc_id
            )
            if delete_result.code == status.HTTP_200_OK:
                logger.info(
                    f"[DOC_UPLOAD] Document deleted due to metadata sync - Doc ID: {doc_id}, KB ID: {kb_id}"
                )
            else:
                logger.error(
                    f"[DOC_UPLOAD] Failed to delete document - Doc ID: {doc_id}, KB ID: {kb_id}, "
                    f"Error: {delete_result.message}"
                )

    # 5. 允许的文件类型
    allowed_file_extensions = {".pdf", ".doc", ".docx", ".txt", ".md"}

    # 文件大小限制：20MB
    max_file_size = 20 * 1024 * 1024  # 20MB in bytes

    # 6. 处理每个文件
    uploaded_docs = []
    success_count = 0
    failed_count = 0

    for file_index, file in enumerate(files):
        try:
            # 5.1 生成文档ID（优先使用 metadata 中的 doc_list）
            doc_id_from_metadata = (
                metadata_doc_list[file_index] if file_index < len(metadata_doc_list) else ""
            )
            if doc_id_from_metadata:
                doc_id = doc_id_from_metadata
                if doc_id in existing_doc_ids:
                    logger.info(
                        f"[DOC_UPLOAD] Skip upload - Doc ID already exists: {doc_id}, "
                        f"KB ID: {kb_id}"
                    )
                    continue
            else:
                doc_id = str(uuid.uuid4())

            # 5.2 获取文件信息并验证文件类型
            filename = file.filename or f"unnamed_{doc_id}"
            file_ext = Path(filename).suffix.lower()

            # 验证文件类型
            if file_ext not in allowed_file_extensions:
                failed_count += 1
                logger.warning(
                    f"[DOC_UPLOAD] Unsupported file type - File: {filename}, Extension: {file_ext}, "
                    f"KB ID: {kb_id}"
                )
                continue

            file_type = _get_file_type(filename)
            mime_type = _get_mime_type(file_type)

            # 4.3 保存文件到服务器
            # 使用 doc_id 作为文件名，保留原始扩展名
            safe_filename = f"{doc_id}{Path(filename).suffix}"
            file_path = storage_path / safe_filename

            # 读取文件内容并保存（异步读取）
            file_content = await file.read()
            file_size = len(file_content)

            # 验证文件大小
            if file_size > max_file_size:
                failed_count += 1
                file_size_mb = file_size / (1024 * 1024)
                max_size_mb = max_file_size / (1024 * 1024)
                logger.warning(
                    f"[DOC_UPLOAD] File size exceeds limit - File: {filename}, Size: {file_size_mb:.2f}MB, "
                    f"Limit: {max_size_mb}MB, KB ID: {kb_id}"
                )
                continue

            with open(file_path, "wb") as f:
                f.write(file_content)

            # 获取OBS对象名
            obs_manager = OBSDocumentManager()
            object_name = obs_manager.obs_name(
                space_id=space_id, kb_id=kb_id, file_name=file_path.name
            )
            # 上传到OBS
            try:
                await obs_manager.upload_document(object_name=object_name, file_path=file_path)
            except Exception as obs_error:
                logger.warning(
                    f"[DOC_UPLOAD] OBS upload failed (continuing with local file) - "
                    f"File: {filename}, Doc ID: {doc_id}, KB ID: {kb_id}, Error: {str(obs_error)}",
                    exc_info=True,
                )

            logger.debug(f"[DOC_UPLOAD] File saved - Path: {file_path}, Size: {file_size} bytes")

            # 4.4 创建文档记录
            current_time = milliseconds()
            doc_data = {
                "space_id": space_id,
                "kb_id": kb_id,
                "doc_id": doc_id,
                "name": filename,
                "file_path": str(file_path),
                "obs_name": object_name,
                "file_size": file_size,
                "file_type": file_type,
                "mime_type": mime_type,
                "status": DocumentStatus.UPLOADED.value,
                "doc_metadata": metadata or {},
                "create_time": current_time,
                "update_time": current_time,
            }

            create_result = knowledge_base_repository.document_create(doc_data)

            if create_result.code == status.HTTP_200_OK:
                success_count += 1
                uploaded_docs.append(
                    DocumentUploadResponse(
                        id=doc_id,
                        name=filename,
                        file_size=file_size,
                        status=DocumentStatus.UPLOADED.value,
                    )
                )
                logger.info(f"[DOC_UPLOAD] Document created - Doc ID: {doc_id}, Name: {filename}")
            else:
                failed_count += 1
                # 删除已保存的文件
                if file_path.exists():
                    file_path.unlink()
                logger.error(
                    f"[DOC_UPLOAD] Failed to create document record - Doc ID: {doc_id}, Error: {create_result.message}"
                )

        except Exception as e:
            failed_count += 1
            logger.error(
                f"[DOC_UPLOAD] Error uploading file {file.filename}: {str(e)}", exc_info=True
            )
            # 如果文件已保存，尝试删除
            try:
                if "file_path" in locals() and file_path.exists():
                    file_path.unlink()
            except Exception as cleanup_error:
                logger.warning(
                    f"[DOC_UPLOAD] Failed to cleanup file after upload error - "
                    f"File: {file.filename}, Path: {file_path if 'file_path' in locals() else 'unknown'}, "
                    f"Error: {str(cleanup_error)}"
                )

    # 5. 准备响应数据
    response_data = DocumentUploadBatchResponse(
        success_count=success_count, failed_count=failed_count, documents=uploaded_docs
    )

    logger.info(
        f"[DOC_UPLOAD] Upload completed - KB ID: {kb_id}, "
        f"Success: {success_count}, Failed: {failed_count}, Duration: {time.time() - start_time:.3f}s"
    )

    # 6. 返回上传结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message=f"Upload completed: {success_count} success, {failed_count} failed",
        data=response_data.model_dump(by_alias=False),
    )


def _timestamp_to_date_str(timestamp: int | None) -> str:
    """将时间戳（毫秒）转换为日期时间字符串（YYYY-MM-DD HH:MM:SS）"""
    if not timestamp:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # 时间戳是毫秒，需要除以1000
    dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@with_exception_handling
def knowledge_base_search(req: KnowledgeBaseSearchRequest) -> ResponseModel:
    """查询知识库（支持分页）"""
    start_time = time.time()

    # 获取分页参数，设置默认值
    page = req.page or 1
    page_size = req.page_size or 10

    logger.info(
        f"[KB_SEARCH] Searching knowledge bases - Query: '{req.query}', Page: {page}, PageSize: {page_size}"
    )

    # 1. 执行查询（带分页）
    search_result = knowledge_base_repository.knowledge_base_search(
        space_id=req.space_id, query=req.query, page=page, page_size=page_size
    )

    if search_result.code != status.HTTP_200_OK:
        logger.error(f"[KB_SEARCH] Search failed - Error: {search_result.message}")
        return search_result

    # 2. 提取分页信息
    result_data = search_result.data
    knowledge_bases_data = result_data.get("knowledge_bases", [])
    total = result_data.get("total", 0)
    total_pages = result_data.get("total_pages", 1)

    # 3. 转换响应数据，并检查是否有图增强文档
    knowledge_bases = []
    for kb in knowledge_bases_data:
        kb_id = kb.get("kb_id", "")
        # 检查是否有图增强文档
        has_graph_enhancement = knowledge_base_repository.has_graph_enhancement_documents(
            space_id=req.space_id, kb_id=kb_id
        )
        kb_config = kb.get("config") or {}
        knowledge_bases.append(
            KnowledgeBaseInfo(
                id=kb_id,
                space_id=kb.get("space_id", ""),
                name=kb.get("name", ""),
                description=kb.get("description"),
                embed_model_config=kb_config.get("embed_model_config"),
                llm_config=kb_config.get("llm_config"),
                config=kb.get("config"),
                create_time=kb.get("create_time"),
                update_time=kb.get("update_time"),
                has_graph_enhancement=has_graph_enhancement,
            )
        )

    response_data = KnowledgeBaseSearchResponse(
        knowledge_bases=knowledge_bases,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )

    logger.info(
        f"[KB_SEARCH] Search completed - Found: {len(knowledge_bases)}/{total} knowledge bases, "
        f"Page: {page}/{total_pages}, Duration: {time.time() - start_time:.3f}s"
    )

    # 4. 返回查询结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="Search knowledge bases successfully",
        data=response_data.model_dump(
            by_alias=True
        ),  # 使用 by_alias=True 以返回 "id" 而不是 "kb_id"
    )


@with_exception_handling
def knowledge_base_list(req: KnowledgeBaseListRequest) -> ResponseModel:
    """获取知识库列表（支持分页）"""
    start_time = time.time()

    logger.info(
        f"[KB_LIST] Getting knowledge base list - Space ID: {req.space_id}, "
        f"Page: {req.page}, Size: {req.size}"
    )

    # 1. 从数据库获取知识库列表
    list_result = knowledge_base_repository.knowledge_base_list(
        space_id=req.space_id, page=req.page, size=req.size
    )

    if list_result.code != status.HTTP_200_OK:
        logger.warning(
            f"[KB_LIST] Database query failed, returning empty list - "
            f"Space ID: {req.space_id}, Error: {list_result.message}"
        )
        return ResponseModel(
            code=status.HTTP_200_OK,
            message="get knowledge base list success",
            data=KnowledgeBaseListResponse(
                items=[], total=0, page=req.page, size=req.size
            ).model_dump(by_alias=False),
        )

    # 2. 转换数据格式，并检查是否有图增强文档
    items = []
    for kb_data in list_result.data.get("items", []):
        kb_id = kb_data.get("kb_id", "")
        # 检查是否有图增强文档
        has_graph_enhancement = knowledge_base_repository.has_graph_enhancement_documents(
            space_id=req.space_id, kb_id=kb_id
        )
        # 获取知识库下文档状态并计算知识库状态
        kb_status = "indexing"
        status_result = knowledge_base_repository.document_status_list(
            space_id=req.space_id, kb_id=kb_id
        )
        if status_result.code == status.HTTP_200_OK:
            status_list = status_result.data or []
            if any(doc_status.startswith("upload") for doc_status in status_list):
                kb_status = "uploading"
            elif status_list and all(doc_status == "indexed" for doc_status in status_list):
                kb_status = "indexed"

        kb_config = kb_data.get("config") or {}
        items.append(
            KnowledgeBaseListItem(
                name=kb_data.get("name", ""),
                desc=kb_data.get("description"),
                id=kb_id,
                type="text",
                embed_model_config=kb_config.get("embed_model_config"),
                llm_config=kb_config.get("llm_config"),
                status=kb_status,
                created_at=_timestamp_to_date_str(kb_data.get("create_time")),
                updated_at=_timestamp_to_date_str(kb_data.get("update_time")),
                has_graph_enhancement=has_graph_enhancement,
            )
        )

    # 3. 获取分页信息
    total = list_result.data.get("total", 0)

    # 4. 构建响应数据
    response_data = KnowledgeBaseListResponse(
        items=items, total=total, page=req.page, size=req.size
    )

    logger.info(
        f"[KB_LIST] Knowledge base list retrieved - Space ID: {req.space_id}, "
        f"Total: {total}, Count: {len(items)}, Page: {req.page}, Size: {req.size}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    # 6. 返回列表结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="get knowledge base list success",
        data=response_data.model_dump(by_alias=False),
    )


@with_exception_handling
def document_list(req: DocumentListRequest) -> ResponseModel:
    """获取知识库文档列表（支持分页）"""
    start_time = time.time()

    logger.info(
        f"[DOC_LIST] Getting document list - Space ID: {req.space_id}, "
        f"KB ID: {req.kb_id}, Page: {req.page}, Size: {req.size}"
    )

    # 1. 验证知识库是否存在
    kb_get = KnowledgeBaseGet(space_id=req.space_id, kb_id=req.kb_id)
    kb_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if kb_result.code != status.HTTP_200_OK or not kb_result.data:
        logger.warning(f"[DOC_LIST] Knowledge base not found - KB ID: {req.kb_id}")
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 2. 从数据库获取文档列表
    list_result = knowledge_base_repository.document_list(
        space_id=req.space_id, kb_id=req.kb_id, page=req.page, size=req.size
    )

    if list_result.code != status.HTTP_200_OK:
        logger.error(
            f"[DOC_LIST] Database query failed - Space ID: {req.space_id}, "
            f"KB ID: {req.kb_id}, Error: {list_result.message}"
        )
        return ResponseModel(
            code=list_result.code,
            message=list_result.message,
            data={"items": [], "total": 0, "page": req.page, "size": req.size},
        )

    # 3. 转换数据格式
    items = []
    for doc_data in list_result.data.get("items", []):
        items.append(
            DocumentListItem(
                name=doc_data.get("name", ""),
                id=doc_data.get("doc_id", ""),
                status=doc_data.get("status", ""),
                created_at=_timestamp_to_date_str(doc_data.get("create_time")),
                updated_at=_timestamp_to_date_str(doc_data.get("update_time")),
            )
        )

    # 4. 构建响应数据
    total = list_result.data.get("total", 0)
    response_data = DocumentListResponse(items=items, total=total, page=req.page, size=req.size)

    logger.info(
        f"[DOC_LIST] Document list retrieved - Space ID: {req.space_id}, "
        f"KB ID: {req.kb_id}, Count: {len(items)}/{total}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    # 6. 返回列表结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="get documents success",
        data=response_data.model_dump(by_alias=False),
    )


@with_exception_handling
def document_update(req: DocumentUpdateRequest) -> ResponseModel:
    """更新文档信息（当前只支持更新文档名称）"""
    start_time = time.time()

    logger.info(
        f"[DOC_UPDATE] Updating document - Space ID: {req.space_id}, "
        f"KB ID: {req.kb_id}, Doc ID: {req.document_id}, Name: {req.document_name}"
    )

    # 1. 验证知识库是否存在
    kb_get = KnowledgeBaseGet(space_id=req.space_id, kb_id=req.kb_id)
    kb_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if kb_result.code != status.HTTP_200_OK or not kb_result.data:
        logger.warning(
            f"[DOC_UPDATE] Knowledge base not found - KB ID: {req.kb_id}"
        )
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 2. 验证文档是否存在
    doc_get_result = knowledge_base_repository.document_get(
        space_id=req.space_id, kb_id=req.kb_id, doc_id=req.document_id
    )
    if doc_get_result.code != status.HTTP_200_OK or not doc_get_result.data:
        logger.warning(
            f"[DOC_UPDATE] Document not found - Doc ID: {req.document_id}, KB ID: {req.kb_id}"
        )
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Document not found")

    # 3. 更新文档名称
    update_result = knowledge_base_repository.document_update(
        space_id=req.space_id, kb_id=req.kb_id, doc_id=req.document_id, name=req.document_name
    )

    if update_result.code != status.HTTP_200_OK:
        logger.error(
            f"[DOC_UPDATE] Update failed - Doc ID: {req.document_id}, KB ID: {req.kb_id}, "
            f"Error: {update_result.message}"
        )
        return ResponseModel(
            code=update_result.code,
            message=update_result.message,
        )

    logger.info(
        f"[DOC_UPDATE] Document updated - Doc ID: {req.document_id}, KB ID: {req.kb_id}, "
        f"New Name: {req.document_name}, Duration: {time.time() - start_time:.3f}s"
    )

    # 4. 返回更新结果
    return ResponseModel(
        code=status.HTTP_200_OK, message="update document message success", data=None
    )


@with_exception_handling
async def document_delete(req: DocumentDeleteRequest) -> ResponseModel:
    """删除文档（支持批量删除）"""
    start_time = time.time()

    logger.info(
        f"[DOC_DELETE] Deleting documents - Space ID: {req.space_id}, "
        f"KB ID: {req.kb_id}, Doc IDs: {req.document_ids}"
    )

    # 1. 验证知识库是否存在
    kb_get = KnowledgeBaseGet(space_id=req.space_id, kb_id=req.kb_id)
    kb_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if kb_result.code != status.HTTP_200_OK or not kb_result.data:
        logger.warning(
            f"[DOC_DELETE] Knowledge base not found - KB ID: {req.kb_id}"
        )
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 2. 批量删除文档
    success_count = 0
    failed_count = 0
    failed_doc_ids = []

    for doc_id in req.document_ids:
        # 验证文档是否存在
        doc_get_result = knowledge_base_repository.document_get(
            space_id=req.space_id, kb_id=req.kb_id, doc_id=doc_id
        )
        if doc_get_result.code != status.HTTP_200_OK or not doc_get_result.data:
            logger.warning(
                f"[DOC_DELETE] Document not found - Doc ID: {doc_id}, KB ID: {req.kb_id}"
            )
            failed_count += 1
            failed_doc_ids.append(doc_id)
            continue

        # 获取文件路径，用于删除本地文件
        file_path = doc_get_result.data.get("file_path")
        obs_name = doc_get_result.data.get("obs_name")

        # 删除文档
        delete_result = knowledge_base_repository.document_delete(
            space_id=req.space_id, kb_id=req.kb_id, doc_id=doc_id
        )

        if delete_result.code != status.HTTP_200_OK:
            logger.error(
                f"[DOC_DELETE] Delete failed - Doc ID: {doc_id}, KB ID: {req.kb_id}, "
                f"Error: {delete_result.message}"
            )
            failed_count += 1
            failed_doc_ids.append(doc_id)
        else:
            success_count += 1

            # 删除本地文件
            if file_path:
                try:
                    file_path_obj = Path(file_path)
                    if file_path_obj.exists():
                        file_path_obj.unlink()
                        logger.info(f"[DOC_DELETE] Local file deleted - Path: {file_path}")
                    else:
                        logger.warning(f"[DOC_DELETE] Local file not found - Path: {file_path}")
                except Exception as e:
                    logger.warning(
                        f"[DOC_DELETE] Failed to delete local file - Path: {file_path}, Error: {str(e)}"
                    )

            # deleting document from OBS (skip if no obs_name or OBS not configured)
            if obs_name and os.getenv("OBS_BUCKET"):
                obs_manager = OBSDocumentManager()
                await obs_manager.delete_document(obs_name)

            # 同步删除索引中的数据（使用新的知识库系统）
            try:
                # 获取文档的索引信息，判断是否使用图增强
                doc_data = doc_get_result.data
                process_info = doc_data.get("process_info", {})
                indexing_strategy = (
                    process_info.get("indexing_strategy", {})
                    if isinstance(process_info, dict)
                    else {}
                )
                use_graph = (
                    indexing_strategy.get("enable_graph_enhancement", False)
                    if isinstance(indexing_strategy, dict)
                    else False
                )

                # 创建索引管理器并删除索引数据
                chunk_index = f"ds_kb_{req.kb_id}_chunks"
                index_manager = _create_index_manager(collection_name=chunk_index)
                await _delete_document_from_index(
                    index_manager=index_manager,
                    index_name=chunk_index,
                    doc_id=doc_id,
                    kb_id=req.kb_id,
                    index_type="chunks",
                )

                # 如果使用图增强，还需要删除triple索引中的数据
                if use_graph:
                    triple_index = f"ds_kb_{req.kb_id}_triples"
                    await _delete_document_from_index(
                        index_manager=index_manager,
                        index_name=triple_index,
                        doc_id=doc_id,
                        kb_id=req.kb_id,
                        index_type="triples",
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[DOC_DELETE] Index cleanup failed - Doc ID: {doc_id}, KB ID: {req.kb_id}, Error: {e}"
                )

    logger.info(
        f"[DOC_DELETE] Documents deletion completed - KB ID: {req.kb_id}, "
        f"Success: {success_count}, Failed: {failed_count}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    # 3. 返回删除结果
    if success_count > 0:
        return ResponseModel(code=status.HTTP_200_OK, message="delete documents success", data=None)
    else:
        # 如果所有文档都删除失败，返回错误
        return ResponseModel(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to delete documents: {failed_doc_ids}",
            data=None,
        )


@with_exception_handling
def document_get_status_batch(req: DocumentStatusRequest) -> ResponseModel:
    """批量查询文档状态"""
    start_time = time.time()

    logger.info(
        f"[DOC_STATUS] Getting document status batch - "
        f"Space ID: {req.space_id}, KB ID: {req.kb_id}, Doc IDs: {len(req.doc_id_list)}"
    )

    # 1. 批量查询文档状态
    status_items = []
    for doc_id in req.doc_id_list:
        doc_result = knowledge_base_repository.document_get(
            space_id=req.space_id, kb_id=req.kb_id, doc_id=doc_id
        )

        if doc_result.code == status.HTTP_200_OK and doc_result.data:
            doc_data = doc_result.data
            status_value = doc_data.get("status", DocumentStatus.UPLOADING.value)
            doc_name = doc_data.get("name")

            # 从 process_info 中提取错误信息和图增强标识
            error_msg = None
            enable_graph_enhancement = None
            process_info = doc_data.get("process_info")
            if isinstance(process_info, dict):
                error_msg = process_info.get("error")
                # 从 indexing_strategy 中提取 enable_graph_enhancement
                indexing_strategy = process_info.get("indexing_strategy")
                if isinstance(indexing_strategy, dict):
                    enable_graph_enhancement = indexing_strategy.get(
                        "enable_graph_enhancement", False
                    )

            # 如果状态是 FAILED 但没有错误信息，提供默认错误信息
            if status_value == DocumentStatus.FAILED.value and not error_msg:
                error_msg = "Processing failed with unknown error"
            
            # 格式化错误信息供前端显示
            if error_msg:
                error_msg = _format_error_message_for_frontend(error_msg)

            status_items.append(
                DocumentStatusResponse(
                    id=doc_id,
                    status=status_value,
                    name=doc_name,
                    error_msg=error_msg,
                    enable_graph_enhancement=enable_graph_enhancement,
                )
            )
        else:
            # 文档不存在，仍然返回但状态为空或标记为不存在
            logger.warning(
                f"[DOC_STATUS] Document not found - Space ID: {req.space_id}, "
                f"KB ID: {req.kb_id}, Doc ID: {doc_id}"
            )
            # 可以选择跳过不存在的文档，或者返回一个标记状态
            # 这里选择跳过，只返回存在的文档

    # 2. 构建响应数据
    response_data = DocumentStatusListResponse(items=status_items)

    logger.info(
        f"[DOC_STATUS] Document status batch retrieved - Space ID: {req.space_id}, "
        f"KB ID: {req.kb_id}, Requested: {len(req.doc_id_list)}, "
        f"Found: {len(status_items)}, Duration: {time.time() - start_time:.3f}s"
    )

    # 3. 返回查询结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="get document status success",
        data=response_data.model_dump(by_alias=False),
    )


@with_exception_handling
async def document_process(req: DocumentProcessRequest) -> ResponseModel:
    """启动文档处理流程，使用 agentcore 的解析/分段/索引能力"""
    start_time = time.time()

    logger.info(
        f"[DOC_PROCESS] Starting document processing - "
        f"KB ID: {req.kb_id}, Files: {len(req.doc_id_list)}"
    )

    kb_get = KnowledgeBaseGet(space_id=req.space_id, kb_id=req.kb_id)
    kb_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if kb_result.code != status.HTTP_200_OK or not kb_result.data:
        logger.warning(
            f"[DOC_PROCESS] Knowledge base not found - KB ID: {req.kb_id}"
        )
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 从知识库 config 中读取 embed_model_config、llm_config
    try:
        embed_model_config, llm_config = _config_dict_to_embed_and_llm(
            kb_result.data.get("config") or {}
        )
    except ValueError as e:
        logger.warning(f"[DOC_PROCESS] Invalid kb config - KB ID: {req.kb_id}, Error: {e}")
        return ResponseModel(
            code=status.HTTP_400_BAD_REQUEST,
            message=str(e),
        )

    processed_count = 0
    failed_count = 0
    failed_docs: list[str] = []
    task_id = str(uuid.uuid4())
    current_time = milliseconds()

    # 构建基础 process_info（用于所有文档）
    process_info_base = {
        "task_id": task_id,
        "parsing_strategy": req.parsing_strategy.model_dump(),
        "segmentation_strategy": req.segmentation_strategy.model_dump(),
        "indexing_strategy": req.indexing_strategy.model_dump(),
        "start_time": current_time,
    }

    # 收集有效文档信息（用于串行处理）
    valid_documents: list[dict] = []

    # 第一阶段：验证所有文档并更新状态
    for doc_id in req.doc_id_list:
        try:
            doc_result = knowledge_base_repository.document_get(
                space_id=req.space_id, kb_id=req.kb_id, doc_id=doc_id
            )

            if doc_result.code != status.HTTP_200_OK or not doc_result.data:
                failed_count += 1
                failed_docs.append(doc_id)
                logger.warning(f"[DOC_PROCESS] Document not found - Doc ID: {doc_id}")
                # 尝试更新状态为FAILED（如果文档存在但查询失败）
                try:
                    knowledge_base_repository.document_update_status(
                        space_id=req.space_id,
                        kb_id=req.kb_id,
                        doc_id=doc_id,
                        doc_status=DocumentStatus.FAILED.value,
                        process_info={
                            **process_info_base,
                            "error": "Document not found",
                            "failed_time": milliseconds(),
                        },
                    )
                except Exception:
                    # 如果文档不存在，无法更新状态，这是正常的
                    pass
                continue

            current_status = doc_result.data.get("status")
            if current_status == DocumentStatus.INDEXED.value:
                logger.info(
                    f"[DOC_PROCESS] Document already indexed - Doc ID: {doc_id}, "
                    f"KB ID: {req.kb_id}"
                )
                continue
            if current_status != DocumentStatus.UPLOADED.value:
                failed_count += 1
                failed_docs.append(doc_id)
                logger.warning(
                    f"[DOC_PROCESS] Document status invalid - Doc ID: {doc_id}, Current status: {current_status}"
                )
                try:
                    knowledge_base_repository.document_update_status(
                        space_id=req.space_id,
                        kb_id=req.kb_id,
                        doc_id=doc_id,
                        doc_status=DocumentStatus.FAILED.value,
                        process_info={
                            **process_info_base,
                            "error": "Document status invalid",
                            "failed_time": milliseconds(),
                        },
                    )
                except Exception as update_error:
                    logger.error(
                        f"[DOC_PROCESS] Failed to update FAILED status - Doc ID: {doc_id}, Error: {str(update_error)}"
                    )
                continue

            file_path = doc_result.data.get("file_path")
            if not file_path:
                failed_count += 1
                failed_docs.append(doc_id)
                logger.error(f"[DOC_PROCESS] File path not found for document {doc_id}")
                knowledge_base_repository.document_update_status(
                    space_id=req.space_id,
                    kb_id=req.kb_id,
                    doc_id=doc_id,
                    doc_status=DocumentStatus.FAILED.value,
                    process_info={
                        **process_info_base,
                        "error": "File path not found",
                        "failed_time": milliseconds(),
                    },
                )
                continue

            # 更新文档状态为 PROCESSING
            update_result = knowledge_base_repository.document_update_status(
                space_id=req.space_id,
                kb_id=req.kb_id,
                doc_id=doc_id,
                doc_status=DocumentStatus.PROCESSING.value,
                process_info=process_info_base,
            )

            if update_result.code != status.HTTP_200_OK:
                failed_count += 1
                failed_docs.append(doc_id)
                logger.error(
                    f"[DOC_PROCESS] Failed to update document status - "
                    f"Doc ID: {doc_id}, Error: {update_result.message}"
                )
                try:
                    knowledge_base_repository.document_update_status(
                        space_id=req.space_id,
                        kb_id=req.kb_id,
                        doc_id=doc_id,
                        doc_status=DocumentStatus.FAILED.value,
                        process_info={
                            **process_info_base,
                            "error": "Failed to update document status",
                            "failed_time": milliseconds(),
                        },
                    )
                except Exception as update_error:
                    logger.error(
                        f"[DOC_PROCESS] Failed to update FAILED status - Doc ID: {doc_id}, Error: {str(update_error)}"
                    )
                continue
            doc_name = doc_result.data.get("name")
            doc_obs_name = doc_result.data.get("obs_name")
            # 收集有效文档信息
            valid_documents.append({
                "doc_id": doc_id,
                "file_path": file_path,
                "name": doc_name,
                "obs_name": doc_obs_name,
            })
            processed_count += 1
            logger.info(
                f"[DOC_PROCESS] Document validated and status updated to PROCESSING - Doc ID: {doc_id}"
            )

        except Exception as e:
            failed_count += 1
            failed_docs.append(doc_id)
            logger.error(
                f"[DOC_PROCESS] Failed to validate document - Doc ID: {doc_id}, "
                f"KB ID: {req.kb_id}, Error: {str(e)}",
                exc_info=True,
            )

            try:
                knowledge_base_repository.document_update_status(
                    space_id=req.space_id,
                    kb_id=req.kb_id,
                    doc_id=doc_id,
                    doc_status=DocumentStatus.FAILED.value,
                    process_info={
                        **process_info_base,
                        "error": "Document validation failed",
                        "failed_time": milliseconds(),
                    },
                )
            except Exception as update_error:
                logger.error(
                    f"[DOC_PROCESS] Failed to update FAILED status - Doc ID: {doc_id}, Error: {str(update_error)}",
                    exc_info=True,
                )

    # 第二阶段：如果有有效文档，创建后台任务串行处理
    if valid_documents:
        logger.info(
            f"[DOC_PROCESS] Creating sequential processing task - Task ID: {task_id}, "
            f"Valid documents: {len(valid_documents)}, KB ID: {req.kb_id}"
        )

        # 创建后台任务，串行处理所有文档
        asyncio.create_task(
            _process_documents_sequentially(
                space_id=req.space_id,
                kb_id=req.kb_id,
                documents=valid_documents,
                parsing_strategy=req.parsing_strategy,
                segmentation_strategy=req.segmentation_strategy,
                indexing_strategy=req.indexing_strategy,
                task_id=task_id,
                process_info_base=process_info_base,
                llm_config=llm_config,
                embed_model_config=embed_model_config,
            )
        )

        logger.info(
            f"[DOC_PROCESS] Sequential processing task created - Task ID: {task_id}, "
            f"KB ID: {req.kb_id}, Documents to process: {len(valid_documents)}"
        )

    response_data = DocumentProcessResponse(
        task_id=task_id,
        processed_count=processed_count,
        failed_count=failed_count,
        failed_docs=failed_docs,
    )

    logger.info(
        f"[DOC_PROCESS] Document processing tasks started - Task ID: {task_id}, "
        f"KB ID: {req.kb_id}, Processed: {processed_count}, Failed: {failed_count}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    return ResponseModel(
        code=status.HTTP_200_OK,
        message="Document processing tasks started",
        data=response_data.model_dump(by_alias=False),
    )


@with_exception_handling
def task_progress(req: TaskProgressRequest) -> ResponseModel:
    """查询任务处理进度"""
    start_time = time.time()

    logger.info(
        f"[TASK_PROGRESS] Querying task progress - Task ID: {req.task_id}, KB ID: {req.kb_id}"
    )

    # 1. 验证知识库是否存在
    kb_get = KnowledgeBaseGet(space_id=req.space_id, kb_id=req.kb_id)
    kb_result = knowledge_base_repository.knowledge_base_get(kb_get)
    if kb_result.code != status.HTTP_200_OK or not kb_result.data:
        logger.warning(
            f"[TASK_PROGRESS] Knowledge base not found - KB ID: {req.kb_id}"
        )
        return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Knowledge base not found")

    # 2. 查询该任务ID下的所有文档
    list_result = knowledge_base_repository.document_list(
        space_id=req.space_id, kb_id=req.kb_id, page=1, size=1000  # 假设一个任务不会超过1000个文档
    )

    if list_result.code != status.HTTP_200_OK:
        logger.error(
            f"[TASK_PROGRESS] Failed to query documents - Space ID: {req.space_id}, "
            f"KB ID: {req.kb_id}, Error: {list_result.message}"
        )
        return ResponseModel(
            code=list_result.code,
            message=list_result.message,
        )

    # 3. 筛选出属于该任务的文档并计算进度
    task_items = []
    total_count = 0
    processed_count = 0
    success_count = 0
    failed_count = 0

    for doc_data in list_result.data.get("items", []):
        process_info = doc_data.get("process_info", {})
        if isinstance(process_info, dict) and process_info.get("task_id") == req.task_id:
            total_count += 1
            doc_id = doc_data.get("doc_id", "")
            doc_name = doc_data.get("name", "")
            doc_status = doc_data.get("status", "")

            # 统计计数
            if doc_status == DocumentStatus.INDEXED.value:
                success_count += 1
            elif doc_status == DocumentStatus.FAILED.value:
                failed_count += 1

            if doc_status in [
                DocumentStatus.PROCESSING.value,
                DocumentStatus.INDEXING.value,
                DocumentStatus.INDEXED.value,
            ]:
                processed_count += 1

            error = None
            if doc_status == DocumentStatus.FAILED.value:
                error = process_info.get("error", "Unknown error")

            task_items.append(
                TaskProgressItem(doc_id=doc_id, doc_name=doc_name, status=doc_status, error=error)
            )

    # 5. 构建响应数据
    response_data = TaskProgressResponse(
        task_id=req.task_id,
        total_count=total_count,
        processed_count=processed_count,
        success_count=success_count,
        failed_count=failed_count,
        items=task_items,
    )

    logger.info(
        f"[TASK_PROGRESS] Task progress retrieved - Task ID: {req.task_id}, "
        f"KB ID: {req.kb_id}, Total: {total_count}, Processed: {processed_count}, "
        f"Success: {success_count}, Failed: {failed_count}, "
        f"Duration: {time.time() - start_time:.3f}s"
    )

    # 5. 返回查询结果
    return ResponseModel(
        code=status.HTTP_200_OK,
        message="get task progress success",
        data=response_data.model_dump(by_alias=False),
    )
