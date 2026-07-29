# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""Knowledge base object storage requirements for distributed (Redis checkpointer) mode."""

import os
from typing import Optional

from server.core.config import settings
from server.core.manager.model_manager.utils import SecurityUtils


def knowledge_base_requires_obs() -> bool:
    """True when checkpointer is Redis (multi-instance); KB files must live in shared object storage."""
    return (settings.checkpointer_type or "").strip().lower() == "redis"


def kb_obs_misconfigured_message() -> Optional[str]:
    """
    If Redis checkpointer is enabled, return a human-readable error when OBS env is incomplete.
    Otherwise None.
    """
    if not knowledge_base_requires_obs():
        return None

    bucket = (os.getenv("OBS_BUCKET") or "").strip()
    server = (os.getenv("OBS_SERVER") or "").strip()
    region = (os.getenv("OBS_REGION") or "").strip()
    access_key_id = (
        SecurityUtils.get_decrypted_secret(
            "OBS_ACCESS_KEY_ID",
            os.getenv("OBS_ACCESS_KEY_ID", None),
        )
        or ""
    ).strip()
    secret_access_key = (
        SecurityUtils.get_decrypted_secret(
            "OBS_SECRET_ACCESS_KEY",
            os.getenv("OBS_SECRET_ACCESS_KEY", None),
        )
        or ""
    ).strip()

    missing = []
    if not server:
        missing.append("OBS_SERVER")
    if not region:
        missing.append("OBS_REGION")
    if not bucket:
        missing.append("OBS_BUCKET")
    if not access_key_id:
        missing.append("OBS_ACCESS_KEY_ID")
    if not secret_access_key:
        missing.append("OBS_SECRET_ACCESS_KEY")

    if not missing:
        return None

    return (
        "CHECKPOINTER_TYPE=redis (distributed deployment) requires object storage for knowledge "
        "base files so all instances share the same documents. "
        "Configure: " + ", ".join(missing)
    )


def assert_kb_obs_configured_for_redis() -> None:
    """Raise ValueError at startup if Redis mode is on but OBS is not fully configured."""
    msg = kb_obs_misconfigured_message()
    if msg:
        raise ValueError(msg)
