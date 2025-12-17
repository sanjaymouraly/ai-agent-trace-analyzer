# Start TraceLens locally (Windows PowerShell).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

python -m pip install --quiet --upgrade pip
pip install --require-hashes -r requirements-dev.lock
pip install --no-deps -e .

if (-not (Test-Path "frontend\node_modules")) {
    npm --prefix frontend ci
}
if (-not (Test-Path "frontend\dist\index.html")) {
    npm --prefix frontend run build
}

python -m examples.generate
python -m alembic upgrade head

if (Test-Path ".env") {
    python -m uvicorn app.main:app --env-file .env --host $env:HOST --port $env:PORT --reload
} else {
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
}
