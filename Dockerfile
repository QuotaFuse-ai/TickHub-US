# 默认以 stdio 传输运行，由 MCP 客户端（Claude Desktop / IDE 插件等）拉起容器。
# HTTP 传输（th-us mcp http）在容器里必然对外监听，须先落实 SEC-03 的鉴权方案，
# 见 docs/tasks/TASK-QUOT-48/security-review.md。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TICKHUB_US_CONFIG_DIR=/usr/local/share/tickhub-us/config

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY config ./config
COPY src ./src

RUN pip install . \
    && useradd --create-home --uid 10001 tickhub

USER tickhub

ENTRYPOINT ["th-us"]
CMD ["mcp", "stdio"]
