# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from server.deepsearch.core.models.prompt_template import PromptTemplateDB


class PromptTemplateRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, space_id: str, prompt_id: int) -> Optional[PromptTemplateDB]:
        """根据ID获取Prompt模板记录"""
        return self.db.query(PromptTemplateDB).filter(
            PromptTemplateDB.prompt_id == prompt_id,
            PromptTemplateDB.space_id == space_id
        ).first()

    def get_by_name(self, space_id: str, prompt_name: str) -> Optional[PromptTemplateDB]:
        """根据名称获取Prompt模板记录"""
        return self.db.query(PromptTemplateDB).filter(
            PromptTemplateDB.prompt_name == prompt_name,
            PromptTemplateDB.space_id == space_id
        ).first()

    def list_by_space(self, space_id: str) -> List[PromptTemplateDB]:
        """列出指定空间下的所有Prompt模板"""
        return self.db.query(PromptTemplateDB).filter(
            PromptTemplateDB.space_id == space_id
        ).order_by(desc(PromptTemplateDB.create_time)).all()

    def list_active_by_space(self, space_id: str) -> List[PromptTemplateDB]:
        """列出指定空间下所有激活的Prompt模板"""
        return self.db.query(PromptTemplateDB).filter(
            PromptTemplateDB.space_id == space_id,
            PromptTemplateDB.is_active == True
        ).order_by(desc(PromptTemplateDB.create_time)).all()

    def create(self, model: PromptTemplateDB) -> PromptTemplateDB:
        """创建并持久化新的Prompt模板记录"""
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return model

    def delete(self, model: PromptTemplateDB) -> None:
        """删除Prompt模板记录"""
        self.db.delete(model)
        self.db.commit()

    def update(self, model: PromptTemplateDB) -> PromptTemplateDB:
        """持久化Prompt模板记录的更新"""
        self.db.commit()
        self.db.refresh(model)
        return model

    def commit(self):
        """手动提交当前事务"""
        self.db.commit()

    def rollback(self):
        """回滚当前事务"""
        self.db.rollback()
