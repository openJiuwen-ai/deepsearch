from functools import wraps

from fastapi import status
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from openjiuwen.core.common.logging import logger

from server.core.database import SessionLocal, milliseconds
from server.core.config import settings
from server.local_retrieval.models import knowledge_base as kb_models
from server.local_retrieval.models import knowledge_base_document as kb_doc_models
from server.schemas.common import ResponseModel
from server.schemas.knowledge_base import KnowledgeBaseGet


class KnowledgeBaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def with_exception_handling(func_):
        @wraps(func_)
        def wrapper(self, *args, **kwargs):
            try:
                return func_(self, *args, **kwargs)
            except Exception as e:
                logger.error("Error: knowledge base db data preprocessing error")
                logger.error(f"Exception details: {type(e).__name__}")
                return ResponseModel(
                    code=status.HTTP_400_BAD_REQUEST,
                    message=f"Error: knowledge base db data preprocessing error: {type(e).__name__}"
                )
        return wrapper

    @staticmethod
    def _handle_db_error(error: Exception) -> ResponseModel[None]:
        logger.error(f"message: DB error: {str(error)}")
        return ResponseModel(code=status.HTTP_500_INTERNAL_SERVER_ERROR, message=f"DB error: {str(error)}")

    def _query_kb(self, space_id: str, kb_id: str):
        return self.db.query(kb_models.KnowledgeBaseDB).filter(
            kb_models.KnowledgeBaseDB.space_id == space_id,
            kb_models.KnowledgeBaseDB.kb_id == kb_id,
        )

    def _query_doc(self, space_id: str, kb_id: str, doc_id: str):
        return self.db.query(kb_doc_models.KnowledgeBaseDocumentDB).filter(
            kb_doc_models.KnowledgeBaseDocumentDB.space_id == space_id,
            kb_doc_models.KnowledgeBaseDocumentDB.kb_id == kb_id,
            kb_doc_models.KnowledgeBaseDocumentDB.doc_id == doc_id,
        )

    '''
    description: 创建知识库
    param {dict} kb_data  待创建的知识库数据
    return {*}
    '''
    @with_exception_handling
    def knowledge_base_create(self, kb_data: dict) -> ResponseModel[None]:
        if not kb_data or not kb_data.get("kb_id"):
            logger.info(f"No knowledge base data to register: \ndata: {kb_data}")
            return ResponseModel(code=status.HTTP_400_BAD_REQUEST, message="No knowledge base data to register")

        kb_id = kb_data["kb_id"]
        try:
            if self.db.query(kb_models.KnowledgeBaseDB).filter(
                kb_models.KnowledgeBaseDB.kb_id == kb_id
            ).first():
                return ResponseModel(code=status.HTTP_400_BAD_REQUEST, message="This db already exists")

            timestamp = milliseconds()
            if "create_time" not in kb_data or not kb_data["create_time"]:
                kb_data["create_time"] = timestamp
            if "update_time" not in kb_data or not kb_data["update_time"]:
                kb_data["update_time"] = timestamp

            kb_record = kb_models.KnowledgeBaseDB.from_dict(kb_data, exclude_invalid=False)
            self.db.add(kb_record)
            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl register successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 从数据库获取知识库
    param {KnowledgeBaseGet} kb_get  知识库查询条件
    return {*}
    '''
    @with_exception_handling
    def knowledge_base_get(self, kb_get: KnowledgeBaseGet) -> ResponseModel[dict | None]:
        try:
            record = self._query_kb(kb_get.space_id, kb_get.kb_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Data not found.")
            return ResponseModel(code=status.HTTP_200_OK, message="Success", data=record.to_dict())
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 删除知识库
    param {KnowledgeBaseGet} kb_get  知识库查询条件
    return {*}
    '''
    @with_exception_handling
    def knowledge_base_delete(self, kb_get: KnowledgeBaseGet) -> ResponseModel[None]:
        try:
            record = self._query_kb(kb_get.space_id, kb_get.kb_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Dl not found.")
            deleted_docs = (
                self.db.query(kb_doc_models.KnowledgeBaseDocumentDB)
                .filter(
                    kb_doc_models.KnowledgeBaseDocumentDB.space_id == kb_get.space_id,
                    kb_doc_models.KnowledgeBaseDocumentDB.kb_id == kb_get.kb_id,
                )
                .delete(synchronize_session=False)
            )
            logger.info(
                f"[KB_REPO_DELETE] Bulk-deleted {deleted_docs} document row(s) - "
                f"space_id={kb_get.space_id}, kb_id={kb_get.kb_id}"
            )
            self.db.delete(record)
            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl unregister successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 检查知识库名称是否已存在
    param {str} space_id  空间ID
    param {str} name  知识库名称
    param {str} exclude_kb_id  排除的知识库ID（用于更新时排除当前知识库）
    return {ResponseModel[bool]}  True表示名称已存在，False表示不存在
    '''
    @with_exception_handling
    def knowledge_base_check_name_exists(
        self,
        space_id: str,
        name: str,
        exclude_kb_id: str | None = None,
    ) -> ResponseModel[bool]:
        """检查知识库名称是否已存在（区分大小写）"""
        try:
            # SQLite 默认区分大小写，MySQL 需要用 BINARY
            if settings.db_type.lower() == "sqlite":
                query = self.db.query(kb_models.KnowledgeBaseDB).filter(
                    kb_models.KnowledgeBaseDB.space_id == space_id,
                    kb_models.KnowledgeBaseDB.name == name,
                )
            else:
                query = self.db.query(kb_models.KnowledgeBaseDB).filter(
                    kb_models.KnowledgeBaseDB.space_id == space_id,
                    func.binary(kb_models.KnowledgeBaseDB.name) == func.binary(name),
                )
            if exclude_kb_id:
                query = query.filter(kb_models.KnowledgeBaseDB.kb_id != exclude_kb_id)

            exists = self.db.query(query.exists()).scalar()
            return ResponseModel(code=status.HTTP_200_OK, message="Success", data=exists)
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 更新知识库
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    param {str} name  新的名字
    param {str} description  新的描述
    return {*}
    '''
    @with_exception_handling
    def knowledge_base_update(
        self,
        space_id: str,
        kb_id: str,
        name: str,
        description: str | None,
    ) -> ResponseModel[None]:
        try:
            record = self._query_kb(space_id, kb_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Dl not found.")

            record.name = name
            record.description = None if description is None else description
            record.update_time = milliseconds()
            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl update successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 创建知识库文档
    param {dict} doc_data  待创建的文档数据
    return {*}
    '''
    @with_exception_handling
    def document_create(self, doc_data: dict) -> ResponseModel[None]:
        if not doc_data or not doc_data.get("doc_id"):
            logger.info(f"No document data to register: \ndata: {doc_data}")
            return ResponseModel(code=status.HTTP_400_BAD_REQUEST, message="No document data to register")

        doc_id = doc_data["doc_id"]
        try:
            if self.db.query(kb_doc_models.KnowledgeBaseDocumentDB).filter(
                kb_doc_models.KnowledgeBaseDocumentDB.doc_id == doc_id
            ).first():
                return ResponseModel(code=status.HTTP_400_BAD_REQUEST, message="This db already exists")

            timestamp = milliseconds()
            if "create_time" not in doc_data or not doc_data["create_time"]:
                doc_data["create_time"] = timestamp
            if "update_time" not in doc_data or not doc_data["update_time"]:
                doc_data["update_time"] = timestamp

            doc_record = kb_doc_models.KnowledgeBaseDocumentDB.from_dict(doc_data, exclude_invalid=False)
            self.db.add(doc_record)
            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl register successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 从数据库获取知识库文档
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    param {str} doc_id  文档ID
    return {*}
    '''
    @with_exception_handling
    def document_get(
        self,
        space_id: str,
        kb_id: str,
        doc_id: str,
    ) -> ResponseModel[dict | None]:
        try:
            record = self._query_doc(space_id, kb_id, doc_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Data not found.")
            return ResponseModel(code=status.HTTP_200_OK, message="Success", data=record.to_dict())
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 删除知识库文档
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    param {str} doc_id  文档ID
    return {*}
    '''
    @with_exception_handling
    def document_delete(
        self,
        space_id: str,
        kb_id: str,
        doc_id: str,
    ) -> ResponseModel[None]:
        try:
            record = self._query_doc(space_id, kb_id, doc_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Dl not found.")
            self.db.delete(record)
            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl unregister successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 更新文档状态
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    param {str} doc_id  文档ID
    param {str} doc_status  新状态
    param {dict} process_info  处理信息（可选）
    return {*}
    '''
    @with_exception_handling
    def document_update_status(
        self,
        space_id: str,
        kb_id: str,
        doc_id: str,
        doc_status: str,
        process_info: dict | None = None,
        es_index_name: str | None = None,
        chunk_count: int | None = None,
    ) -> ResponseModel[None]:
        try:
            record = self._query_doc(space_id, kb_id, doc_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Dl not found.")

            record.status = doc_status
            record.update_time = milliseconds()

            if process_info is not None:
                record.process_info = process_info

            if es_index_name is not None:
                record.es_index_id = None
                record.es_index_name = es_index_name
                record.indexed_time = milliseconds()

            if chunk_count is not None:
                record.chunk_count = chunk_count

            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl update successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 更新文档信息（当前只支持更新文档名称）
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    param {str} doc_id  文档ID
    param {str} name  新的文档名称
    return {*}
    '''
    @with_exception_handling
    def document_update(
        self,
        space_id: str,
        kb_id: str,
        doc_id: str,
        name: str,
    ) -> ResponseModel[None]:
        try:
            record = self._query_doc(space_id, kb_id, doc_id).first()
            if not record:
                return ResponseModel(code=status.HTTP_404_NOT_FOUND, message="Dl not found.")

            record.name = name
            record.update_time = milliseconds()
            self.db.commit()
            return ResponseModel(code=status.HTTP_200_OK, message="Dl update successfully.")
        except SQLAlchemyError as e:
            self.db.rollback()
            return self._handle_db_error(e)

    '''
    description: 查询知识库（查询词出现在名称或描述中，支持分页）
    param {str} space_id  空间ID
    param {str} query  查询词（查询词完整出现在知识库名称或描述中，大小写不敏感）
    param {int} page  页码，从1开始
    param {int} page_size  每页大小
    return {*}
    '''
    @with_exception_handling
    def knowledge_base_search(
        self,
        space_id: str,
        query: str,
        page: int = 1,
        page_size: int = 10,
    ) -> ResponseModel[dict]:
        try:
            query_lower = query.lower()
            search_conditions = or_(
                func.lower(kb_models.KnowledgeBaseDB.name).contains(query_lower),
                func.lower(kb_models.KnowledgeBaseDB.description).contains(query_lower),
            )

            page = max(1, page)
            page_size = max(1, min(page_size, 100))
            offset = (page - 1) * page_size

            base_query = self.db.query(kb_models.KnowledgeBaseDB).filter(
                kb_models.KnowledgeBaseDB.space_id == space_id,
                search_conditions,
            )
            total = base_query.count()

            kb_list = base_query.offset(offset).limit(page_size).all()
            knowledge_bases = [kb.to_dict() for kb in kb_list]

            total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1

            return ResponseModel(
                code=status.HTTP_200_OK,
                message="Search knowledge bases successfully",
                data={
                    "knowledge_bases": knowledge_bases,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                },
            )
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 获取知识库列表（支持分页）
    param {str} space_id  空间ID
    param {int} page  页码，从1开始
    param {int} size  每页大小
    return {*}
    '''
    @with_exception_handling
    def knowledge_base_list(
        self,
        space_id: str,
        page: int = 1,
        size: int = 10,
    ) -> ResponseModel[dict]:
        try:
            page = max(1, page)
            size = max(1, size)
            offset = (page - 1) * size

            total = self.db.query(kb_models.KnowledgeBaseDB).filter(
                kb_models.KnowledgeBaseDB.space_id == space_id
            ).count()

            if total == 0:
                return ResponseModel(
                    code=status.HTTP_200_OK,
                    message="Get knowledge base list success",
                    data={"items": [], "total": 0},
                )

            query = self.db.query(kb_models.KnowledgeBaseDB).filter(
                kb_models.KnowledgeBaseDB.space_id == space_id
            ).order_by(
                kb_models.KnowledgeBaseDB.create_time.desc()
            ).offset(offset).limit(size)

            kb_list = query.all()

            items = []
            for kb in kb_list:
                items.append({
                    "kb_id": kb.kb_id,
                    "space_id": kb.space_id,
                    "name": kb.name,
                    "description": kb.description,
                    "config": kb.config,
                    "create_time": kb.create_time,
                    "update_time": kb.update_time,
                })

            return ResponseModel(
                code=status.HTTP_200_OK,
                message="Get knowledge base list success",
                data={
                    "items": items,
                    "total": total,
                },
            )
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 获取知识库文档列表（支持分页）
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    param {int} page  页码，从1开始
    param {int} size  每页大小
    return {*}
    '''
    @with_exception_handling
    def document_list(
        self,
        space_id: str,
        kb_id: str,
        page: int = 1,
        size: int = 10,
    ) -> ResponseModel[dict]:
        try:
            page = max(1, page)
            size = max(1, size)
            offset = (page - 1) * size

            base_query = self.db.query(kb_doc_models.KnowledgeBaseDocumentDB).filter(
                kb_doc_models.KnowledgeBaseDocumentDB.space_id == space_id,
                kb_doc_models.KnowledgeBaseDocumentDB.kb_id == kb_id,
            )
            total = base_query.count()

            if total == 0:
                return ResponseModel(
                    code=status.HTTP_200_OK,
                    message="Get document list success",
                    data={"items": [], "total": 0, "page": page, "size": size},
                )

            doc_list = base_query.order_by(
                kb_doc_models.KnowledgeBaseDocumentDB.create_time.desc()
            ).offset(offset).limit(size).all()

            items = []
            for doc in doc_list:
                items.append({
                    "doc_id": doc.doc_id,
                    "name": doc.name,
                    "status": doc.status,
                    "process_info": doc.process_info if doc.process_info else {},
                    "create_time": doc.create_time,
                    "update_time": doc.update_time,
                })

            return ResponseModel(
                code=status.HTTP_200_OK,
                message="Get document list success",
                data={
                    "items": items,
                    "total": total,
                    "page": page,
                    "size": size,
                },
            )
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 获取知识库文档状态列表
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    return {*}
    '''
    @with_exception_handling
    def document_status_list(
        self,
        space_id: str,
        kb_id: str,
    ) -> ResponseModel[list[str]]:
        try:
            status_rows = self.db.query(
                kb_doc_models.KnowledgeBaseDocumentDB.status
            ).filter(
                kb_doc_models.KnowledgeBaseDocumentDB.space_id == space_id,
                kb_doc_models.KnowledgeBaseDocumentDB.kb_id == kb_id,
            ).all()

            status_list = [row[0] for row in status_rows if row and row[0]]
            return ResponseModel(
                code=status.HTTP_200_OK,
                message="Get document status list success",
                data=status_list,
            )
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 获取知识库文档ID列表
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    return {*}
    '''
    @with_exception_handling
    def document_id_list(
        self,
        space_id: str,
        kb_id: str,
    ) -> ResponseModel[list[str]]:
        try:
            id_rows = self.db.query(
                kb_doc_models.KnowledgeBaseDocumentDB.doc_id
            ).filter(
                kb_doc_models.KnowledgeBaseDocumentDB.space_id == space_id,
                kb_doc_models.KnowledgeBaseDocumentDB.kb_id == kb_id,
            ).all()

            id_list = [row[0] for row in id_rows if row and row[0]]
            return ResponseModel(
                code=status.HTTP_200_OK,
                message="Get document id list success",
                data=id_list,
            )
        except SQLAlchemyError as e:
            return self._handle_db_error(e)

    '''
    description: 检查知识库是否有图增强构建的文档
    param {str} space_id  空间ID
    param {str} kb_id  知识库ID
    return {bool} 是否有图增强文档
    '''
    @with_exception_handling
    def has_graph_enhancement_documents(self, space_id: str, kb_id: str) -> bool:
        try:
            docs = self.db.query(kb_doc_models.KnowledgeBaseDocumentDB).filter(
                kb_doc_models.KnowledgeBaseDocumentDB.space_id == space_id,
                kb_doc_models.KnowledgeBaseDocumentDB.kb_id == kb_id,
                kb_doc_models.KnowledgeBaseDocumentDB.status == "indexed",
            ).all()

            for doc in docs:
                if doc.process_info and isinstance(doc.process_info, dict):
                    indexing_strategy = doc.process_info.get("indexing_strategy")
                    if isinstance(indexing_strategy, dict):
                        enable_graph_enhancement = indexing_strategy.get("enable_graph_enhancement", False)
                        if enable_graph_enhancement:
                            return True

            return False
        except SQLAlchemyError as e:
            self._handle_db_error(e)
            return False

# 创建全局实例
knowledge_base_repository = KnowledgeBaseRepository(SessionLocal())

