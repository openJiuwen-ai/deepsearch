# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging

from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import search_engine_mapping
from server.core.manager.model_manager.utils import SecurityUtils
from server.deepsearch.common.exception.exceptions import WebSearchEngineExistsException, ValidationError, \
    WebSearchEngineNotFoundException, WebSearchEngineNotRegisterException, WebSearchEngineExecutionException
from server.deepsearch.core.manager.repositories.web_search_engine_repository import \
    WebSearchEngineRepositoryInter
from server.deepsearch.core.models.web_search_engine_model import WebSearchEngineModel
from server.schemas.web_search_engine import WebSearchEngineCreateRequestDTO, WebSearchEngineCreateRes, \
    WebSearchEngineGetRequestDTO, WebSearchEngineGetRes, WebSearchEngineListRequestDTO, WebSearchEngineListRes, \
    WebSearchEngineItem, WebSearchEngineDeleteRequestDTO, WebSearchEngineDeleteRes, WebSearchEngineUpdateRequestDTO, \
    WebSearchEngineUpdateRes, WebSearchEnginePostRequestDTO, WebSearchEnginePostRes, WebSearchEngineDetail

MAX_SEARCH_RESULT_NUM = 3

logger = logging.getLogger(__name__)


class WebSearchEngineService:

    def __init__(self, web_search_engine_repository: WebSearchEngineRepositoryInter) -> None:
        self.repository = web_search_engine_repository
        self.security_utils = SecurityUtils()

    def create_web_search_engine(self, create_request: WebSearchEngineCreateRequestDTO):
        """
           创建联网增强引擎
        """
        try:
            model = self.repository.get_by_name(create_request.space_id, create_request.search_engine_name)
            if model:
                raise WebSearchEngineExistsException(f"Web search engine {create_request.search_engine_name}"
                                                     f" already exists under your space.")

            # search api key 加密
            encrypted_api_key = self.security_utils.encrypt_api_key(create_request.search_api_key) \
                if create_request.search_api_key else None
            create_request.search_api_key = encrypted_api_key

            patch = create_request.model_dump(exclude_unset=True)
            dao_model = WebSearchEngineModel()
            for key, value in patch.items():
                setattr(dao_model, key, value)
            self.repository.create(dao_model)
            return WebSearchEngineCreateRes.model_validate(dao_model)
        except WebSearchEngineExistsException:
            raise
        except Exception as e:
            logger.error(f"Failed to create web search engine: {str(e)}")
            raise ValidationError(f"Failed to create web search engine: {str(e)}") from e

    def get_web_search_engine_by_id(self, request: WebSearchEngineGetRequestDTO):
        """查询记录"""
        try:
            record = self.repository.get_by_id(request.space_id, request.web_search_engine_id)
            if not record:
                raise WebSearchEngineNotFoundException(f"Web search engine id "
                                                       f"{request.web_search_engine_id} not found under your space.")
            return WebSearchEngineGetRes.model_validate(record)
        except WebSearchEngineNotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get web search engine: {str(e)}")
            raise ValidationError(f"Failed to get web search engine: {str(e)}") from e

    def get_web_search_engine_list(self, request: WebSearchEngineListRequestDTO):
        """获取记录列表"""
        try:
            records = self.repository.get_list_by_id(request.space_id)
            search_engine_list = WebSearchEngineListRes()
            items = []
            for item in records:
                web_search_engine_item = WebSearchEngineItem.model_validate(item)
                items.append(web_search_engine_item)
            search_engine_list.data = items
            return search_engine_list
        except Exception as e:
            logger.error(f"Failed to get web search engine list: {str(e)}")
            raise ValidationError(f"Failed to get web search engine list: {str(e)}") from e

    def delete_web_search_engine_by_id(self, request: WebSearchEngineDeleteRequestDTO):
        """删除指定记录"""
        try:
            self.repository.delete_by_id(request.space_id, request.web_search_engine_id)
            return WebSearchEngineDeleteRes()
        except Exception as e:
            logger.error(f"Failed to delete web search engine: {str(e)}")
            raise ValidationError(f"Failed to delete web search engine: {str(e)}") from e

    def update_web_search_engine(self, request: WebSearchEngineUpdateRequestDTO):
        """更新指定记录"""
        try:
            # 1. 检查记录是否存在
            existing_record = self.repository.get_by_id(request.space_id, request.web_search_engine_id)
            if not existing_record:
                raise WebSearchEngineNotFoundException(f"Web search engine "
                                                       f"{request.web_search_engine_id} not found under your space.")

            # 2. search api key 加密处理
            if request.search_api_key:
                request.search_api_key = self.security_utils.encrypt_api_key(request.search_api_key)

            # 3. 构造用于更新的临时模型对象
            update_data = request.model_dump(exclude_unset=True)
            temp_model = WebSearchEngineModel()
            for key, value in update_data.items():
                setattr(temp_model, key, value)

            # 4. 执行更新
            self.repository.update(temp_model)

            # 5. 重新获取最新数据返回
            updated_record = self.repository.get_by_id(request.space_id, request.web_search_engine_id)
            return WebSearchEngineUpdateRes.model_validate(updated_record)
        except WebSearchEngineNotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to update web search engine: {str(e)}")
            raise ValidationError(f"Failed to update web search engine: {str(e)}") from e

    def post_web_search_engine(self, request: WebSearchEnginePostRequestDTO):
        """获取指定联网增强引擎并访问"""
        try:
            # 获取配置
            web_search_config: WebSearchEngineDetail = self.repository.get_engine_detail_by_id(
                request.space_id,
                request.web_search_engine_id
            )

            if not web_search_config:
                error_msg = f"Web search engine {request.web_search_engine_id} not found under your space."
                logger.error(error_msg)
                raise WebSearchEngineNotFoundException(error_msg)
            # 访问联网增强引擎
            search_results = self.run_web_search_engine(request, web_search_config)
            return WebSearchEnginePostRes.model_validate(search_results)

        # 透传所有业务异常
        except (WebSearchEngineNotFoundException,
                WebSearchEngineNotRegisterException,
                WebSearchEngineExecutionException):
            raise
        except Exception as e:
            logger.error(f"Unexpected error during running web search: {str(e)}", exc_info=True)
            raise ValidationError(f"Failed to run web search engine {request.web_search_engine_id}: {str(e)}") from e

    @staticmethod
    def run_web_search_engine(request: WebSearchEnginePostRequestDTO, web_search_config: WebSearchEngineDetail):
        """访问联网增强引擎并返回标准化结果"""
        web_engine_name = web_search_config.search_engine_name
        api_wrapper = search_engine_mapping.get(web_engine_name)

        if not api_wrapper:
            error_msg = (
                f"Failed to get web search engine [{request.web_search_engine_id}] {web_engine_name} "
                f"ApiWrapper from registry. Check if search engine is registered."
            )
            logger.error(error_msg)
            raise WebSearchEngineNotRegisterException(error_msg)
        search_engine = api_wrapper(
            search_api_key=bytearray(web_search_config.search_api_key.encode('utf-8')),
            search_url=web_search_config.search_url,
            max_web_search_results=MAX_SEARCH_RESULT_NUM
        )

        try:
            search_results = search_engine.results(request.query)
        except Exception as e:
            error_msg = f"Error when running web search engine [{request.web_search_engine_id}] {web_engine_name}: {e}"
            logger.error(error_msg, exc_info=True)
            raise WebSearchEngineExecutionException(error_msg) from e

        # 确保结果是列表
        if not isinstance(search_results, list):
            logger.warning(f"Non-list result from {web_engine_name}: {type(search_results)}")
            search_results = []

        return dict(search_engine_name=web_engine_name, datas=search_results)
