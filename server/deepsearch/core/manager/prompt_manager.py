# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List

from fastapi import status
from sqlalchemy.orm import Session

from server.core.database import milliseconds
from server.deepsearch.common.exception.exceptions import (
    PromptTemplateBasicException,
    PromptTemplateNotFoundException,
    PromptTemplateValidationException,
    PromptTemplateExistsException,
)
from server.deepsearch.core.manager.repositories.prompt_template_repository import PromptTemplateRepository
from server.deepsearch.core.models.prompt_template import PromptTemplateDB

logger = logging.getLogger(__name__)

# Prompt模板文件目录
PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "openjiuwen_deepsearch",
    "algorithm",
    "prompts"
)


@dataclass
class ImportPromptParams:
    """导入Prompt参数"""
    space_id: str
    prompt_name: str
    prompt_type: str
    prompt_content: str
    default_prompt: str
    description: str
    is_active: bool


@dataclass
class UpdatePromptParams:
    """更新Prompt参数"""
    space_id: str
    prompt_id: int
    prompt_content: str
    prompt_name: str
    prompt_type: str
    description: str
    is_active: bool


@dataclass
class ResetPromptParams:
    """重置Prompt参数"""
    space_id: str
    prompt_id: int


class PromptManager:
    """Prompt模板管理器"""
    _NAME_PATTERN = re.compile(r'^[\u4e00-\u9fa5a-zA-Z0-9_\-\.]+$')
    _MAX_NAME_LENGTH = 200

    def __init__(self):
        pass

    def _validate_prompt_name(self, name: str) -> None:
        """验证Prompt名称"""
        if not name:
            raise PromptTemplateValidationException("Prompt name cannot be empty")

        name = name.strip()
        if len(name) > self._MAX_NAME_LENGTH:
            raise PromptTemplateValidationException(f"Prompt name {name} too long")

        if not self._NAME_PATTERN.match(name):
            raise PromptTemplateValidationException(
                f"Invalid prompt name: {name}. Only Chinese/English letters, "
                f"numbers, underscores (_), hyphens (-), and dots (.) are allowed."
            )

    def _load_prompt_from_file(self, prompt_name: str) -> str:
        """从文件系统加载默认Prompt内容"""
        file_path = os.path.join(PROMPTS_DIR, f"{prompt_name}.md")
        if not os.path.exists(file_path):
            raise PromptTemplateNotFoundException(f"Default prompt file not found: {prompt_name}.md")

        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def _get_all_prompt_files(self) -> List[str]:
        """获取所有Prompt模板文件名"""
        if not os.path.exists(PROMPTS_DIR):
            logger.warning(f"Prompts directory not found: {PROMPTS_DIR}")
            return []

        files = []
        for file in os.listdir(PROMPTS_DIR):
            if file.endswith('.md') and file != 'template.py':
                files.append(file[:-3])  # 去掉.md后缀
        return files

    async def import_prompt(
            self,
            db: Session,
            params: ImportPromptParams
    ) -> Dict[str, Any]:
        """导入Prompt模板"""
        repo = PromptTemplateRepository(db)

        try:
            self._validate_prompt_name(params.prompt_name)

            # 检查是否已存在
            existing = repo.get_by_name(
                space_id=params.space_id,
                prompt_name=params.prompt_name
            )

            if existing:
                raise PromptTemplateExistsException(
                    f"Prompt '{params.prompt_name}' already exists in space '{params.space_id}'"
                )

            # 如果未提供 default_prompt，从文件系统加载
            default_prompt = params.default_prompt
            if not default_prompt:
                try:
                    default_prompt = self._load_prompt_from_file(params.prompt_name)
                except PromptTemplateNotFoundException:
                    # 如果文件不存在，使用 prompt_content 作为默认值
                    logger.warning(f"Default prompt file not found for {params.prompt_name}, using prompt_content as default")
                    default_prompt = params.prompt_content

            prompt = PromptTemplateDB(
                space_id=params.space_id,
                prompt_name=params.prompt_name,
                prompt_type=params.prompt_type,
                prompt_content=params.prompt_content,
                default_prompt=default_prompt,
                description=params.description,
                is_active=params.is_active,
                create_time=milliseconds(),
                update_time=milliseconds(),
            )
            repo.create(prompt)
            logger.info(f"Created new prompt: {params.prompt_name}")

            return {"code": status.HTTP_200_OK, "msg": "success", "prompt_id": prompt.prompt_id}

        except PromptTemplateBasicException:
            repo.rollback()
            raise
        except Exception:
            repo.rollback()
            raise

    def list_prompts(self, db: Session, space_id: str) -> Dict[str, Any]:
        """列出指定空间下的所有Prompt模板"""
        repo = PromptTemplateRepository(db)
        prompts = repo.list_by_space(space_id)

        data = []
        for prompt in prompts:
            create_time_dt = datetime.fromtimestamp(prompt.create_time / 1000)
            update_time_dt = datetime.fromtimestamp(prompt.update_time / 1000)
            data.append({
                "prompt_id": prompt.prompt_id,
                "prompt_name": prompt.prompt_name,
                "prompt_type": prompt.prompt_type,
                "description": prompt.description or "",
                "is_active": prompt.is_active,
                "create_time": create_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "update_time": update_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
            })

        return {"code": status.HTTP_200_OK, "msg": "success", "data": data}

    def get_prompt(self, db: Session, space_id: str, prompt_id: int) -> Dict[str, Any]:
        """获取单个Prompt模板详情"""
        repo = PromptTemplateRepository(db)
        prompt = repo.get_by_id(space_id, prompt_id)

        if not prompt:
            logger.info(f"Prompt not found: {prompt_id}")
            raise PromptTemplateNotFoundException(f"Prompt with id '{prompt_id}' not found")

        create_time_dt = datetime.fromtimestamp(prompt.create_time / 1000)
        update_time_dt = datetime.fromtimestamp(prompt.update_time / 1000)

        return {
            "code": status.HTTP_200_OK,
            "msg": "success",
            "prompt_id": prompt.prompt_id,
            "space_id": prompt.space_id,
            "prompt_name": prompt.prompt_name,
            "prompt_type": prompt.prompt_type,
            "prompt_content": prompt.prompt_content,
            "default_prompt": prompt.default_prompt,
            "description": prompt.description or "",
            "is_active": prompt.is_active,
            "create_time": create_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "update_time": update_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def delete_prompt(self, db: Session, space_id: str, prompt_id: int) -> Dict[str, Any]:
        """删除Prompt模板"""
        repo = PromptTemplateRepository(db)
        prompt = repo.get_by_id(space_id, prompt_id)

        if not prompt:
            raise PromptTemplateNotFoundException(f"Prompt with id '{prompt_id}' not found")

        repo.delete(prompt)
        logger.info(f"Deleted prompt: {prompt_id}")
        return {"code": status.HTTP_200_OK, "msg": "success"}

    def update_prompt(self, db: Session, params: UpdatePromptParams) -> Dict[str, Any]:
        """更新Prompt模板"""
        repo = PromptTemplateRepository(db)
        self._validate_prompt_name(params.prompt_name)

        prompt = repo.get_by_id(params.space_id, params.prompt_id)
        if not prompt:
            raise PromptTemplateNotFoundException(f"Prompt with id '{params.prompt_id}' not found")

        # 名称变更时的冲突校验
        if prompt.prompt_name != params.prompt_name:
            existing = repo.get_by_name(space_id=params.space_id, prompt_name=params.prompt_name)
            if existing and existing.prompt_id != params.prompt_id:
                raise PromptTemplateValidationException(f"Prompt name '{params.prompt_name}' already exists")

        prompt.prompt_name = params.prompt_name
        prompt.prompt_type = params.prompt_type
        prompt.prompt_content = params.prompt_content
        prompt.description = params.description
        prompt.is_active = params.is_active
        prompt.update_time = milliseconds()

        try:
            repo.commit()
            return {"code": status.HTTP_200_OK, "msg": "success", "prompt_id": params.prompt_id}
        except Exception as e:
            repo.rollback()
            logger.error(f"Prompt update failed: {str(e)}")
            raise

    def reset_to_default(self, db: Session, params: ResetPromptParams) -> Dict[str, Any]:
        """重置Prompt为默认值"""
        repo = PromptTemplateRepository(db)
        prompt = repo.get_by_id(params.space_id, params.prompt_id)

        if not prompt:
            raise PromptTemplateNotFoundException(f"Prompt with id '{params.prompt_id}' not found")

        # 重置为默认Prompt
        prompt.prompt_content = prompt.default_prompt
        prompt.update_time = milliseconds()

        try:
            repo.commit()
            logger.info(f"Reset prompt {params.prompt_id} to default")
            return {"code": status.HTTP_200_OK, "msg": "success", "prompt_id": params.prompt_id}
        except Exception as e:
            repo.rollback()
            logger.error(f"Prompt reset failed: {str(e)}")
            raise

    async def load_default_prompts(self, db: Session, space_id: str = "default") -> Dict[str, Any]:
        """从文件系统加载默认Prompt到数据库"""
        repo = PromptTemplateRepository(db)
        prompt_files = self._get_all_prompt_files()

        loaded_count = 0
        skipped_count = 0

        for prompt_name in prompt_files:
            try:
                # 加载文件内容
                default_content = self._load_prompt_from_file(prompt_name)

                # 检查是否已存在
                existing = repo.get_by_name(space_id=space_id, prompt_name=prompt_name)

                if existing:
                    # 更新默认Prompt内容
                    existing.default_prompt = default_content
                    # 如果当前Prompt内容为空或与默认相同，也更新
                    if not existing.prompt_content or existing.prompt_content == existing.default_prompt:
                        existing.prompt_content = default_content
                    existing.update_time = milliseconds()
                    repo.commit()
                    logger.info(f"Updated existing prompt: {prompt_name}")
                else:
                    # 创建新Prompt
                    prompt = PromptTemplateDB(
                        space_id=space_id,
                        prompt_name=prompt_name,
                        prompt_type="system",
                        prompt_content=default_content,
                        default_prompt=default_content,
                        description=f"System default prompt: {prompt_name}",
                        is_active=True,
                        create_time=milliseconds(),
                        update_time=milliseconds(),
                    )
                    repo.create(prompt)
                    logger.info(f"Created new prompt: {prompt_name}")

                loaded_count += 1

            except Exception as e:
                logger.error(f"Failed to load prompt {prompt_name}: {str(e)}")
                skipped_count += 1

        return {
            "code": status.HTTP_200_OK,
            "msg": "success",
            "loaded_count": loaded_count,
            "skipped_count": skipped_count,
            "total": len(prompt_files)
        }


prompt_manager = PromptManager()
