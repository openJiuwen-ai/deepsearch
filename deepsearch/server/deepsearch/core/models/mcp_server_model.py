# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.core.database import Base


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=timezone.utc).isoformat()


class McpServerModel(Base):
    __tablename__ = 'mcp_server'

    mcp_server_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    space_id: Mapped[str] = mapped_column(String(255), nullable=False)
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    server_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    transport_type: Mapped[str] = mapped_column(String(32), nullable=False, default="streamable_http")
    headers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timeout: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)
    type: Mapped[str] = mapped_column(String(64), nullable=False, default="search")
    extension: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    create_time: Mapped[str] = mapped_column(String(255), default=_utc_now_iso)
    update_time: Mapped[str] = mapped_column(String(255), default=_utc_now_iso, onupdate=_utc_now_iso)