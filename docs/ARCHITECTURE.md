# Architecture

TraceLens is a single-purpose, modular monolith: one API process, one SPA, one
database. It is deliberately *not* a set of microservices — the analysis needs
the whole trace in memory, and splitting it would add network hops without
adding capability.

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Browser (React 19 + TypeScript + Vite + Tailwind)                          │
│  Overview · Trace explorer · Run investigation · Compare · Tool analytics   │
│  AI assistant · Settings · Audit log                                        │
│  React Flow (dependency graph) · Recharts (latency/tokens/categories)      │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ JSON over HTTP (same origin)
┌───────────────────────────────▼────────────────────────────────────────────┐
│  FastAPI application factory (app/main.py)                                  │
│  SecurityAndLimitMiddleware: auth · rate limit · body caps · safe headers   │
│  CORS · request-id · structured JSON logging · optional OpenTelemetry        │
└──┬─────────────┬─────────────┬──────────────┬──────────────┬───────────────┘
   │             │             │              │              │
┌──▼──────────┐ ┌▼──────────┐ ┌▼───────────┐ ┌▼────────────┐ ┌▼──────────────┐
│ app/api/*   │ │app/ingest-│ │app/analysis│ │app/compara- │ │ app/experi-   │
│ routers     │ │ion.py     │ │.py         │ │ sion.py     │ │ ments.py     │
│ traces      │ │ adapters  │ │ graph +    │ │ alignment   │ │ isolated      │
│ analysis    │ │ canonical │ │ rules +    │ │ divergence  │ │ fixture       │
│ ingestion   │ │ schema    │ │ evidence   │ │ metrics     │ │ sandbox      │
│ experiments │ │ redaction │ │ + ranking  │ │             │ │               │
│ ai          │ │           │ │            │ │             │ │               │
│ admin       │ └───────────┘ └────────────┘ └─────────────┘ └───────────────┘
└──┬─────────────┬─────────────┬──────────────┬──────────────┬───────────────┘
   │             │             │              │              │
┌──▼──────────┐ ┌▼─────────┐ ┌▼──────────┐ ┌▼──────────┐ ┌─▼──────────────┐
│ app/db.py   │ │app/llm.py│ │app/pric-  │ │app/reports│ │ app/crypto.py  │
│ SQLAlchemy  │ │ provider │ │ ing.py    │ │.py + pdf  │ │ AES-GCM       │
│ 2.0 +       │ │ abstract-│ │ reference │ │ JSON/MD/   │ │ secret storage │
│ Alembic     │ │ ion      │ │ list      │ │ HTML/PDF   │ │                │
└──┬──────────┘ │ consent  │ │ prices    │ └────────────┘ └────────────────┘
   │            │ budgets   │ └──────────┘
   │            └───────────┘
┌──▼──────────────┐   ┌──────────────────┐   ┌──────────────────────┐
│ SQLite (local)  │   │ PostgreSQL       │   │ Redis (optional)    │
│ or PostgreSQL   │   │ (production)     │   │ background queue    │
└─────────────────┘   └──────────────────┘   └──────────────────────┘
```

## Layers

### 1. Ingestion (`app/ingestion.py`, `app/otlp.py`, `app/schema.py`)

The only place that turns untrusted input into internal structures.

- **Canonical model** (`app/schema.py`): a versioned Pydantic model with
  validation of identity, unique span identifiers, timestamp parsing,
  non-negative cost and cross-run consistency.
- **Adapters**: canonical JSON, JSONL, batch envelopes, OTLP/HTTP JSON
  (`resourceSpans`), and LangChain / LangGraph run exports. Adapters are
  pure data transformations; no framework code is imported.
- **Redaction** (`app/ingestion.redact`): recursive, key-name and value-pattern
  based. Applied to anything that leaves the process.
- **Warnings and provenance**: every import records its mapping decision,
  schema version, warnings (missing timestamps, naive datetimes, skipped
  non-object rows) and the source format.
- **Limits**: upload bytes, spans per run, records per batch, total batch spans.

### 2. Storage (`app/models.py`, `app/db.py`, `alembic/`)

SQLAlchemy 2.0 ORM over a portable schema.

| Table | Purpose |
|---|---|
| `workspaces` | Isolation boundary; every other table carries `workspace_id` |
| `api_keys` | Hashed, workspace-scoped keys |
| `runs` | Execution metadata plus the canonical JSON and the original JSON |
| `spans` | Normalized span rows for database-side filtering and analytics |
| `findings` / `hypotheses` | Deterministic analysis output, materialized at import |
| `annotations` | Append-only reviewer notes |
| `experiments` | Controlled experiment results |
| `audit_events` | Import / change / AI / experiment trail |
| `app_settings` | Per-workspace settings (provider selection, redaction flag) |
| `stored_secrets` | AES-GCM encrypted provider credentials |
| `ai_usage_events` | Token and cost ledger for budget enforcement |

The canonical JSON is stored verbatim so the analysis can always be re-run
against exactly what was imported. The relational columns exist so that
filtering, pagination and analytics happen in the database.

### 3. Reconstruction and classification (`app/analysis.py`)

**Graph reconstruction** produces nodes, typed edges, a chronological order, a
logical (topological) order and warnings.

- *Edges are typed.* `parent_child` is hierarchy, `explicit_dependency` is a
  producer-declared data dependency, `tool_call_reference` links a model call
  to the tool invocation that carries the same call id, `span_link` is an
  OpenTelemetry association.
- *Only dependency edges drive impact analysis.* Chronological precedence is
  never treated as causation.
- *Cycles are handled.* A topological order is computed with a heap-based
  Kahn algorithm; if the graph is cyclic the component is reported and the
  chronological order is used **for display only**, with an explicit warning.
- *The critical path* is the longest path over dependency edges using the
  recorded durations, and is only reported when the graph supports it.

**Failure classification** is a deterministic rule set over a stable category
vocabulary (`app.analysis.FAILURE_CATEGORIES`). Rules distinguish:

- *observed* errors (an exception, an error status, a producer-supplied
  invalid-state flag) from *inferred* problems (an expected tool that differs
  from the called tool, a retrieved document that differs from the labelled
  relevant one);
- a *recovered* failure from a root cause (a later equivalent attempt that
  succeeded is recorded as contradicting evidence and lowers the ranking);
- a *successful* tool response from a *correct* one (no rule claims correctness
  from a non-error response).

Run-level rules add retry-loop detection, a missing final answer, a rejected
final answer, and an explicit abstention when a run failed but nothing inside
it explains why.

**Ranking** is a heuristic score, not a probability: observed beats inferred,
upstream anomalies with dependency-connected downstream symptoms outrank
terminal symptoms, and a successful recovery subtracts from the score. The
per-hypothesis `limitations` field always states that association is not proof.

### 4. Comparison (`app/comparison.py`)

Deterministic, retry-aware alignment with `difflib.SequenceMatcher` over
normalized action identity (action type + tool name, with retry suffixes
stripped). Retry occurrences are preserved as separate actions. The result
contains the aligned rows, field-level diffs, the first divergence, a
topology-change flag, and quantitative deltas. No LLM is involved.

### 5. Controlled experiments (`app/experiments.py`)

A deliberately small sandbox:

- only spans that declare an allow-listed `replay_adapter`
  (`mock_required_fields`, `mock_type_check`, `mock_copy`) are executed;
- the fixture replaces one span's output and the deterministic validators run
  over the dependency descendants;
- every other action is abstained from and reported with a reason;
- the result is labelled *Experimentally Supported*, *Experimentally Refuted*
  or *Insufficient Evidence*, always with the fixture hash, the sandbox
  version, and the limitation that this proves the tested invariant only.

There is no path from an uploaded trace to a network call, a shell, a file
write or a provider request.

### 6. Optional AI (`app/llm.py`, `app/pricing.py`)

A provider abstraction with a uniform shape:

```
ProviderConfig(key, protocol, base_url, api_key, model, configured)
```

- `openai`, `openrouter` and `openai-compatible` speak the OpenAI chat
  completions protocol; `anthropic` speaks the Messages protocol; `ollama`
  speaks the native chat API.
- Credentials come from environment variables or from AES-GCM encrypted
  storage; the browser never sees them.
- Outbound requests are bounded: fixed timeout, 1 MiB response cap, no
  redirects, HTTPS-only custom URLs, and rejection of private/loopback
  addresses unless the deployment opts in.
- Every call needs per-request consent, passes request-rate and currency
  budget checks, is written to `ai_usage_events`, and is audited.
- The evidence packet is a bounded, redacted subset of the trace. Model output
  is checked for span-ID citations and is never merged into the trace.

`app/pricing.py` holds the reference list prices retrieved from the
providers' own pricing pages, with the retrieval date and source URLs. An
unknown model returns `estimated_cost_usd = None` rather than a guess.

### 7. API (`app/api/`)

Routers are thin: validate, call a domain function, shape the response.

- `traces.py` — run index, detail, timeline, graph, annotations, report export
- `analysis.py` — overview, run analysis, comparison, tool analytics
- `ingestion.py` — ingest, batch, validate, OTLP, demo seed
- `experiments.py` — controlled fixture checks
- `ai.py` — provider status, configuration, credentials, discovery, estimate, assist
- `admin.py` — settings, redaction, audit, workspaces, API keys, health

Dependencies (`app/api/deps.py`) resolve the caller into a `Principal` and a
workspace id. Workspace scoping is applied in the data layer.

### 8. Frontend (`frontend/`)

React 19 + TypeScript + Vite + Tailwind. No global state library: a small
`useAsync` hook plus local component state is enough and keeps the data flow
explicit.

- `lib/api.ts` — fetch wrapper, bearer token in `sessionStorage` only
- `lib/types.ts` — the API contract as TypeScript types
- `components/graph/DependencyGraph.tsx` — React Flow, layered layout computed
  from dependency edges
- `components/timeline/ExecutionTimeline.tsx` — recorded start/end windows
- `pages/*` — one file per screen

The production build is served by FastAPI from the same origin, so a default
deployment needs no CORS configuration.

## Background processing

Redis is optional. `REDIS_URL` enables a Redis-backed queue; otherwise an
in-process asyncio worker with a bounded queue is used. The work items are
deliberately small and idempotent: post-import analysis materialization,
retention cleanup, and demo seeding. The retention sweeper also runs at
startup and hourly in-process, so a single-process deployment needs no queue
at all.

## Observability

`app/observability.py` installs a JSON log formatter (one object per line)
and, when `OTEL_EXPORTER_OTLP_ENDPOINT` is set and the OpenTelemetry packages
are installed, instruments the FastAPI app. Missing optional packages degrade
to plain logging; the application never fails to start because of them.

## Security model

| Threat | Control |
|---|---|
| Uploaded content executed | No code path evaluates imported data; the sandbox is a fixed allow-list of pure functions |
| Cross-tenant reads | `workspace_id` is applied in every query, not only in routes |
| Credential leakage | Env vars or AES-GCM encrypted storage; API keys hashed; responses never echo secrets |
| SSRF | HTTPS-only custom URLs, DNS resolution check, private/loopback rejection, no redirects |
| Prompt injection via trace content | Trace strings are sent inside a JSON packet with an explicit "data, not instructions" policy; the model is instructed never to invent events |
| Oversized payloads | Upload caps, per-run span caps, batch caps, 1 MiB provider response cap |
| Cost runaway | Per-minute request limit, daily request limit, daily and monthly currency budgets, pre-flight estimate and explicit consent |
| Silent tampering | Original JSON preserved; annotations are append-only; every change is audited |

## Extension points

- **New failure category**: add it to `FAILURE_CATEGORIES` and to the rule
  table in `app/analysis.py`. The API, UI and evaluation harness pick it up.
- **New trace format**: add an adapter in `app/ingestion.py` and return the
  canonical mapping. `app/otlp.py` is a good template.
- **New AI provider**: add a `ProviderSpec` in `app/llm.py` with its protocol
  and route. The OpenAI-compatible protocol already covers most gateways.
- **New replay validator**: add the adapter name to `SAFE_ADAPTERS` and
  implement it in `app/experiments.py`. It must be a pure function of its
  inputs.
