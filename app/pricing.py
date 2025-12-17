"""Model pricing configuration used for cost visibility and budget enforcement.

The values below are **reference list prices published by the providers** and
were retrieved from the official pricing pages on the date recorded in
``as_of``. Provider prices change; treat these as a starting point, verify
them against your own provider contract, and override them with
``LLM_PRICING_OVERRIDES`` (JSON) or a pricing file when your rates differ.

Nothing in this module is invented: an unknown model returns a cost estimate
of ``None`` rather than a made-up number.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Date on which the reference prices below were retrieved from the providers.
AS_OF = "2026-10-19"

OPENAI_SOURCE = "https://platform.openai.com/docs/pricing"
ANTHROPIC_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
OPENROUTER_SOURCE = "https://openrouter.ai/models"


@dataclass(frozen=True)
class Price:
    """USD price per one million tokens."""

    input_per_million: float
    output_per_million: float
    source: str
    as_of: str = AS_OF

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1_000_000) * self.input_per_million + (output_tokens / 1_000_000) * self.output_per_million,
            8,
        )


#: OpenAI standard (non-batch) list prices, USD per 1M tokens.
OPENAI_PRICES: dict[str, Price] = {
    name: Price(value[0], value[1], OPENAI_SOURCE)
    for name, value in {
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "gpt-4-turbo": (10.00, 30.00),
        "gpt-3.5-turbo": (0.50, 1.50),
        "gpt-5": (1.25, 10.00),
        "gpt-5-mini": (0.25, 2.00),
        "gpt-5-nano": (0.05, 0.40),
        "gpt-5.1": (1.25, 10.00),
        "gpt-5.2": (1.75, 14.00),
        "gpt-5.3-codex": (1.75, 14.00),
        "gpt-5.4": (2.50, 15.00),
        "gpt-5.4-mini": (0.75, 4.50),
        "gpt-5.4-nano": (0.20, 1.25),
        "gpt-5.5": (5.00, 30.00),
        "gpt-5.6-luna": (0.20, 1.20),
        "gpt-5.6-terra": (2.00, 12.00),
        "gpt-5.6-sol": (4.00, 20.00),
        "gpt-6-luna": (0.10, 0.50),
        "gpt-6-sol": (2.00, 10.00),
        "gpt-6-astra": (10.00, 50.00),
        "gpt-6.1-sol": (2.00, 10.00),
        "o1": (15.00, 60.00),
        "o3": (2.00, 8.00),
        "o3-mini": (1.10, 4.40),
        "o4-mini": (1.10, 4.40),
        "chat-latest": (5.00, 30.00),
    }.items()
}

#: Anthropic list prices, USD per 1M tokens.
ANTHROPIC_PRICES: dict[str, Price] = {
    name: Price(value[0], value[1], ANTHROPIC_SOURCE)
    for name, value in {
        "claude-opus-5.5": (4.00, 20.00),
        "claude-opus-5": (5.00, 25.00),
        "claude-opus-4.8": (5.00, 25.00),
        "claude-opus-4.7": (5.00, 25.00),
        "claude-opus-4.6": (5.00, 25.00),
        "claude-opus-4.5": (5.00, 25.00),
        "claude-sonnet-5.5": (2.00, 10.00),
        "claude-sonnet-5": (2.00, 10.00),
        "claude-sonnet-4.6": (3.00, 15.00),
        "claude-sonnet-4.5": (3.00, 15.00),
        "claude-haiku-5.5": (0.10, 0.50),
        "claude-haiku-4.5": (1.00, 5.00),
        "claude-fable-5.1": (10.00, 50.00),
        "claude-fable-5": (10.00, 50.00),
    }.items()
}

#: Local inference: no per-token API charge. Compute cost is borne locally.
LOCAL_PRICE = Price(0.0, 0.0, "local execution (no per-token provider charge)")

PROVIDER_TABLES: dict[str, dict[str, Price]] = {
    "openai": OPENAI_PRICES,
    "anthropic": ANTHROPIC_PRICES,
    "openrouter": {},
    "ollama": {},
    "openai-compatible": {},
}


def _overrides() -> dict[str, Price]:
    raw = os.getenv("LLM_PRICING_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    result: dict[str, Price] = {}
    if not isinstance(data, dict):
        return result
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        try:
            result[str(key)] = Price(
                float(value["input_per_million"]),
                float(value["output_per_million"]),
                str(value.get("source", "operator override (LLM_PRICING_OVERRIDES)")),
                str(value.get("as_of", AS_OF)),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _file_prices() -> dict[str, Price]:
    path = os.getenv("LLM_PRICING_FILE", "").strip()
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, Price] = {}
    if not isinstance(data, dict):
        return result
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        try:
            result[str(key)] = Price(
                float(value["input_per_million"]),
                float(value["output_per_million"]),
                str(value.get("source", f"operator pricing file {file_path.name}")),
                str(value.get("as_of", AS_OF)),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _normalize(model: str) -> str:
    return model.strip().lower().replace("_", "-")


def lookup_price(provider: str, model: str) -> Price | None:
    """Return the reference price for a model, or ``None`` when it is unknown."""
    if not model:
        return None
    overrides = _overrides()
    files = _file_prices()
    for candidate in (f"{provider}:{model}", model):
        if candidate in overrides:
            return overrides[candidate]
        if candidate in files:
            return files[candidate]
    if provider == "ollama":
        return LOCAL_PRICE
    table = PROVIDER_TABLES.get(provider, {})
    normalized = _normalize(model)
    # Exact match first, then longest-prefix match (e.g. "gpt-4o-2024-05-13").
    if normalized in table:
        return table[normalized]
    matches = [name for name in table if normalized.startswith(name)]
    if matches:
        return table[max(matches, key=len)]
    return None


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    """Estimate the USD cost of a request.

    Returns ``estimated_cost_usd=None`` when the model is not in the reference
    table, so an unknown price is never presented as a number.
    """
    price = lookup_price(provider, model)
    if price is None:
        return {
            "estimated_cost_usd": None,
            "pricing_source": "unknown model — configure LLM_PRICING_OVERRIDES or LLM_PRICING_FILE",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "as_of": None,
        }
    return {
        "estimated_cost_usd": price.cost(input_tokens, output_tokens),
        "pricing_source": price.source,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "as_of": price.as_of,
        "input_per_million": price.input_per_million,
        "output_per_million": price.output_per_million,
    }


def known_models(provider: str) -> list[str]:
    """Models with a known reference price, for UI hints and discovery."""
    if provider == "openai":
        return sorted(OPENAI_PRICES)
    if provider == "anthropic":
        return sorted(ANTHROPIC_PRICES)
    return []


def pricing_summary() -> dict[str, Any]:
    return {
        "as_of": AS_OF,
        "sources": {
            "openai": OPENAI_SOURCE,
            "anthropic": ANTHROPIC_SOURCE,
            "openrouter": OPENROUTER_SOURCE,
        },
        "models_with_known_price": {
            "openai": len(OPENAI_PRICES),
            "anthropic": len(ANTHROPIC_PRICES),
        },
        "override_env": "LLM_PRICING_OVERRIDES",
        "pricing_file_env": "LLM_PRICING_FILE",
        "note": (
            "Reference list prices retrieved from provider documentation. Verify against your own "
            "contract before relying on them for budgeting."
        ),
    }
