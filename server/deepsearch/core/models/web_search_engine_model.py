# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


class WebSearchEngineModel(Base):
    __tablename__ = 'web_search_engine'

    web_search_engine_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    space_id: Mapped[str] = mapped_column(String(255), nullable=False)
    search_engine_name: Mapped[str] = mapped_column(String(255), nullable=False)
    search_api_key: Mapped[str] = mapped_column(String(255), nullable=False)
    search_url: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    create_time: Mapped[str] = mapped_column(String(255), default=lambda: datetime.now(timezone.utc)
                                             .replace(tzinfo=timezone.utc).isoformat())
    update_time: Mapped[str] = mapped_column(String(255),
                                             default=lambda: datetime.now(timezone.utc).replace(
                                                 tzinfo=timezone.utc).isoformat(),
                                             onupdate=datetime.now(timezone.utc).replace(
                                                 tzinfo=timezone.utc).isoformat()
                                             )
