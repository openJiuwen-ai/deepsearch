# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
from pathlib import Path

from pydantic import model_validator
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

    # Checkpointer 配置 (in_memory / persistence / redis)
    checkpointer_type: str = "in_memory"

    # Persistence Checkpointer 配置
    checkpointer_db_type: str = "sqlite"
    checkpointer_db_path: str = "data/databases/checkpointer.db"

    # Redis Checkpointer 配置
    redis_url: str = "redis://localhost:6379"
    redis_cluster_mode: bool = False
    redis_ttl: int = 7200
    redis_refresh_on_read: bool = True


    class Config:
        # 从当前文件位置找到项目根目录的 .env 文件
        env_file = str(Path(__file__).parent.parent.parent / ".env")
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields from .env file

    @model_validator(mode="after")
    def redis_checkpointer_requires_shared_db(self) -> "Settings":
        """Redis 仅同步工作流会话；知识库等元数据在应用库。SQLite 为进程本地文件，多实例无法共享。"""
        cp = (self.checkpointer_type or "").strip().lower()
        db = (self.db_type or "").strip().lower()
        if cp == "redis" and db != "mysql":
            raise ValueError(
                "CHECKPOINTER_TYPE=redis 时必须设置 DB_TYPE=mysql 并让所有实例连接同一 MySQL。"
                " SQLite 数据库文件仅在单机进程内有效，多实例下知识库等业务数据无法互通。"
            )
        return self


# Create settings instance
settings = Settings()
