# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 数据库类型配置 (mysql/sqlite)
    db_type: str = "mysql"

    # mysql配置
    db_host: str = ""
    db_port: int = 3306
    db_user: str = ""
    db_password: str = ""
    deepsearch_db_name: str = ""

    # mysql数据库连接池配置， 避免断联时间过久导致前端需要发两次请求
    db_pool_pre_ping: bool = True
    db_pool_recycle: int = 3600

    # sqlite配置
    sqlite_db_path: str = "data/databases"
    deepsearch_sqlite_db: str = "agent.db"

    class Config:
        # 从当前文件位置找到项目根目录的 .env 文件
        env_file = str(Path(__file__).parent.parent.parent / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields from .env file


# Create settings instance
settings = Settings()
