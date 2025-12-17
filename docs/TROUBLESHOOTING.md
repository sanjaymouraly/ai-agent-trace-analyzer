# Troubleshooting

## Contents

- [The dashboard shows "frontend_not_built"](#the-dashboard-shows-frontend_not_built)
- [The API returns 401](#the-api-returns-401)
- [The dashboard cannot reach the API](#the-dashboard-cannot-reach-the-api)
- [No demo traces are visible](#no-demo-traces-are-visible)
- [Import fails with "trace_validation_failed"](#import-fails-with-trace_validation_failed)
- [Import fails with "payload_too_large"](#import-fails-with-payload_too_large)
- ["database is locked" on SQLite](#database-is-locked-on-sqlite)
- [Alembic and the ORM disagree](#alembic-and-the-orm-disagree)
- [The AI assistant is not ready](#the-ai-assistant-is-not-ready)
- [The AI assistant returns "provider_empty_response"](#the-ai-assistant-returns-provider_empty_response)
- [The AI assistant cannot reach a local Ollama server](#the-ai-assistant-cannot-reach-a-local-ollama-server)
- [A custom provider URL is rejected](#a-custom-provider-url-is-rejected)
- ["ai_daily_budget_reached"](#ai_daily_budget_reached)
- [The dependency graph is empty](#the-dependency-graph-is-empty)
- [The critical path is missing](#the-critical-path-is-missing)
- [A comparison reports a divergence between unrelated runs](#a-comparison-reports-a-divergence-between-unrelated-runs)
- [Playwright cannot launch a browser](#playwright-cannot-launch-a-browser)
- [Windows: the execution policy blocks the virtual environment](#windows-the-execution-policy-blocks-the-virtual-environment)
- [Logs are not JSON](#logs-are-not-json)
- [Resetting the local state](#resetting-the-local-state)

## The dashboard shows "frontend_not_built"

The API is running but the SPA bundle is missing.

```bash
npm --prefix frontend ci
npm --prefix frontend run build
ls frontend/dist/index.html
```

The API serves `frontend/dist` when it exists. During frontend development you
can skip the build and run Vite instead:

```bash
npm --prefix frontend run dev   # http://localhost:5173, proxies /api to :8000
```

## The API returns 401

`APP_AUTH_TOKEN` is set and the request has no (or an invalid) bearer token.

```bash
curl -H "Authorization: Bearer $APP_AUTH_TOKEN" http://127.0.0.1:8000/api/v1/runs
```

In the dashboard, use the **•••** button at the bottom of the sidebar and paste
the token. The token is kept in `sessionStorage` for the browser tab only.

To run without authentication, unset `APP_AUTH_TOKEN` and restart. That is only
appropriate for a local, single-user install.

## The dashboard cannot reach the API

- In production the dashboard is served by the API on the same origin — check
  that `frontend/dist` was built and that the server was restarted.
- In development, confirm that `uvicorn` is on port 8000 and that
  `VITE_API_TARGET` in `frontend/vite.config.ts` points at the right host.
- Check the browser console for a failed request and the server log for the
  corresponding request id.

## No demo traces are visible

Demo data is seeded when `SEED_DEMO=true` (the default). It is seeded at
startup, so a database that already exists keeps its contents.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/demo/seed
```

or click **Load demo traces** in the dashboard.

## Import fails with "trace_validation_failed"

Common causes:

| Message | Fix |
|---|---|
| `spans/actions must be a JSON array.` | The trace has no `spans` array. |
| `span_id values must be unique within a run.` | Two spans share an id. |
| `trace_id and run_id must not be empty.` | Both identifiers are required. |
| `Trace schema validation failed: [...]` | A field has the wrong type; the error names the field path. |
| `Only .json and .jsonl trace uploads are supported.` | The file extension is not supported. |
| `Invalid JSONL at line N` | A malformed line in a JSONL file. |

Validate a file without storing it:

```bash
tracelens validate path/to/trace.json
```

## Import fails with "payload_too_large"

Raise `MAX_UPLOAD_BYTES` **and** the reverse proxy's body limit
(`client_max_body_size` in nginx). For very large traces, split the file into
JSONL and upload the parts separately.

## "database is locked" on SQLite

SQLite serializes writers. Either reduce concurrency, or move to PostgreSQL:

```bash
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/tracelens
```

Also make sure the WAL files are writable and that only one process writes to
the database.

## Alembic and the ORM disagree

```bash
python -m alembic current                    # applied revision
python -m alembic upgrade head                # apply pending migrations
python -m alembic revision --autogenerate -m "describe the change"
```

For a local development database that you are willing to discard:

```bash
rm -f data/tracelens.db data/tracelens.db-wal data/tracelens.db-shm
python -m alembic upgrade head
```

## The AI assistant is not ready

`GET /api/v1/llm/status` reports `ready` and a `reason`:

| `reason` | Fix |
|---|---|
| `provider_credentials_or_base_url_missing` | Set the API key (or base URL) for the selected provider. |
| `model_not_selected` | Set the model in *Settings → AI provider*. |

The assistant is optional. Everything else works with `APP_LLM_PROVIDER=disabled`.

## The AI assistant returns "provider_empty_response"

The provider accepted the request but returned no assistant text. This happens
with very small local models that produce only a preamble, or with a prompt the
model cannot handle.

- Use a larger model.
- Ask a narrower question.
- Check the raw provider response with `curl` against your provider's API.

TraceLens reports this as a clean error and does not retry automatically, so a
failing provider cannot silently burn your budget.

## The AI assistant cannot reach a local Ollama server

- Server-side: `OLLAMA_BASE_URL=http://127.0.0.1:11434` works when the API runs
  on the host. From inside a container use
  `http://host.docker.internal:11434` (Compose already maps that host) and make
  sure Ollama listens on all interfaces: `OLLAMA_HOST=0.0.0.0 ollama serve`.
- Test the provider directly:

  ```bash
  curl http://127.0.0.1:11434/api/tags
  ```

## A custom provider URL is rejected

Custom provider URLs must use HTTPS, and they must not resolve to private,
loopback or reserved addresses unless the deployment explicitly opts in:

```bash
APP_LLM_ALLOW_PRIVATE_BASE_URL=true
```

Only enable this for a private network you control.

## "ai_daily_budget_reached"

The configured daily or monthly currency budget is exhausted. Raise
`APP_LLM_DAILY_BUDGET_USD` / `APP_LLM_MONTHLY_BUDGET_USD`, wait for the next
day, or use a local model. The spent amount is shown in *Settings* and in
`GET /api/v1/llm/status`.

## The dependency graph is empty

The graph is built from **declared** dependencies: `explicit_dependencies`, the
`depends_on` / `input_from` / `source_span_id` attributes, and shared
`tool_call_id` values. Parent/child relations are drawn as grey hierarchy edges
but are not treated as data dependencies.

If your producer does not record data dependencies, add them — for example
`"explicit_dependencies": ["<producer-span-id>"]` on the consuming span.

## The critical path is missing

The critical path is only computed when the dependency graph is acyclic and
contains at least one dependency edge. The reconstruction warnings list the
reason (a cycle, a broken reference). The panel caption also shows
`critical path unsupported`.

## A comparison reports a divergence between unrelated runs

Expected: the alignment is structural, and different tasks have different
actions. The comparison view labels a divergence as *observed*, not as
*erroneous*, and it never assumes task equivalence. Compare runs of the same
task for a meaningful result.

## Playwright cannot launch a browser

```bash
python -m playwright install --with-deps chromium
```

On a minimal Linux image the system libraries are missing; `--with-deps` installs
them. In a container, run the browser workflow on the host rather than inside
the container.

## Windows: the execution policy blocks the virtual environment

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Logs are not JSON

Set `LOG_JSON=false` for human-readable output, or `LOG_LEVEL=DEBUG` for more
detail. JSON is the default because it is what log shippers expect.

## Resetting the local state

```bash
# Stop the server first
rm -f data/tracelens.db data/tracelens.db-wal data/tracelens.db-shm data/.secret_key
python -m alembic upgrade head
uvicorn app.main:app --port 8000
```

This deletes every stored trace, annotation, experiment and audit event, and
invalidates any stored provider credentials (they were encrypted with the old
secret key).
