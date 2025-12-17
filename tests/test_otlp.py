import json

from app.ingestion import parse_document


def test_otlp_http_json_batch_is_split_into_distinct_runs():
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agent"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "instrumentation"},
                        "spans": [
                            {
                                "traceId": "trace-a",
                                "spanId": "span-a",
                                "name": "tool A",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000100000000",
                                "status": {"code": 1},
                            },
                            {
                                "traceId": "trace-b",
                                "spanId": "span-b",
                                "name": "tool B",
                                "startTimeUnixNano": "1760000001000000000",
                                "endTimeUnixNano": "1760000001100000000",
                                "status": {"code": 1},
                            },
                        ],
                    }
                ],
            }
        ]
    }
    records = parse_document(json.dumps(payload).encode(), "otlp.json")
    assert [trace.run_id for trace, _ in records] == ["trace-a", "trace-b"]
    assert [trace.spans[0].span_id for trace, _ in records] == ["span-a", "span-b"]
    assert all(trace.provenance["source_format"] == "otlp-json" for trace, _ in records)
