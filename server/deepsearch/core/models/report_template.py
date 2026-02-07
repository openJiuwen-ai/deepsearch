# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from sqlalchemy import BigInteger, String, Integer, Text, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


class ReportTemplateDB(Base):
    """
    Database model for report templates.
    """

    __tablename__ = "report_template"
    __table_args__ = (
        Index("idx_space_id", "space_id"),
        UniqueConstraint(
            "space_id",
            "template_name",
            name="uq_space_template_name",
        ),
    )

    template_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, name="id")
    space_id: Mapped[str] = mapped_column(String(100), nullable=False)
    template_name: Mapped[str] = mapped_column(String(200), nullable=False)
    template_desc: Mapped[str] = mapped_column(Text, nullable=True, name="description")
    template_content: Mapped[str] = mapped_column(Text, nullable=False)
    create_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    update_time: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:
        return (
            "<ReportTemplateDB "
            f"id={self.template_id} "
            f"space_id='{self.space_id}' "
            f"template_name='{self.template_name}'>"
        )
