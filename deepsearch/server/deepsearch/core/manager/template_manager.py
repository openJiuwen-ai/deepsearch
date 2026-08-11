# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import logging
import re
import time
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

    async def import_template(
            self,
            db: Session,
            params: ImportTemplateParams
    ) -> Dict[str, Any]:
        """Import a template, overwriting existing if name is same"""
        repo = ReportTemplateRepository(db)
        start_time = time.perf_counter()
        logger.info(
            "Importing report template space_id=%s template_name=%s file_name=%s is_template=%s",
            params.space_id,
            params.template_name,
            params.file_name,
            params.is_template,
        )

        try:
            self._validate_template_name(params.template_name)

            if "general" in params.llm_config:
                for _, llm_config in params.llm_config.items():
                    api_key = llm_config.get("api_key", "")
                    if isinstance(api_key, str):
                        llm_config["api_key"] = bytearray(api_key, encoding="utf-8")
            else:
                api_key = params.llm_config.get("api_key", "")
                if isinstance(api_key, str):
                    params.llm_config["api_key"] = bytearray(api_key, encoding="utf-8")

            llm_config = params.llm_config
            agent_config_dict = {"llm_config": dict(general=llm_config) if "model_name" in llm_config else llm_config}

            result = await TemplateGenerator.generate_template(
                file_name=params.file_name,
                file_stream=params.file_stream,
                is_template=params.is_template,
                agent_config=agent_config_dict,
            )
            logger.info(
                "Generated report template content space_id=%s template_name=%s status=%s duration_ms=%.2f",
                params.space_id,
                params.template_name,
                result.get("status"),
                (time.perf_counter() - start_time) * 1000,
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
                logger.info(
                    "Overwrote existing template space_id=%s template_id=%s template_name=%s",
                    params.space_id,
                    target_id,
                    params.template_name,
                )
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
                logger.info(
                    "Created new template space_id=%s template_id=%s template_name=%s",
                    params.space_id,
                    target_id,
                    params.template_name,
                )

            logger.info(
                "Imported report template space_id=%s template_id=%s template_name=%s duration_ms=%.2f",
                params.space_id,
                target_id,
                params.template_name,
                (time.perf_counter() - start_time) * 1000,
            )
            return {"code": status.HTTP_200_OK, "msg": "success", "template_id": target_id}

        except ReportTemplateBasicException:
            repo.rollback()
            logger.warning(
                "Report template import failed with business exception space_id=%s template_name=%s duration_ms=%.2f",
                params.space_id,
                params.template_name,
                (time.perf_counter() - start_time) * 1000,
            )
            raise
        except Exception:
            repo.rollback()
            logger.exception(
                "Report template import failed space_id=%s template_name=%s duration_ms=%.2f",
                params.space_id,
                params.template_name,
                (time.perf_counter() - start_time) * 1000,
            )
            raise

    @staticmethod
    def list_templates(db: Session, space_id: str) -> Dict[str, Any]:
        """List all templates in a space"""
        logger.info("Listing report templates space_id=%s", space_id)
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

        logger.info("Listed report templates space_id=%s count=%s", space_id, len(data))
        return {"code": status.HTTP_200_OK, "msg": "success", "data": data}

    @staticmethod
    def get_template_content(db: Session, space_id: str, template_id: int) -> Dict[str, Any]:
        """Return the content of a template"""
        logger.info("Getting report template content space_id=%s template_id=%s", space_id, template_id)
        repo = ReportTemplateRepository(db)
        template = repo.get_by_id(space_id, template_id)

        if not template:
            logger.info("Report template not found space_id=%s template_id=%s", space_id, template_id)
            raise TemplateNotFoundException(f"Template with id '{template_id}' not found")

        logger.info("Got report template content space_id=%s template_id=%s", space_id, template_id)
        return {
            "code": status.HTTP_200_OK,
            "msg": "success",
            "template_content": template.template_content
        }

    @staticmethod
    def delete_template(db: Session, space_id: str, template_id: int) -> Dict[str, Any]:
        """Delete a specific template"""
        logger.info("Deleting report template space_id=%s template_id=%s", space_id, template_id)
        repo = ReportTemplateRepository(db)
        template = repo.get_by_id(space_id, template_id)

        if not template:
            raise TemplateNotFoundException(f"Template with id '{template_id}' not found")

        repo.delete(template)
        logger.info("Deleted report template space_id=%s template_id=%s", space_id, template_id)
        return {"code": status.HTTP_200_OK, "msg": "success"}

    def update_template(self, db: Session, params: UpdateTemplateParams) -> Dict[str, Any]:
        """Update a specific template"""
        repo = ReportTemplateRepository(db)
        logger.info(
            "Updating report template space_id=%s template_id=%s template_name=%s",
            params.space_id,
            params.template_id,
            params.template_name,
        )
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
            logger.info(
                "Updated report template space_id=%s template_id=%s template_name=%s",
                params.space_id,
                params.template_id,
                params.template_name,
            )
            return {"code": status.HTTP_200_OK, "msg": "success", "template_id": params.template_id}
        except Exception as e:
            repo.rollback()
            logger.error(f"Template update failed: {str(e)}")
            raise

    def _validate_template_name(self, name: str) -> None:
        """Validate template name"""
        if not name:
            raise TemplateValidationError("Template name cannot be empty")

        name = name.strip()
        if len(name) > self._MAX_NAME_LENGTH:
            raise TemplateValidationError("Template name too long")

        if not self._NAME_PATTERN.match(name):
            raise TemplateValidationError(
                f"Invalid template name: {name}. Only Chinese/English letters, "
                f"numbers, underscores (_), hyphens (-), and dots (.) are allowed."
            )


report_template_manager = ReportTemplateManager()
