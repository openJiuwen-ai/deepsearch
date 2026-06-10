ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

ARG INDEX_URL=https://pypi.org/simple
ARG APT_MIRROR=deb.debian.org

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# 安装系统依赖
RUN sed -i "s@deb.debian.org@${APT_MIRROR}@g" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install uv -i ${INDEX_URL}

# 先复制依赖文件（利用 Docker 缓存层优化构建）
COPY pyproject.toml uv.lock* ./

# 复制项目文件（uv pip install -e . 需要项目文件存在）
COPY . .

# 使用 uv 安装项目及其依赖
RUN uv pip install --system -e . --group backend --index-url ${INDEX_URL} --prerelease allow

# 创建必要的目录
RUN mkdir -p data/databases logs

# 设置权限
RUN chmod +x start_backend.py || true
RUN chmod +x server/telemetry_event_server.py || true

# 暴露端口
EXPOSE 8000
EXPOSE 8089

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health'); urllib.request.urlopen('http://localhost:8089')" || exit 1

# 同时启动：8000 主 API（DeepResearch 等）、8089 Telemetry（DeepSearch 模式 search_mode=search）
CMD ["sh", "-c", "python -m server.telemetry_event_server --host 0.0.0.0 & python start_backend.py"]
