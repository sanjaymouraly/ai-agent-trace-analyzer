"""Evaluation harness for the deterministic analysis engine.

The harness runs the engine over labelled synthetic fixtures and reports
**measured** metrics: category precision/recall, known-cause localization
accuracy, first-divergence accuracy and abstention quality.

These numbers describe the behaviour on this fixture set. They are a
regression benchmark, not a real-world accuracy guarantee.

Usage::

    python -m evals.benchmark            # prints the summary and writes evals/latest-results.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.analysis import analyze_trace, classify_failures  # noqa: E402
from app.comparison import compare_traces  # noqa: E402
from app.ingestion import normalize_trace  # noqa: E402
from examples.generate import generate_demo_traces  # noqa: E402

PROPAGATION_CATEGORY = "failure_propagation"


def _score_categories(cases: list[dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = 0
    per_category: dict[str, dict[str, int]] = {}
    for case in cases:
        predicted = set(case["predicted_categories"])
        expected = set(case["expected_categories"])
        for category in predicted & expected:
            bucket = per_category.setdefault(category, {"tp": 0, "fp": 0, "fn": 0})
            bucket["tp"] += 1
        for category in predicted - expected:
            bucket = per_category.setdefault(category, {"tp": 0, "fp": 0, "fn": 0})
            bucket["fp"] += 1
        for category in expected - predicted:
            bucket = per_category.setdefault(category, {"tp": 0, "fp": 0, "fn": 0})
            bucket["fn"] += 1
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "per_category": {
            category: {
                **counts,
                "precision": round(counts["tp"] / (counts["tp"] + counts["fp"]), 4) if counts["tp"] + counts["fp"] else None,
            }
            for category, counts in sorted(per_category.items())
        },
    }


def score() -> dict[str, Any]:
    labels = json.loads((ROOT / "evals" / "ground_truth.json").read_text(encoding="utf-8"))
    generated = {raw["run_id"]: normalize_trace(raw, source_format="synthetic-evaluation") for raw in generate_demo_traces()}

    cases: list[dict[str, Any]] = []
    localized = 0
    localization_total = 0
    abstain_tp = abstain_fp = abstain_fn = 0
    hypothesis_count = 0
    false_positive_runs = 0

    for label in labels["cases"]:
        trace = generated[label["run_id"]]
        findings = classify_failures(trace)
        predicted = {f["category"] for f in findings if f["category"] != PROPAGATION_CATEGORY}
        expected = set(label["expected_categories"])
        result = analyze_trace(trace)
        top = (result.get("root_cause_hypotheses") or [{}])[0]
        predicted_cause = top.get("primary_span_id")
        hypothesis_count += len(result.get("root_cause_hypotheses", []))

        if expected - predicted:
            false_positive_runs += 0  # tracked through precision/recall instead
        if predicted - expected:
            false_positive_runs += 1

        expected_cause = label.get("expected_cause_span_id")
        if expected_cause:
            localization_total += 1
            localized += int(predicted_cause == expected_cause)

        predicted_abstain = not predicted_cause or top.get("category") == "unknown_or_insufficient_evidence"
        if label.get("must_abstain"):
            abstain_tp += int(predicted_abstain)
            abstain_fn += int(not predicted_abstain)
        elif expected_cause:
            abstain_fp += int(predicted_abstain)

        cases.append(
            {
                "run_id": trace.run_id,
                "expected_categories": sorted(expected),
                "predicted_categories": sorted(predicted),
                "true_positive_categories": sorted(predicted & expected),
                "false_positive_categories": sorted(predicted - expected),
                "missed_categories": sorted(expected - predicted),
                "expected_cause_span_id": expected_cause,
                "predicted_top_hypothesis_span_id": predicted_cause,
                "localization_correct": bool(expected_cause and predicted_cause == expected_cause),
                "expected_abstention": bool(label.get("must_abstain")),
                "predicted_abstention": predicted_abstain,
                "hypothesis_count": len(result.get("root_cause_hypotheses", [])),
            }
        )

    comparisons: list[dict[str, Any]] = []
    divergence_correct = 0
    for spec in labels.get("comparisons", []):
        baseline = generated[spec["baseline_run_id"]]
        candidate = generated[spec["candidate_run_id"]]
        pair = compare_traces([baseline, candidate])["pairwise"][0]
        divergence = pair.get("first_divergence") or {}
        predicted_span = divergence.get("right_span_id")
        expected_span = spec.get("expected_divergence_span_id")
        fields = {diff.get("field") for diff in divergence.get("differences", [])}
        field_ok = spec.get("expected_divergence_field") in fields if spec.get("expected_divergence_field") else True
        correct = bool(predicted_span == expected_span and field_ok)
        divergence_correct += int(correct)
        comparisons.append(
            {
                "name": spec["name"],
                "baseline_run_id": spec["baseline_run_id"],
                "candidate_run_id": spec["candidate_run_id"],
                "expected_divergence_span_id": expected_span,
                "predicted_divergence_span_id": predicted_span,
                "expected_divergence_field": spec.get("expected_divergence_field"),
                "observed_difference_fields": sorted(field for field in fields if field),
                "correct": correct,
                "aligned_action_count": len(pair.get("aligned_actions", [])),
            }
        )

    category_metrics = _score_categories(cases)
    abstain_precision = abstain_tp / (abstain_tp + abstain_fp) if abstain_tp + abstain_fp else (1.0 if not abstain_tp else 0.0)
    abstain_recall = abstain_tp / (abstain_tp + abstain_fn) if abstain_tp + abstain_fn else 0.0

    return {
        "benchmark": "TraceLens synthetic labeled fixtures v2",
        "case_count": len(cases),
        "comparison_count": len(comparisons),
        "category_micro": category_metrics,
        "known_cause_localization": {
            "correct_top_hypothesis": localized,
            "cases_with_labeled_cause": localization_total,
            "accuracy": round(localized / localization_total, 4) if localization_total else None,
        },
        "first_divergence": {
            "correct": divergence_correct,
            "comparisons": len(comparisons),
            "accuracy": round(divergence_correct / len(comparisons), 4) if comparisons else None,
        },
        "abstention_quality": {
            "correct_abstentions": abstain_tp,
            "false_abstentions_on_labeled_causes": abstain_fp,
            "missed_abstentions": abstain_fn,
            "precision": round(abstain_precision, 4),
            "recall": round(abstain_recall, 4),
        },
        "false_positive_runs": false_positive_runs,
        "mean_hypotheses_per_run": round(hypothesis_count / len(cases), 2) if cases else None,
        "limitations": [
            "Synthetic traces encode clean annotations and do not represent production instrumentation noise.",
            "Category metrics count expected category labels as multi-label observations.",
            "Cause localization checks only the top-ranked hypothesis.",
            "The divergence set is small; treat its accuracy as indicative, not statistically meaningful.",
            "Metrics are a regression benchmark, not a real-world accuracy guarantee.",
        ],
        "cases": cases,
        "comparisons": comparisons,
    }


def main() -> int:
    result = score()
    target = ROOT / "evals" / "latest-results.json"
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = {key: value for key, value in result.items() if key not in {"cases", "comparisons"}}
    print(json.dumps(summary, indent=2))
    print(f"\nPer-case results: {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
