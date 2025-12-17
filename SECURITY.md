# Security policy

## Supported deployment modes

TraceLens supports two deployment modes.

**Local single-user mode** (no `APP_AUTH_TOKEN`): the default for a developer
laptop. There is **no access control** — anyone who can reach the port can read
every stored trace and use every configured provider. Bind to `127.0.0.1` and
do not expose it to an untrusted network.

**Authenticated multi-user mode** (`APP_AUTH_TOKEN` set, workspace-scoped API
keys): intended for a shared or hosted deployment. Set a long random token, put
it behind HTTPS at a trusted reverse proxy, restrict access to the database
volume, and set `APP_SECRET_KEY` so stored provider credentials are decryptable
after a restore.

## Reporting a vulnerability

Please do not open a public issue containing exploitable details. Use the
repository's private security advisory channel. Include a reproducible report,
the affected version, the impact and a suggested mitigation where possible. Do
not include real traces, credentials, personal data or production endpoints.

You can expect an acknowledgement within 7 days and a status update within 30
days of a confirmed report.

## Data handling notes

- Imported JSON is treated as data only. The project does not deserialize
  pickles, import uploaded code or execute trace content. The counterfactual
  sandbox runs a fixed allow-list of pure functions and abstains on anything
  else.
- The canonical trace and the original JSON are stored in the configured
  database. Common secret keys and bearer strings are masked in API responses
  and exports by default; the masking is best-effort pattern redaction and is
  not a substitute for minimizing the data you import.
- Disabling response redaction requires an explicit confirmation and is
  audited.
- The API has bounded request sizes, per-run and per-batch span caps,
  pagination, a per-client rate limiter, optional bearer authentication and a
  restrictive same-origin CSP.
- Provider credentials entered in the UI are encrypted with AES-GCM before
  storage and are write-only. API keys are stored as SHA-256 hashes. Neither
  is returned by the API, written to the logs, or included in exports.
- Optional external AI providers are disabled by default. Per-request consent is
  required; only a bounded, best-effort-redacted evidence subset is sent —
  never the original payload. Requests are bounded by timeout, response size,
  request rate, daily request count and daily/monthly currency budgets.
- The provider adapters have been exercised against a local Ollama server and
  against recorded HTTP fixtures; they have not been verified against live
  paid accounts.

See [`docs/SECURITY.md`](docs/SECURITY.md) for the full threat model and the
production deployment checklist.
