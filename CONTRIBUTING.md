# Contributing

Thanks for helping improve TraceLens. The project favours evidence-backed
behaviour, small dependencies and explicit limitations over broad but unverified
integration claims.

## Local setup

1. Use Python 3.10 or newer and Node.js 20 or newer.
2. Create a virtual environment, then install the hash-locked dev set and the
   editable project:

   ```bash
   python -m pip install --require-hashes -r requirements-dev.lock
   python -m pip install --no-deps -e .
   ```

   `requirements-dev.txt` is the human-editable range file; use
   `requirements-dev.lock` for reproducible installs.
3. Install the frontend dependencies and build the dashboard:

   ```bash
   npm --prefix frontend ci
   npm --prefix frontend run build
   ```

4. Run `python -m examples.generate` to refresh the deterministic JSON fixtures.

## Checks before submitting

```bash
# Backend
python -m pytest -q
ruff check app agent_debugger examples evals tests scripts
ruff format --check app agent_debugger examples evals tests scripts
mypy app agent_debugger --ignore-missing-imports
bandit -r app agent_debugger scripts -ll -q
pip-audit -r requirements.lock

# Evaluation harness
python -m evals.benchmark

# Frontend
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build

# Browser workflow (starts the real server and drives it with Playwright)
python -m playwright install chromium
python scripts/run_e2e.py
```

## Refreshing dependency locks

`requirements.txt` and `requirements-dev.txt` hold the human-edited ranges.
Regenerate the hash-pinned locks with `pip-tools`:

```bash
pip install pip-tools
python -m piptools compile --generate-hashes --output-file=requirements.lock requirements.txt
python -m piptools compile --generate-hashes --output-file=requirements-dev.lock requirements-dev.txt
```

## Database migrations

The schema is managed by Alembic.

```bash
python -m alembic revision --autogenerate -m "describe the change"
python -m alembic upgrade head
```

Migrations must work on both SQLite and PostgreSQL. Use batch mode
(`render_as_batch`) for SQLite, which is already configured in `alembic/env.py`.

## Change expectations

**Evidence discipline**

- Every finding and hypothesis must carry an explicit evidence label:
  `Observed`, `Inferred Hypothesis`, `Experimentally Supported`,
  `Experimentally Refuted`, `Confirmed Under Specified Test Conditions` or
  `Insufficient Evidence`.
- Never claim causality from timing, correlation, or a model's narration.
- A successful tool response is not evidence of correctness.
- A tool error that a later retry recovered from is not automatically a root
  cause — record it as contradicting evidence.
- When the evidence is insufficient, abstain and say so.
- Keep timestamps separate from dependency edges. Chronological order is never
  a causal claim.

**Data handling**

- Preserve the original trace payload and add normalized mapping warnings
  where the interpretation is lossy.
- Never execute uploaded content. New replay adapters must be small,
  deterministic, allow-listed and free of side effects.
- Never log, export or return credentials, tokens or secrets.

**AI providers**

A new provider must ship with server-side secret handling, SSRF controls,
explicit consent for trace-content transfer, request and currency limits, span
citation checking, and offline tests. The deterministic engine must keep
working with every provider disabled.

**Scope of claims**

- Document the integration you actually implemented and tested. Do not claim
  native support for a framework or provider that has not been exercised.
- A synthetic benchmark improvement is not a claim of real-world accuracy.
  Report the measured numbers and the fixture limitations.

**Repository hygiene**

- Do not commit trace dumps, runtime databases, credentials, browser binaries,
  virtual environments, build artifacts or screenshots that were not captured
  from the running application.
- Update `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md` and the
  README when public behaviour changes.
- Regenerate the screenshots with `python scripts/capture_screenshots.py`
  after a visible UI change.

## Pull requests

Include the problem, the design, compatibility notes, the exact test commands
you ran with their results, and any path you deliberately left unsupported.
