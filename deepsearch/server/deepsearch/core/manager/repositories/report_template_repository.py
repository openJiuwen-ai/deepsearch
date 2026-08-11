# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from server.deepsearch.core.models.report_template import ReportTemplateDB


class ReportTemplateRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, space_id: str, template_id: int) -> Optional[ReportTemplateDB]:
        """Get a template record by template id"""
        return self.db.query(ReportTemplateDB).filter(
            ReportTemplateDB.template_id == template_id,
            ReportTemplateDB.space_id == space_id
        ).first()

    def get_by_name(self, space_id: str, template_name: str) -> Optional[ReportTemplateDB]:
        """Get a template record by template name"""
        return self.db.query(ReportTemplateDB).filter(
            ReportTemplateDB.template_name == template_name,
            ReportTemplateDB.space_id == space_id
        ).first()

    def list_by_space(self, space_id: str) -> List[ReportTemplateDB]:
        """List all templates under the specified space"""
        return self.db.query(ReportTemplateDB).filter(
            ReportTemplateDB.space_id == space_id
        ).order_by(desc(ReportTemplateDB.create_time)).all()

    def create(self, model: ReportTemplateDB) -> ReportTemplateDB:
        """Create and persist a new template record"""
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return model

    def delete(self, model: ReportTemplateDB) -> None:
        """Delete a template record"""
        self.db.delete(model)
        self.db.commit()

    def update(self, model: ReportTemplateDB) -> ReportTemplateDB:
        """Persist updates to a template record"""
        self.db.commit()
        self.db.refresh(model)
        return model

    def commit(self):
        """Manually commit current transaction"""
        self.db.commit()

    def rollback(self):
        """Rollback current transaction"""
        self.db.rollback()
