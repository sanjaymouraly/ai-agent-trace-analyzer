# Deployment

TraceLens runs as a single API process that also serves the dashboard. It is
designed to start with one command and to stay small.

## Contents

- [Docker Compose (recommended)](#docker-compose-recommended)
- [PostgreSQL](#postgresql)
- [Redis background queue](#redis-background-queue)
- [Native deployment](#native-deployment)
- [Reverse proxy](#reverse-proxy)
- [Authentication](#authentication)
- [Migrations](#migrations)
- [Backup and restore](#backup-and-restore)
- [Scaling](#scaling)
- [Monitoring](#monitoring)
- [Resource limits](#resource-limits)

## Docker Compose (recommended)

```bash
docker compose up --build -d
curl http://127.0.0.1:8000/api/v1/ready
```

The image is a two-stage build: Node builds the dashboard, Python 3.12 runs the
API. The container runs as a non-root user, with a read-only root filesystem, a
tmpfs `/tmp`, all capabilities dropped and `no-new-privileges`.

Data lives in the named volume `tracelens-data` (`/app/data`).

| Command | Effect |
|---|---|
| `docker compose up --build` | Build and start |
| `docker compose down` | Stop and remove the container |
| `docker compose down -v` | Also delete the stored traces |

## PostgreSQL

SQLite is the right default for a single-user local install. Use PostgreSQL for
a shared or multi-user deployment.

```bash
docker compose --profile postgres up --build
```

Then point the application at it:

```bash
DATABASE_URL=postgresql+psycopg://tracelens:tracelens@postgres:5432/tracelens \
  docker compose up --build
```

Run the migrations once before the first start (the container entrypoint does
this automatically):

```bash
docker compose exec tracelens python -m alembic upgrade head
```

**Pool sizing.** The default SQLAlchemy pool is fine for a single process. With
multiple workers, set `pool_size` and `max_overflow` explicitly, or put PgBouncer
in front of PostgreSQL.

## Redis background queue

Redis is optional. It is only needed when you run more than one API process
and want a shared background queue.

```bash
docker compose --profile redis up --build
DATABASE_URL=postgresql+psycopg://… REDIS_URL=redis://redis:6379/0 docker compose up --build
```

Without `REDIS_URL`, TraceLens uses an in-process asyncio worker with a bounded
queue. That is sufficient for a single process and is the recommended setup.

## Native deployment

```bash
python -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install --no-deps -e .

npm --prefix frontend ci
npm --prefix frontend run build

export DATABASE_URL=postgresql+psycopg://user:pass@host:5432/tracelens
export APP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

python -m alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Use a process supervisor (systemd, supervisord, or a container platform) to
restart the service.

> **One worker per SQLite database.** SQLite serializes writers. Use
> PostgreSQL before scaling out the API workers.

## Reverse proxy

Terminate TLS at the proxy and forward to the app. The application sets
`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` and a
restrictive `Content-Security-Policy` on every response.

```nginx
server {
    listen 443 ssl http2;
    server_name tracelens.example.com;

    ssl_certificate     /etc/letsencrypt/live/tracelens.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tracelens.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 12m;   # slightly above MAX_UPLOAD_BYTES
    }
}
```

Set `CORS_ORIGINS` to the exact origins that may call the API from a browser.
The dashboard served from the same origin needs no CORS entry.

## Authentication

`APP_AUTH_TOKEN` enables authenticated mode:

- every `/api/v1/*` request (except health and readiness) needs
  `Authorization: Bearer <token>`;
- the deployment token maps to the `default` workspace;
- workspace-scoped keys can be minted with
  `POST /api/v1/workspaces/{slug}/keys` and are shown exactly once.

```bash
curl -H "Authorization: Bearer $APP_AUTH_TOKEN" http://127.0.0.1:8000/api/v1/runs
```

**Single-user local mode vs authenticated multi-user mode.** With no token
configured, every request is treated as the local user in the `default`
workspace: there is no identity, no audit actor distinction beyond
`local-user`, and no isolation. That is convenient for a laptop and wrong for
a shared deployment. Set `APP_AUTH_TOKEN` (and use workspace keys) for any
deployment that is reachable by more than one person.

## Migrations

```bash
python -m alembic upgrade head          # apply
python -m alembic downgrade -1           # roll back one revision
python -m alembic current                # show the applied revision
python -m alembic revision --autogenerate -m "describe the change"
```

The Docker entrypoint applies pending migrations before starting the server.
Take a backup before migrating a production database.

## Backup and restore

**PostgreSQL**

```bash
pg_dump -Fc -h db -U tracelens tracelens > tracelens-$(date +%F).dump
pg_restore -h db -U tracelens -d tracelens --clean tracelens-2026-10-19.dump
```

**SQLite** — stop the service first, then copy the file (including the `-wal`
and `-shm` files if present, or use the online backup API):

```bash
sqlite3 data/tracelens.db ".backup '/backups/tracelens-$(date +%F).db'"
```

The backup contains the canonical traces, the original payloads, the findings,
the audit log **and** the encrypted provider credentials. `APP_SECRET_KEY` is
**not** in the database: without the same key, stored credentials cannot be
decrypted after a restore. Keep the secret key in your secret manager, not
only in the backup.

## Scaling

The analysis is CPU-bound and works on one trace at a time in memory. The
practical scaling path is:

1. move to PostgreSQL;
2. run multiple API workers behind the proxy (the dashboard is static, so it
   scales trivially);
3. move the background queue to Redis;
4. keep ingestion and analysis asynchronous for very large batches by posting
   traces and letting the worker materialize the findings.

## Monitoring

| Signal | Where |
|---|---|
| Liveness | `GET /api/v1/health` |
| Readiness | `GET /api/v1/ready` |
| Structured logs | stdout, one JSON object per line |
| Audit trail | `GET /api/v1/audit` |
| AI spend | `GET /api/v1/llm/status` (`cost_spent_today_usd`) |

Example log line:

```json
{"ts":"2026-10-19T16:49:47Z","level":"INFO","logger":"tracelens.ingestion","message":"trace import records=1 inserted=1 updated=0"}
```

To export traces to an OpenTelemetry collector, set
`OTEL_EXPORTER_OTLP_ENDPOINT` and install the optional packages:

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-fastapi
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
```

## Resource limits

| Knob | Variable | Default | Notes |
|---|---|---|---|
| Upload size | `MAX_UPLOAD_BYTES` | 10 MiB | Raise the proxy limit too |
| Spans per run | `MAX_SPANS_PER_RUN` | 20 000 | Bounds analysis memory |
| Records per batch | `MAX_BATCH_RECORDS` | 500 | Bounds request handling time |
| Spans per batch | `MAX_BATCH_SPANS` | 100 000 | Bounds total work per request |
| Request rate | `RATE_LIMIT_PER_MINUTE` | 240 | Per client address |
| Retention | `TRACE_RETENTION_DAYS` | 0 (off) | Deletes runs older than N days |
| Compare span cap | *(code constant)* | 5 000 per run | Bounds deterministic alignment |
| Experiment step cap | *(code constant)* | 100 | Bounds the sandbox |
| Provider response cap | *(code constant)* | 1 MiB | Bounds outbound reads |

Data retention: set `TRACE_RETENTION_DAYS` to a positive number and runs older
than that are deleted at startup and hourly. Deletion cascades to spans,
findings, hypotheses, annotations and experiments. Everything is recorded in
the audit log.
