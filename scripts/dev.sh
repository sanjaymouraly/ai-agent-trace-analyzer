#!/usr/bin/env bash
# Start TraceLens locally (Linux / macOS).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --quiet --upgrade pip
pip install --require-hashes -r requirements-dev.lock
pip install --no-deps -e .

if [[ ! -d frontend/node_modules ]]; then
  npm --prefix frontend ci
fi
if [[ ! -f frontend/dist/index.html ]]; then
  npm --prefix frontend run build
fi

python -m examples.generate
python -m alembic upgrade head

if [[ -f .env ]]; then
  exec uvicorn app.main:app --env-file .env --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --reload
fi
exec uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" --reload
