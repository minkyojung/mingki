FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY code_mcp ./code_mcp

ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8002 PYTHONUNBUFFERED=1
EXPOSE 8002
CMD ["python", "-m", "code_mcp.server"]
