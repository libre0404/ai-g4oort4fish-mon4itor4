# Stage 1: Build the environment with dependencies
FROM python:3.11-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

RUN playwright install chromium

# Stage 2: Create the final, lean image
FROM python:3.11-slim-bookworm

WORKDIR /app
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV RUNNING_IN_DOCKER=true
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUTF8=1

ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}

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
    && playwright install-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright

COPY . .

EXPOSE 8000

CMD ["python", "web_server.py"]
