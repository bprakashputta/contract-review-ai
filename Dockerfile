FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# dependencies first (cached layer), then the project
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY prompts ./prompts
COPY playbooks ./playbooks
COPY web ./web
RUN uv sync --locked --no-dev

RUN useradd --create-home app && mkdir -p /app/data && chown app /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["/app/.venv/bin/uvicorn", "contract_review_ai.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
