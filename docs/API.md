# REST API

Base URL: `http://127.0.0.1:8000` (local default).
Interactive documentation: `/docs` (Swagger UI) and `/redoc`.
Machine-readable schema: `/openapi.json`.

All endpoints are versioned under `/api/v1` (plus the OTLP-standard
`/v1/traces`). Requests and responses are JSON unless stated otherwise.

## Authentication

| Mode | Configuration | Behaviour |
|---|---|---|
| Local single user | `APP_AUTH_TOKEN` unset | No token required; every request maps to the `default` workspace. |
| Authenticated | `APP_AUTH_TOKEN` set | `Authorization: Bearer <token>` required on `/api/v1/*` (except health and readiness). The deployment token maps to the `default` workspace. |

Workspace-scoped API keys can be created by an administrator
(`POST /api/v1/workspaces/{slug}/keys`). The raw key is returned **once**; only
its SHA-256 hash is stored.

## Error format

Every error uses the same envelope:

```json
{
  "error": {
    "code": "trace_validation_failed",
    "message": "Record 1: spans must be a JSON array."
  },
  "request_id": "3f9c0b7e1c2d4a5b"
}
```

Validation failures never echo the submitted values, because those values may
contain secrets or private agent content. `request_id` also appears in the
structured logs and in the `X-Request-ID` response header.

| Code | Status | Meaning |
|---|---|---|
| `unauthorized` | 401 | Missing or invalid bearer token |
| `forbidden` | 403 | The principal may not perform this action |
| `run_not_found` | 404 | Unknown run id in this workspace |
| `trace_validation_failed` | 422 | The payload is not a valid trace |
| `request_validation_failed` | 422 | The request body does not match the schema |
| `span_limit_exceeded` | 422 | The run exceeds `MAX_SPANS_PER_RUN` |
| `batch_too_large` | 413 | Batch exceeds the record or span cap |
| `payload_too_large` | 413 | The request exceeds the upload limit |
| `rate_limited` | 429 | Per-client request limit reached |
| `ai_daily_limit_reached` | 429 | Daily AI request limit reached |
| `ai_daily_budget_reached` | 429 | Daily AI currency budget reached |
| `ai_monthly_budget_reached` | 429 | Monthly AI currency budget reached |
| `ai_rate_limited` | 429 | Too many AI requests in the last minute |
| `external_transfer_consent_required` | 400 | The AI call was not explicitly authorized |
| `ai_not_ready` | 503 | No provider credential/model configured |
| `confirmation_required` | 400 | A destructive or side-effecting action needs an explicit flag |

---

## Operations

### `GET /api/v1/health`

Liveness probe. Never requires authentication.

```json
{ "status": "ok", "app": "TraceLens", "version": "1.0.0", "mode": "local-single-workspace", "ai_enabled": false }
```

### `GET /api/v1/ready`

Readiness probe: verifies that the database is reachable.

```json
{ "status": "ready", "database": "ok", "app_version": "1.0.0" }
```

---

## Traces

### `GET /api/v1/runs`

Filterable, paginated run index.

| Parameter | Type | Notes |
|---|---|---|
| `query` | string | Matches task, run id, trace id or task id |
| `status` | string | `success`, `failure`, `partial`, `running`, `unknown` |
| `provider` | string | Substring match |
| `task` | string | Substring match |
| `model` | string | Substring match |
| `agent` | string | Substring match |
| `min_duration_ms` / `max_duration_ms` | number | Duration window |
| `limit` | int (1–200) | Default 50 |
| `offset` | int | Default 0 |

```bash
curl 'http://127.0.0.1:8000/api/v1/runs?status=failure&limit=10'
```

```json
{
  "items": [ { "run_id": "demo-orders-invalid-args", "task": "Summarize orders…", "status": "failure", "...": "..." } ],
  "total": 1,
  "limit": 10,
  "offset": 0,
  "workspace_id": "default"
}
```

### `GET /api/v1/runs/{run_id}`

The full run record: metadata, the canonical trace, annotations, experiments,
findings and hypotheses.

### `GET /api/v1/runs/{run_id}/timeline`

Flat, chronologically ordered span list for the timeline view.

### `GET /api/v1/runs/{run_id}/graph`

The dependency reconstruction: nodes, typed edges, chronological and logical
order, warnings, and the critical path (when supported).

```json
{
  "nodes": [ { "span_id": "bad-q1", "name": "Search orders", "action_type": "tool", "status": "error", "...": "..." } ],
  "edges": [ { "source": "bad-q1", "target": "bad-s1", "kind": "explicit_dependency", "dependency": true, "evidence": "Producer-declared depends_on/input reference." } ],
  "chronological_order": ["bad-p1", "bad-q1", "bad-s1", "bad-f1"],
  "logical_order": ["bad-p1", "bad-q1", "bad-s1", "bad-f1"],
  "warnings": [],
  "critical_path": { "span_ids": ["bad-p1", "bad-q1"], "duration_ms": 98.0, "basis": "…" },
  "critical_path_supported": true
}
```

