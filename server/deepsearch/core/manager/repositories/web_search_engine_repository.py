# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from sqlalchemy.orm import Session

from server.core.manager.model_manager.utils import SecurityUtils
from server.deepsearch.common.exception.exceptions import WebSearchEngineNotFoundException, \
    WebSearchEngineApiKeyDecryptError, ValidationError
from server.deepsearch.core.models.web_search_engine_model import WebSearchEngineModel
from server.schemas.web_search_engine import WebSearchEngineDetail


class WebSearchEngineRepositoryInter(ABC):

    @abstractmethod
    def create(self, model: WebSearchEngineModel) -> int:
        """创建联网增强引擎"""
        pass

    @abstractmethod
    def get_by_id(self, space_id: str, web_search_engine_id: int) -> type[WebSearchEngineModel]:
        """获取指定联网增强引擎"""
        pass

    @abstractmethod
    def get_by_name(self, space_id: str, web_search_engine_name: str) -> type[WebSearchEngineModel]:
        """获取用户名下指定名称的联网增强引擎"""
        pass

    @abstractmethod
    def get_list_by_id(self, space_id: str) -> List[type[WebSearchEngineModel]]:
        """通过id获取联网增强引擎列表"""
        pass

    @abstractmethod
    def delete_by_id(self, space_id: str, web_search_engine_id: int) -> None:
        """通过id删除指定联网增强引擎"""
        pass

    @abstractmethod
    def update(self, model: WebSearchEngineModel):
        """更新联网增强引擎"""
        pass

    @abstractmethod
    def get_engine_detail_by_id(self, space_id: str, web_search_engine_id: int) -> Optional[WebSearchEngineDetail]:
        """获取联网增强引擎详细信息"""
        pass


logger = logging.getLogger(__name__)


class WebSearchEngineRepository(WebSearchEngineRepositoryInter):

    def __init__(self, db: Session):
        self.db = db

    def delete_by_id(self, space_id: str, web_search_engine_id: int):
        """删除指定记录"""
        record = self.get_by_id(space_id, web_search_engine_id)
        if record:
            self.db.delete(record)
            self.db.commit()
        else:
            raise WebSearchEngineNotFoundException(f"web search engine id {web_search_engine_id}"
                                                   f" not found under your space.")

    def get_list_by_id(self, space_id: str):
        """查询指定用户id下所有联网增强引擎记录"""
        return self.db.query(WebSearchEngineModel).filter(WebSearchEngineModel.space_id == space_id).all()

    def get_by_id(self, space_id: str, web_search_engine_id: int):
        """通过id查询联网增强引擎记录"""
        return self.db.query(WebSearchEngineModel).filter(
            WebSearchEngineModel.web_search_engine_id == web_search_engine_id,
            WebSearchEngineModel.space_id == space_id).first()

    def get_by_name(self, space_id: str, web_search_engine_name: str) -> type[WebSearchEngineModel]:
        """通过引擎名称查询联网增强引擎记录"""
        return self.db.query(WebSearchEngineModel).filter(
            WebSearchEngineModel.search_engine_name == web_search_engine_name,
            WebSearchEngineModel.space_id == space_id).first()

    def create(self, model: WebSearchEngineModel):
        """插入联网增强引擎记录"""
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)

    def update(self, model: WebSearchEngineModel):
        """更新记录, 如果没有对应记录就新建, 如果有更新"""
        record = self.get_by_id(model.space_id, model.web_search_engine_id)
        if not record:
            raise WebSearchEngineNotFoundException(f"web search engine id {model.web_search_engine_id} "
                                                   f"not found under your space.")
        else:
            if model.search_engine_name is not None:
                record.search_engine_name = model.search_engine_name
            if model.search_api_key is not None:
                record.search_api_key = model.search_api_key
            if model.search_url is not None:
                record.search_url = model.search_url
            if model.extension is not None:
                record.extension = model.extension
            if model.is_active is not None:
                record.is_active = model.is_active
            self.db.commit()

    def get_engine_detail_by_id(self, space_id, engine_id):
        """获取联网增强引擎详细信息"""
        try:
            record = self.get_by_id(space_id, engine_id)
            if not record:
                logger.warning(f"Web search engine not found under your space.")
                return None
            result = WebSearchEngineDetail.model_validate(record)
            if result.search_api_key:
                try:
                    # 解密api key
                    result.search_api_key = SecurityUtils().decrypt_api_key(result.search_api_key)
                except Exception as e:
                    raise WebSearchEngineApiKeyDecryptError(f"API key decryption failed: {str(e)}") from e
            return result
        except WebSearchEngineApiKeyDecryptError:
            raise
        except Exception as e:
            logger.error(f"Failed to get web search engine: {str(e)}")
            raise ValidationError(f"Failed to get web search engine: {str(e)}") from e
