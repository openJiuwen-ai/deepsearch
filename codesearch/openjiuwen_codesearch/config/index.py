# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional

from pydantic import BaseModel


class EmbedConfig(BaseModel):
    service: str = "openrouter"  # openrouter | ollama | custom
    url: str = "https://openrouter.ai/api/v1/embeddings"
    model: str = "qwen/qwen3-embedding-8b"
    api_key: str = ""
    batch_size: int = 64
    # Qwen3-Embedding 官方建议的查询前缀
    query_prefix: str = (
        "Instruct: Given a code search query, retrieve relevant code snippets\nQuery: "
    )
    # 运行产物一律进 ./output/（对齐 deepsearch 惯例）；目录不存在会自动创建
    cache_dir: str = "./output/cache"
    max_retries: int = 8
    retry_backoff_seconds: float = 2.0
    timeout_seconds: float = 60.0  # 单次请求总超时


class MilvusConfig(BaseModel):
    host: str = "localhost"
    port: str = "19530"
    # 认证部署（共用实例开启鉴权的场景）；支持 "user:password" 或 API token 形式
    token: str = ""
    # Milvus 2.2+ database：比 cs_ 前缀更强一级的硬隔离（与 deepsearch 对齐的
    # 运维手段）；"default" 表示不切换
    database_name: str = "default"
    # 与其他产品（deepsearch）共用 Milvus 实例时的命名空间隔离：
    # collection 实名 = f"{collection_prefix}{name}__{schema_version}"
    collection_prefix: str = "cs_"
    # 进程内 pymilvus 连接别名：与 deepsearch 同进程共存时避免抢占 "default"
    connection_alias: str = "codesearch"
    # schema 变更必须递增版本；禁止静默复用旧结构
    # （旧实现 RESET_INDICES=True 静默 drop 的替代方案）。
    schema_version: str = "v1"
    query_batch_size: int = 32
    insert_batch_size: int = 128
    heavy_fetch_batch_size: int = 20  # 大字段按 id 分批取，防 65MB payload 上限


class IndexConfig(BaseModel):
    use_dense_embeddings: bool = False
    max_num_files_per_repo: Optional[int] = None  # None 表示不限；>=1 生效
    max_file_size_bytes: int = 5 * 1024 * 1024  # 超大文件（多为生成物）跳过并告警
    max_char_limit: int = 65535   # Milvus VARCHAR 上限，超长 chunk 截断
    max_num_calls: int = 4096     # calls 数组容量上限，单条截 2048 字符
    # trigram 字段是存储大头（hex 编码 ≈ 原文 7 倍体积）。空间敏感场景可关闭，
    # 代价：use_trigram=True 的精确子串检索失效（agent 的一条工具臂退化）。
    enable_trigram: bool = True
