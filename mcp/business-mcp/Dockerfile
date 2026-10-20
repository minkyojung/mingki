FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY business_mcp ./business_mcp

ENV PATH="/app/.venv/bin:$PATH" HOST=0.0.0.0 PORT=8003 PYTHONUNBUFFERED=1
EXPOSE 8003
CMD ["python", "-m", "business_mcp.server"]
