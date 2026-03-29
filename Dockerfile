FROM python:3.11-slim

# 系统依赖：OpenCV 运行时 + ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1-mesa-glx \
        libglib2.0-0 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先复制依赖定义，利用 Docker 缓存
COPY pyproject.toml uv.lock* ./

# 安装依赖（不安装 dev 依赖）
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# 复制项目文件
COPY src/ src/
COPY evals/ evals/
COPY scripts/ scripts/

# 创建上传目录
RUN mkdir -p uploads

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
