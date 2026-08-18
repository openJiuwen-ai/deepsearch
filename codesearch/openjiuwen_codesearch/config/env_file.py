# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""把磁盘上的 ``.env`` 注入 ``os.environ``（``.env`` 覆盖同名 export）。

``CodeSearchConfig.from_env()`` 与服务端 Settings 都依赖进程环境；
仅放置 ``.env`` 文件而无人加载时，``os.getenv`` 读不到变量。
找不到 ``.env`` 时仍可依赖 ``export`` / Docker ``-e`` 等进程环境注入。
"""

import logging
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_LOADED = False


def _candidate_paths() -> list[Path]:
    """优先 cwd，再向上查找若干层（方便在仓库子目录启动）。"""
    seen: set[Path] = set()
    out: list[Path] = []
    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents[:4]):
        path = (base / ".env").resolve()
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def ensure_dotenv_loaded() -> Path | None:
    """加载第一个存在的 ``.env``；文件中的键覆盖同名进程环境变量。可重复调用。"""
    global _LOADED
    if _LOADED:
        return None
    _LOADED = True
    for path in _candidate_paths():
        if path.is_file():
            # override=True：.env 权重大于已有 export；无 .env 时 export 仍生效
            load_dotenv(dotenv_path=path, override=True)
            logger.debug("Loaded environment from %s (overrides process env)", path)
            return path
    return None
