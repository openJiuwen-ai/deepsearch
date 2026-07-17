# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from sqlalchemy import BigInteger, String, Integer, Text, Boolean, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


class PromptTemplateDB(Base):
    """
    Database model for prompt templates.
    """

    __tablename__ = "prompt_template"
    __table_args__ = (
        Index("idx_space_id", "space_id"),
        Index("idx_prompt_name", "prompt_name"),
        UniqueConstraint(
            "space_id",
            "prompt_name",
            name="uq_space_prompt_name",
        ),
    )

    prompt_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, name="id")
    space_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_type: Mapped[str] = mapped_column(String(50), nullable=False, default="system")
    prompt_content: Mapped[str] = mapped_column(Text, nullable=False)
    default_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    create_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_time: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:
        return (
            "<PromptTemplateDB "
            f"id={self.prompt_id} "
            f"space_id='{self.space_id}' "
            f"prompt_name='{self.prompt_name}' "
            f"prompt_type='{self.prompt_type}'>"
        )
