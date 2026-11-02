# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""服务端配置：环境变量驱动（pydantic-settings），仅服务层使用。

SDK 侧仍以 `CodeSearchConfig` 构造注入为准，本模块只负责把部署环境的
变量收敛成服务进程参数。
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from openjiuwen_codesearch.config.env_file import ensure_dotenv_loaded

ensure_dotenv_loaded()


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODESEARCH_", env_file=".env", extra="ignore")

    # 监听地址与同系列产品保持一致（0.0.0.0）。本服务不含鉴权，
    # 部署到可从外部访问的网络时需自行在前置网关上做访问控制。
    host: str = "0.0.0.0"
    port: int = 8100
    log_level: str = "INFO"
    # 单次检索的服务端上限（秒）；超时返回 504，避免连接长期挂起
    search_timeout_seconds: float = 900.0
    # 允许被索引的根目录白名单（os.pathsep 分隔，Linux/macOS 为 ":"）。
    # 留空 = 禁用索引接口：/v1/index 一律拒绝。
    # 不设白名单时，任何能访问本服务的人都可让服务端索引宿主机任意目录下的
    # .py 文件，并通过 /v1/search 读回其内容。
    index_roots: str = ""

    def allowed_index_roots(self) -> list[Path]:
        return [Path(r).expanduser().resolve() for r in self.index_roots.split(os.pathsep) if r.strip()]


settings = ServerSettings()
