# Security

This document describes the security model of TraceLens, the controls that
implement it, and the limitations a reader should be aware of before deploying
it with sensitive data.

## Threat model

TraceLens ingests **untrusted** execution traces and, optionally, forwards a
**redacted subset** of them to an external AI provider. The relevant threats
are:

1. Code execution through imported content.
2. Cross-tenant data access.
3. Credential disclosure (to the browser, to logs, to exports, to an AI
   provider).
4. Server-side request forgery through a configurable provider URL.
5. Resource exhaustion through oversized payloads or runaway AI calls.
6. Silent tampering with evidence.
7. Prompt injection through trace content reaching a language model.

## Controls

### 1. Imported content is data, never code

- No `eval`, `exec`, `importlib`, `pickle` or `yaml.load` on imported data.
- The only "execution" in the product is the counterfactual sandbox, which
  runs a **fixed allow-list of pure functions** (`mock_required_fields`,
  `mock_type_check`, `mock_copy`) over a recorded fixture. It has no access to
  the network, the filesystem, the environment or a provider.
- A regression test asserts that a payload containing a shell command is
  stored verbatim and never executed.

### 2. Workspace isolation

- Every data row carries a `workspace_id`.
- Isolation is applied in the data-access layer (`app/db.py`), not only in the
  route handlers, so a new endpoint cannot accidentally bypass it.
- API keys are scoped to a workspace; the deployment token maps to the
  `default` workspace only.
- A regression test asserts that a key for workspace B cannot read or write
  workspace A's runs.

### 3. Credential handling

| Credential | Storage | Exposure |
|---|---|---|
| Provider API keys entered in the UI | AES-GCM encrypted in `stored_secrets` | Write-only. Never returned by any endpoint, never in logs, never in exports. |
| Provider API keys from the environment | Process environment | Not logged, not serialized, not returned. |
| TraceLens API keys | SHA-256 hash in `api_keys` | Shown exactly once at creation. |
| `APP_SECRET_KEY` | Environment / generated 0600 file | Needed to decrypt stored credentials after a restore. |

- The encryption key is derived from `APP_SECRET_KEY` with SHA-256; set it
  explicitly in production.
- The dashboard bundle contains no credential, and the bearer token is kept in
  `sessionStorage` (tab-scoped) rather than `localStorage`.
- The API's public status endpoint reports only *whether* a credential is
  present, never any part of it.

### 4. Redaction

`app/ingestion.redact` replaces the values of sensitive keys (`api_key`,
`authorization`, `access_token`, `password`, `secret`, `cookie`,
`private_key`, …) and masks credential-shaped values (`Bearer …`,
`api_key=…`, `sk-…`, `AKIA…`) throughout imported payloads, API responses and
exports.

**This is best-effort pattern redaction, not a data-loss-prevention system.**
It cannot recognize every secret, and it does not scan free-form prose. Keep
it enabled, but do not treat it as a guarantee. Redaction can be turned off in
*Settings*, which requires an explicit confirmation and is audited.

### 5. SSRF protection for provider URLs

- Custom provider URLs must be `https` (except a local Ollama endpoint).
- URLs with embedded credentials, query strings or fragments are rejected.
- The hostname is resolved and every address must be globally routable;
  private, loopback and reserved ranges are blocked.
- Redirects are not followed.
- Outbound requests use `trust_env=False` (no proxy environment inheritance),
  a fixed timeout and a 1 MiB response cap.
- Private endpoints require an explicit, server-side
  `APP_LLM_ALLOW_PRIVATE_BASE_URL=true` opt-in.

### 6. Resource limits

Upload bytes, spans per run, records per batch, spans per batch, requests per
minute, AI requests per minute, AI requests per day, AI daily and monthly
currency budgets, comparison span cap, experiment step cap, provider response
cap. Every limit has a test.

### 7. Evidence integrity

- The original imported payload is stored verbatim next to the normalized
  representation.
- Annotations are append-only and never modify the trace.
- Every import, re-import, deletion, annotation, experiment, AI call and
  configuration change is written to `audit_events` with an actor and a
  timestamp.
- The report export includes the rules version, the schema version, the import
  warnings and the experiment status so a reader can tell how a conclusion was
  produced.

### 8. Prompt injection

Trace strings are attacker-controlled in the general case. When AI assistance
is used:

- only a **bounded, redacted subset** of the trace is sent — never the
  original payload, never the whole trace;
- the packet is JSON with an explicit policy line stating that trace strings
  are untrusted data and never instructions;
- the system prompt forbids inventing events, requires span-id citations and
  separates observation from inference;
- the answer is checked against the supplied evidence span ids and
  unrecognised citations are surfaced to the user;
- the answer is never written back into the trace or the findings.

Residual risk: a sufficiently capable model can still be steered by crafted
trace content. The mitigations reduce the impact (bounded, redacted data;
advisory-only output; citation checking) but do not eliminate it. Treat AI
assistance output as untrusted.

### 9. Browser and transport hardening

Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy:
no-referrer`, `X-Frame-Options: SAMEORIGIN` and a restrictive
`Content-Security-Policy` (`default-src 'self'`, no inline scripts, no
third-party origins). CORS is opt-in and exact-origin.

## Local mode versus authenticated multi-user mode

| | Local single user | Authenticated multi-user |
|---|---|---|
| `APP_AUTH_TOKEN` | unset | set |
| Identity | none; every request is `local-user` | bearer token or workspace key |
| Workspace isolation | single `default` workspace | per-workspace keys |
| Audit actor | `local-user` | `deployment-token` / `api-key` / named reviewer |
| Suitable for | a laptop | a shared or hosted deployment |

**Local mode has no access control at all.** Anyone who can reach the port can
read every stored trace, delete runs, change the redaction setting and use any
configured AI provider. Bind to `127.0.0.1` in local mode. Set
`APP_AUTH_TOKEN` for anything else.

## Deployment checklist

- [ ] `APP_AUTH_TOKEN` set to a long random value.
- [ ] `APP_SECRET_KEY` set to a long random value and stored in a secret manager.
- [ ] `ENVIRONMENT=production`.
- [ ] `DEFAULT_REDACTION=true` and redaction left enabled.
- [ ] `SEED_DEMO=false` (demo traces are synthetic but still noise).
- [ ] TLS terminated in front of the app; `CORS_ORIGINS` set to exact origins.
- [ ] PostgreSQL instead of SQLite for a shared deployment.
- [ ] `TRACE_RETENTION_DAYS` set to a retention period you are happy with.
- [ ] `RATE_LIMIT_PER_MINUTE` reviewed against your traffic.
- [ ] `APP_LLM_DAILY_BUDGET_USD` / `APP_LLM_MONTHLY_BUDGET_USD` set.
- [ ] Backups tested, and the secret key is stored **outside** the backup.
- [ ] Access logs shipped somewhere durable.

## Reporting a vulnerability

Please report security issues privately through the repository's security
advisory page rather than in a public issue. See [../SECURITY.md](../SECURITY.md)
for the response-time expectations.
