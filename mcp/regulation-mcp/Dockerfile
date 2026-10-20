FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY regulation_mcp ./regulation_mcp

ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8001 PYTHONUNBUFFERED=1
EXPOSE 8001
CMD ["python", "-m", "regulation_mcp.server"]
