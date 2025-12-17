# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Stage 1 — build the single-page dashboard
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — python runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////app/data/tracelens.db \
    SEED_DEMO=true \
    APP_NAME=TraceLens

WORKDIR /app

RUN groupadd --system tracelens && useradd --system --gid tracelens --home-dir /app tracelens \
    && mkdir -p /app/data && chown -R tracelens:tracelens /app

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY --chown=tracelens:tracelens alembic.ini ./
COPY --chown=tracelens:tracelens alembic/ ./alembic/
COPY --chown=tracelens:tracelens app/ ./app/
COPY --chown=tracelens:tracelens agent_debugger/ ./agent_debugger/
COPY --chown=tracelens:tracelens examples/ ./examples/
COPY --chown=tracelens:tracelens evals/ ./evals/
COPY --chown=tracelens:tracelens tests/ ./tests/
COPY --chown=tracelens:tracelens pyproject.toml README.md LICENSE ./
COPY --chown=tracelens:tracelens scripts/ ./scripts/

# The built dashboard is served by the API on the same origin.
COPY --from=frontend --chown=tracelens:tracelens /build/dist/ ./frontend/dist/

RUN mkdir -p /app/data && chown -R tracelens:tracelens /app/data
USER tracelens
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3)"

CMD ["sh", "-c", "python -m alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