### `DELETE /api/v1/runs/{run_id}?confirm=true`

Deletes the run, its spans, findings, hypotheses, annotations and experiments.
`confirm=true` is required.

### `GET /api/v1/runs/{run_id}/report?format=markdown|json|html|pdf`

Investigation report including identifiers, findings, evidence, uncertainty,
the timeline, the dependency graph and reproducibility information. `pdf`
returns `application/pdf`.

### `POST /api/v1/runs/{run_id}/annotations`

```json
{ "author": "reviewer", "note": "Confirmed against the tool schema.", "finding_id": "bad-q1:invalid_tool_arguments", "disposition": "confirmed" }
```

Annotations are append-only and never modify the original trace.

---

## Analysis

### `GET /api/v1/runs/{run_id}/analysis`

Optional `reference_run_id` adds a comparison against a reference execution.

```json
{
  "run_id": "demo-orders-invalid-args",
  "trace_id": "trace-demo-orders-invalid-args",
  "rules_version": "rules-1.1.0",
  "summary": "Run failed. Earliest recorded error is invalid_tool_arguments at span bad-q1…",
  "earliest_observed_error": {
    "span_id": "bad-q1",
    "category": "invalid_tool_arguments",
    "status": "Observed",
    "finding_id": "bad-q1:invalid_tool_arguments",
    "rule_id": "invalid_tool_arguments.v1",
    "timestamp": "2026-10-17T09:00:00.070000+00:00",
    "note": "The earliest explicitly recorded error. It marks the first observed anomaly, not necessarily the root cause of the run outcome."
  },
  "earliest_observed_divergence": null,
  "earliest_plausible_failure_propagation_point": {
    "span_id": "bad-q1",
    "affected_span_ids": ["bad-s1", "bad-f1"],
    "status": "Inferred Hypothesis",
    "rule_id": "dependency.failure_propagation.v1",
    "confidence": 0.7,
    "note": "Earliest point where a recorded failure is connected to downstream symptoms by a declared dependency…"
  },
  "findings": [
    {
      "finding_id": "bad-q1:invalid_tool_arguments",
      "rule_id": "invalid_tool_arguments.v1",
      "category": "invalid_tool_arguments",
      "status": "Observed",
      "confidence": 0.94,
      "span_ids": ["bad-q1"],
      "evidence": [ { "span_id": "bad-q1", "field": "exception/status/attributes", "value": "Invalid arguments: expected ISO date for 'from' and 'to'…" } ],
      "supporting_evidence": ["…"],
      "contradicting_evidence": [],
      "limitations": ["A single span can be incomplete or mis-instrumented."],
      "suggested_investigation": "Compare the serialized arguments with the tool's validated schema…"
    }
  ],
  "root_cause_hypotheses": [
    {
      "hypothesis_id": "hypothesis:bad-q1:invalid_tool_arguments",
      "title": "Invalid tool arguments at Search orders",
      "category": "invalid_tool_arguments",
      "status": "Inferred Hypothesis",
      "confidence": 0.9,
      "primary_span_id": "bad-q1",
      "span_ids": ["bad-q1", "bad-s1", "bad-f1"],
      "supporting_evidence": ["…"],
      "contradicting_evidence": [],
      "missing_information": [],
      "limitations": ["Association/dependency evidence does not prove that changing this action changes the final outcome."],
      "suggested_experiment": "Compare the serialized arguments with the tool's validated schema…"
    }
  ],
  "graph": { "...": "same shape as /graph" },
  "impact": { "bad-q1": ["bad-s1", "bad-f1"] },
  "categories": ["planning_or_orchestration_error", "…"],
  "evidence_statuses": ["Observed", "Inferred Hypothesis", "…"],
  "missing_information": ["Evaluator output or rubric: …"],
  "suggested_experiments": [ { "hypothesis_id": "…", "proposal": "…", "safety": "…" } ],
  "evidence_policy": "Observed means present in the imported record…"
}
```

### `POST /api/v1/compare`

```json
{ "run_ids": ["demo-orders-reference", "demo-orders-invalid-args"] }
```

2–8 run ids. The first is the alignment baseline. Returns the aligned
actions, field-level diffs, the first divergence, a topology-change flag and
the metric deltas.

### `GET /api/v1/tool-analytics`

Aggregated tool statistics: calls, errors, timeouts, recovery rate, error
types, latency percentiles and outliers.

### `GET /api/v1/overview`

Workspace metrics: totals, success rate, latency percentiles, token volume,
recorded cost, failure-category counts, the latency series and the recent runs.

---

## Ingestion

### `POST /api/v1/ingest`

