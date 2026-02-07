# 使用 Python 3.11 作为基础镜像
FROM python:3.11-slim-bookworm

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN pip install uv

# 先复制依赖文件（利用 Docker 缓存层优化构建）
COPY pyproject.toml uv.lock* ./

# 复制项目文件（uv pip install -e . 需要项目文件存在）
COPY . .

# 使用 uv 安装项目及其依赖
RUN uv pip install --system -e . --group backend

# 创建必要的目录
RUN mkdir -p data/databases logs

# 设置权限
RUN chmod +x start_backend.py || true

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# 启动命令
CMD ["python", "start_backend.py"]
