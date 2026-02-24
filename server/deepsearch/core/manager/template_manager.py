# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any

from fastapi import status
from sqlalchemy.orm import Session

from openjiuwen_deepsearch.algorithm.report_template.template_generator import TemplateGenerator
from server.core.database import milliseconds
from server.deepsearch.common.exception.exceptions import (
    ReportTemplateBasicException,
    TemplateNotFoundException,
    TemplateGenerationException,
    TemplateValidationError,
)
from server.deepsearch.core.manager.repositories.report_template_repository import ReportTemplateRepository
from server.deepsearch.core.models.report_template import ReportTemplateDB

logger = logging.getLogger(__name__)


@dataclass
class ImportTemplateParams:
    """Parameters for importing a template"""
    space_id: str
    template_name: str
    template_desc: str
    file_name: str
    file_stream: str  # Base64
    is_template: bool
    llm_config: dict


@dataclass
class UpdateTemplateParams:
    """Parameters for updating a template"""
    space_id: str
    template_id: int
    template_content: str  # Base64
    template_name: str
    template_desc: str


class ReportTemplateManager:
    """The singleton for template persistence and management"""
    _NAME_PATTERN = re.compile(r'^[\u4e00-\u9fa5a-zA-Z0-9_\-\.]+$')
    _MAX_NAME_LENGTH = 200

    def __init__(self):
        pass

    def _validate_template_name(self, name: str) -> None:
        """Validate template name"""
        if not name:
            raise TemplateValidationError("Template name cannot be empty")

        name = name.strip()
        if len(name) > self._MAX_NAME_LENGTH:
            raise TemplateValidationError(f"Template name too long")

        if not self._NAME_PATTERN.match(name):
            raise TemplateValidationError(
                f"Invalid template name: {name}. Only Chinese/English letters, "
                f"numbers, underscores (_), hyphens (-), and dots (.) are allowed."
            )

    async def import_template(
            self,
            db: Session,
            params: ImportTemplateParams
    ) -> Dict[str, Any]:
        """Import a template, overwriting existing if name is same"""
        repo = ReportTemplateRepository(db)

        try:
            self._validate_template_name(params.template_name)

            api_key = params.llm_config.get("api_key", "")
            if isinstance(api_key, str):
                params.llm_config["api_key"] = bytearray(api_key, encoding="utf-8")
            llm_config = params.llm_config
            agent_config_dict = {"llm_config": llm_config}

            result = await TemplateGenerator.generate_template(
                file_name=params.file_name,
                file_stream=params.file_stream,
                is_template=params.is_template,
                agent_config=agent_config_dict,
            )

            if result.get("status") != "success":
                error_msg = result.get("error_message", "AI Generation failed")
                logger.error(
                    "Template %s generation failed: %s",
                    params.template_name,
                    error_msg,
                )
                raise TemplateGenerationException(error_msg)

            new_content = result.get("template_content", "")
            existing = repo.get_by_name(
                space_id=params.space_id,
                template_name=params.template_name
            )

            if existing:
                existing.template_content = new_content
                existing.template_desc = params.template_desc
                existing.update_time = milliseconds()
                repo.commit()
                target_id = existing.template_id
                logger.info("Overwrote existing template: %s", params.template_name)
            else:
                template = ReportTemplateDB(
                    space_id=params.space_id,
                    template_name=params.template_name,
                    template_content=new_content,
                    template_desc=params.template_desc,
                    create_time=milliseconds(),
                    update_time=milliseconds(),
                )
                repo.create(template)
                target_id = template.template_id
                logger.info("Created new template: %s", params.template_name)

            return {"code": status.HTTP_200_OK, "msg": "success", "template_id": target_id}

        except ReportTemplateBasicException:
            repo.rollback()
            raise
        except Exception:
            repo.rollback()
            raise

    def list_templates(self, db: Session, space_id: str) -> Dict[str, Any]:
        """List all templates in a space"""
        repo = ReportTemplateRepository(db)
        templates = repo.list_by_space(space_id)

        data = []
        for template in templates:
            create_time_dt = datetime.fromtimestamp(template.create_time / 1000)
            data.append({
                "template_name": template.template_name,
                "template_desc": template.template_desc or "",
                "template_id": template.template_id,
                "create_time": create_time_dt.strftime("%Y-%m-%d %H:%M:%S"),
            })

        return {"code": status.HTTP_200_OK, "msg": "success", "data": data}

    def get_template_content(self, db: Session, space_id: str, template_id: int) -> Dict[str, Any]:
        """Return the content of a template"""
        repo = ReportTemplateRepository(db)
        template = repo.get_by_id(space_id, template_id)

        if not template:
            logger.info(f"Template not found:{template_id}")
            raise TemplateNotFoundException(f"Template with id '{template_id}' not found")

        return {
            "code": status.HTTP_200_OK,
            "msg": "success",
            "template_content": template.template_content
        }

    def delete_template(self, db: Session, space_id: str, template_id: int) -> Dict[str, Any]:
        """Delete a specific template"""
        repo = ReportTemplateRepository(db)
        template = repo.get_by_id(space_id, template_id)

        if not template:
            raise TemplateNotFoundException(f"Template with id '{template_id}' not found")

        repo.delete(template)
        logger.info(f"Deleted template: {template_id}")
        return {"code": status.HTTP_200_OK, "msg": "success"}

    def update_template(self, db: Session, params: UpdateTemplateParams) -> Dict[str, Any]:
        """Update a specific template"""
        repo = ReportTemplateRepository(db)
        self._validate_template_name(params.template_name)

        template = repo.get_by_id(params.space_id, params.template_id)
        if not template:
            raise TemplateNotFoundException(f"Template with id '{params.template_id}' not found")

        # 名称变更时的冲突校验
        if template.template_name != params.template_name:
            existing = repo.get_by_name(space_id=params.space_id, template_name=params.template_name)
            if existing and existing.template_id != params.template_id:
                raise TemplateValidationError(f"Template name '{params.template_name}' already exists")

        template.template_name = params.template_name
        template.template_desc = params.template_desc
        template.template_content = params.template_content
        template.update_time = milliseconds()

        try:
            repo.commit()
            return {"code": status.HTTP_200_OK, "msg": "success", "template_id": params.template_id}
        except Exception as e:
            repo.rollback()
            logger.error(f"Template update failed: {str(e)}")
            raise


report_template_manager = ReportTemplateManager()