Body: a canonical trace object, an array of traces, or `{"traces": [...]}`.
Set the `X-Filename` header to influence format detection for JSONL content.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  --data @examples/traces/demo-orders-invalid-args.json
```

```json
{
  "accepted": 1,
  "inserted": ["demo-orders-invalid-args"],
  "updated": [],
  "workspace_id": "default",
  "warnings": [ { "run_id": "demo-orders-invalid-args", "warnings": [] } ]
}
```

Re-ingesting the same `run_id` is idempotent: the run is updated, not
duplicated, and the event is audited as `trace.reingested`.

### `POST /api/v1/ingest/file`

Multipart upload with a `file` field (`.json` or `.jsonl`).

### `POST /api/v1/ingest/batch`

Validated batch ingestion. Each record is validated independently: valid
records are stored, invalid ones are reported.

```json
{
  "submitted": 2,
  "accepted": 1,
  "inserted": ["sample-run"],
  "updated": [],
  "rejected": [ { "index": 2, "code": "trace_validation_failed", "message": "spans must be a JSON array." } ],
  "warnings": []
}
```

### `POST /api/v1/validate`

Validates without storing. Returns the per-run summary and any warnings.

### `POST /v1/traces`

OTLP/HTTP JSON. `Content-Type: application/json` only; protobuf is not
implemented and returns `415`. Multi-trace payloads are split per trace id.

### `POST /api/v1/demo/seed`

Loads the deterministic example executions.

---

## Experiments

### `POST /api/v1/experiments/counterfactual`

```json
{
  "run_id": "demo-orders-invalid-args",
  "source_span_id": "bad-q1",
  "replacement_output": { "orders": [ { "id": "A1" } ] },
  "title": "Corrected fixture",
  "confirm_side_effect_free": true
}
```

`confirm_side_effect_free` is required. Only allow-listed deterministic
validators run.

```json
{
  "id": "…",
  "status": "Experimentally Supported",
  "decision": "declared_deterministic_downstream_checks_passed",
  "result": {
    "title": "Corrected fixture",
    "status": "Experimentally Supported",
    "scope": "This result applies only to allow-listed local deterministic checks and the supplied fixture…",
    "fixture_sha256": "…",
    "steps": [ { "span_id": "bad-s1", "action": "Summarize order totals", "adapter": "mock_required_fields", "outcome": "passed", "side_effects": false } ],
    "limitations": [ "No imported code, tool, network, filesystem, provider, or external side effect was executed." ],
    "reproducibility": { "sandbox_version": "counterfactual-sandbox-1", "max_steps": 100 }
  }
}
```

### `GET /api/v1/runs/{run_id}/experiments`

Recorded experiments for a run.

---

## Optional AI assistance

### `GET /api/v1/llm/status`

Provider configuration state, budgets, limits and the pricing reference.
Never contains a credential.

### `PUT /api/v1/llm/config`

```json
{ "provider": "openai", "model": "gpt-4.1-mini" }
```

### `POST /api/v1/llm/credentials`

Stores a credential encrypted. **Write-only** — the value is never returned.

```json
{ "provider": "openai", "api_key": "sk-…", "base_url": null, "model": "gpt-4.1-mini" }
```

### `DELETE /api/v1/llm/credentials/{provider}`

Removes the stored credential for that provider.

### `GET /api/v1/llm/models`

Model discovery through the configured provider, plus the models that have a
known reference price.

### `POST /api/v1/llm/test-connection`

Runs discovery and reports success, the model count and a sample.

### `POST /api/v1/llm/estimate`

Pre-flight cost estimate for a question, used for the confirmation step.

```json
{ "run_id": "…", "question": "What failed?" }
```

### `POST /api/v1/llm/assist`

```json
{ "run_id": "…", "question": "…", "authorize_external_transfer": true }
```

`authorize_external_transfer` is required. The response contains the advisory
answer, the evidence span ids, the citation check, the provider usage and the
estimated cost.

---

## Settings and administration

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/settings` | Deployment, redaction, limits, provider status |
| `PUT` | `/api/v1/settings/redaction` | Toggle payload redaction (`confirm_sensitive` required to disable) |
| `GET` | `/api/v1/audit` | Audit trail |
| `GET` | `/api/v1/workspaces` | List workspaces (administrator) |
| `POST` | `/api/v1/workspaces` | Create a workspace (administrator) |
| `POST` | `/api/v1/workspaces/{slug}/keys` | Create a workspace-scoped API key (administrator) |

---

## Limits

| Limit | Environment variable | Default |
|---|---|---|
| Upload size | `MAX_UPLOAD_BYTES` | 10 MiB |
| Spans per run | `MAX_SPANS_PER_RUN` | 20 000 |
| Records per batch | `MAX_BATCH_RECORDS` | 500 |
| Spans per batch | `MAX_BATCH_SPANS` | 100 000 |
| Requests per minute | `RATE_LIMIT_PER_MINUTE` | 240 |
| AI requests per day | `APP_LLM_MAX_DAILY_REQUESTS` | 20 |
| AI requests per minute | `APP_LLM_MAX_REQUESTS_PER_MINUTE` | 5 |
| AI daily budget | `APP_LLM_DAILY_BUDGET_USD` | 1.00 USD |
| AI monthly budget | `APP_LLM_MONTHLY_BUDGET_USD` | 10.00 USD |
