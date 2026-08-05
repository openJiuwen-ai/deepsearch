# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# openJiuwen-CodeSearch 容器镜像（服务形态，结构与同系列产品对齐）。
#
#
# 构建上下文必须是仓库根（需要同时拷贝 base/ 与 codesearch/）：
#   docker build -f codesearch/docker/Dockerfile -t openjiuwen-codesearch:0.2.0 .
# 运行示例（服务形态，连接宿主 Milvus）：
#   docker run --rm -p 8100:8100 -e CODESEARCH_LLM_API_KEY -e CODESEARCH_LLM_BASE_URL \
#     -e MILVUS_HOST=host.docker.internal \
#     -v /path/to/repo:/repo -v $(pwd)/output:/app/output openjiuwen-codesearch:0.2.0

ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

ARG INDEX_URL=https://pypi.org/simple
ARG APT_MIRROR=deb.debian.org

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# curl 供 HEALTHCHECK 使用；依赖全部来自包索引，无需 git
RUN sed -i "s@deb.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# 先拷依赖声明利用构建缓存层
COPY base/pyproject.toml ./base/pyproject.toml
COPY codesearch/pyproject.toml ./codesearch/pyproject.toml

COPY base/ ./base/
COPY codesearch/ ./codesearch/

RUN pip install --no-cache-dir -i ${INDEX_URL} ./base && \
    pip install --no-cache-dir -i ${INDEX_URL} "./codesearch[milvus,llm,server]"

# 运行产物统一落 /app/output（挂卷持久化）
RUN mkdir -p /app/output

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8100/api/health || exit 1

# 默认以服务形态启动；如需 CLI：
#   docker run --entrypoint codesearch <image> index --repo /repo --collection demo
#
# 用 console script 而非 `python start_backend.py`：后者要求源码树在场，
# 且工作目录恰好是项目根（靠 sys.path[0] 才 import 得到服务包），属隐性依赖。
# 服务实现已随包分发，装完即可用命令启动。
CMD ["codesearch-server"]
