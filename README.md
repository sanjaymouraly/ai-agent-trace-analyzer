# AI Agent Execution Trace Analyzer & Root-Cause Debugger

Analyses AI agent execution traces to find failures, root causes, and recovery points.

## Features

- Imports agent execution traces from JSON/JSONL and reconstructs the full tool-call timeline.
- Builds a causal dependency graph instead of relying on log timestamps.
- Identifies the first failing action and the downstream steps damaged by it.
- Compares failing runs with successful runs to separate real defects from normal path variation.
- Classifies failures by tool, model, prompt, environment, and orchestration layer.
- Produces a root-cause report with evidence, affected steps, and suggested recovery points.

## Stack

Python, Node.js, FastAPI, Uvicorn, Pydantic, HTTPX, SQLAlchemy, React, TypeScript, Vite

## Clone

```bash
git clone https://github.com/sanjaymouraly/ai-agent-trace-analyzer.git
cd ai-agent-trace-analyzer
```

## Setup

```bash
# The project ships a Docker Compose setup.
docker compose up --build
```

See `docs/` or the compose file for environment variables and ports.

## Run

```bash
docker compose up --build
```

## Tests

```bash
pytest -q
```

## Notes

- Sample data, when included, is synthetic.

## License

MIT © 2025 [Sanjay Mouraly](https://github.com/sanjaymouraly)
