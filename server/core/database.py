# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from server.core.config import settings


def get_database_url() -> str:
    """根据数据库类型生成数据库连接URL"""
    if settings.db_type.lower() == "mysql":
        return (f"mysql+pymysql://{settings.db_user}:{settings.db_password}@"
                f"{settings.db_host}:{settings.db_port}/{settings.deepsearch_db_name}?charset=utf8mb4")

    elif settings.db_type.lower() == "sqlite":
        # 确保数据库目录存在
        db_path = Path(settings.sqlite_db_path)
        db_path.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path}/{settings.deepsearch_sqlite_db}"

    else:
        raise ValueError(f"Unsupported database type: {settings.db_type.lower()}")


database_url = get_database_url()

# Create database engine
engine_kwargs = {
    "connect_args": {"check_same_thread": False} if "sqlite" in database_url else {}
}
if settings.db_type.lower() == "mysql":
    engine_kwargs.update(
        {
            "pool_pre_ping": settings.db_pool_pre_ping,
            "pool_recycle": settings.db_pool_recycle,
        }
    )
engine = create_engine(database_url, **engine_kwargs)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()


# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_milliseconds() -> int:
    """返回当前时间戳的毫秒整数部分."""
    return int(time.time() * 1000)


milliseconds = get_milliseconds
