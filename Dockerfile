# Stage 1: Build the environment with dependencies
FROM python:3.11-slim-bookworm AS builder

# 防交互提示
ENV DEBIAN_FRONTEND=noninteractive

# 创建虚拟环境
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 安装 Python 依赖到虚拟环境中（使用国内镜像源加速）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# Stage 2: Create the final, lean image
FROM python:3.11-slim-bookworm

# 基本环境变量
ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUTF8=1
ENV RUNNING_IN_DOCKER=true

# Playwright 浏览器缓存路径
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

WORKDIR /app

# 从 builder 阶段复制虚拟环境
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}

# 安装运行 Playwright/Chromium 所需系统依赖 + zbar 等
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libzbar0 \
        curl \
        wget \
        iputils-ping \
        dnsutils \
        iproute2 \
        netcat-openbsd \
        telnet \
    && rm -rf /var/lib/apt/lists/*

# 安装 Playwright 依赖和 Chromium 浏览器（使用 venv 中的 playwright）
RUN playwright install-deps chromium \
    && playwright install chromium

# 复制预先下载好的浏览器缓存（可选，这里仍保留）
# 如果 builder 阶段也安装了浏览器，可取消下面这一行
# COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

# 复制应用代码（.dockerignore 负责排除无关文件）
COPY . .

# 创建必要目录
RUN mkdir -p jsonl logs images

# 如需 Web 控制台可暴露 8000（仅 spider_v2 监控时可忽略）
EXPOSE 8000

# 默认入口：直接跑监控脚本（可在 docker-compose 中覆盖）
CMD ["python", "spider_v2.py"]
