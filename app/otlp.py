"""OpenTelemetry Protocol JSON batch splitter (no imported code or protobuf decoder)."""

from __future__ import annotations

from typing import Any


def split_otlp_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Split resourceSpans JSON into per-trace envelopes while retaining resources/scopes."""
    resources = payload.get("resourceSpans")
    if not isinstance(resources, list):
        return [payload]
    trace_ids = list(
        dict.fromkeys(
            str(span.get("traceId"))
            for resource in resources
            if isinstance(resource, dict)
            for scope_key in ("scopeSpans", "instrumentationLibrarySpans")
            for scope in (resource.get(scope_key) or [])
            if isinstance(scope, dict)
            for span in (scope.get("spans") or [])
            if isinstance(span, dict) and span.get("traceId")
        )
    )
    if len(trace_ids) <= 1:
        return [payload]
    result: list[dict[str, Any]] = []
    for trace_id in trace_ids:
        selected_resources: list[dict[str, Any]] = []
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            selected_resource = {k: v for k, v in resource.items() if k not in {"scopeSpans", "instrumentationLibrarySpans"}}
            for scope_key in ("scopeSpans", "instrumentationLibrarySpans"):
                selected_scopes = []
                for scope in resource.get(scope_key, []) or []:
                    if not isinstance(scope, dict):
                        continue
                    spans = [
                        span
                        for span in scope.get("spans", []) or []
                        if isinstance(span, dict) and str(span.get("traceId", "")) == trace_id
                    ]
                    if spans:
                        selected_scopes.append({**scope, "spans": spans})
                if selected_scopes:
                    selected_resource[scope_key] = selected_scopes
            if "scopeSpans" in selected_resource or "instrumentationLibrarySpans" in selected_resource:
                selected_resources.append(selected_resource)
        result.append(
            {
                "resourceSpans": selected_resources,
                "trace_id": trace_id,
                "run_id": trace_id,
                "task": f"OTLP trace {trace_id[:12]}",
            }
        )
    return result
