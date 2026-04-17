"""Pattern 04 — Eval in CI.

Block prompt changes that regress quality. Run a gold-set evaluation on every
PR that modifies prompts. Fail CI if regression exceeds threshold.

This is implemented as a pytest plugin + a CI job. The gold set is a JSONL
file in the repo. The eval harness scores each prompt-response pair.

Usage in CI:
    python -m patterns.eval_in_ci.run \\
        --gold-set evals/gold_set.jsonl \\
        --baseline-scores evals/baseline_scores.json \\
        --regression-threshold 0.05
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalExample:
    prompt: str
    expected_keywords: list[str]
    forbidden_keywords: list[str]
    expected_output_pattern: str | None = None


@dataclass
class EvalResult:
    example_id: str
    score: float
    passed: bool
    failures: list[str]


def score_response(response: str, example: EvalExample) -> EvalResult:
    """Simple rubric-based scoring.

    In production, this would include:
    - LLM-as-judge for semantic quality
    - Regex patterns for structured output
    - Latency / cost checks
    """
    failures = []
    score = 1.0

    # Check required keywords present
    missing = [kw for kw in example.expected_keywords if kw.lower() not in response.lower()]
    if missing:
        failures.append(f"missing keywords: {missing}")
        score -= 0.3

    # Check forbidden keywords absent
    present = [kw for kw in example.forbidden_keywords if kw.lower() in response.lower()]
    if present:
        failures.append(f"forbidden keywords present: {present}")
        score -= 0.5

    # Check output pattern if specified
    if example.expected_output_pattern:
        import re

        if not re.search(example.expected_output_pattern, response):
            failures.append("expected pattern not found")
            score -= 0.2

    score = max(0.0, score)
    return EvalResult(
        example_id="",
        score=score,
        passed=score >= 0.7,
        failures=failures,
    )


def load_gold_set(path: Path) -> list[tuple[str, EvalExample]]:
    """Load gold set from JSONL."""
    examples = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        ex = EvalExample(
            prompt=record["prompt"],
            expected_keywords=record.get("expected_keywords", []),
            forbidden_keywords=record.get("forbidden_keywords", []),
            expected_output_pattern=record.get("expected_output_pattern"),
        )
        examples.append((record.get("id", f"ex_{line_no}"), ex))
    return examples


def run_eval(
    gold_set_path: Path,
    generate_fn,  # callable: prompt → response
) -> list[EvalResult]:
    """Run gold-set eval; return results."""
    examples = load_gold_set(gold_set_path)
    results = []
    for ex_id, example in examples:
        try:
            response = generate_fn(example.prompt)
        except Exception as e:
            results.append(
                EvalResult(
                    example_id=ex_id,
                    score=0.0,
                    passed=False,
                    failures=[f"generate failed: {e}"],
                )
            )
            continue
        result = score_response(response, example)
        result.example_id = ex_id
        results.append(result)
    return results


def compare_to_baseline(
    current_results: list[EvalResult],
    baseline_scores_path: Path,
    regression_threshold: float = 0.05,
) -> tuple[bool, dict]:
    """Compare current eval to baseline.

    Returns (passed, detail_dict).
    Fails if aggregate score regressed more than regression_threshold.
    """
    if not baseline_scores_path.exists():
        # No baseline yet — accept current as baseline
        return True, {
            "status": "no_baseline",
            "note": "No baseline to compare against; current results accepted",
            "current_aggregate": sum(r.score for r in current_results) / max(1, len(current_results)),
        }

    baseline = json.loads(baseline_scores_path.read_text())
    baseline_aggregate = baseline.get("aggregate_score", 0.0)
    current_aggregate = sum(r.score for r in current_results) / max(1, len(current_results))

    delta = current_aggregate - baseline_aggregate

    if delta < -regression_threshold:
        return False, {
            "status": "regression",
            "baseline_aggregate": baseline_aggregate,
            "current_aggregate": current_aggregate,
            "delta": delta,
            "threshold": -regression_threshold,
            "failed_examples": [r.example_id for r in current_results if not r.passed],
        }

    return True, {
        "status": "passed",
        "baseline_aggregate": baseline_aggregate,
        "current_aggregate": current_aggregate,
        "delta": delta,
    }


def update_baseline(
    current_results: list[EvalResult],
    baseline_scores_path: Path,
) -> None:
    """Write current results as new baseline. Run on merge to main."""
    aggregate = sum(r.score for r in current_results) / max(1, len(current_results))
    baseline_scores_path.write_text(
        json.dumps(
            {
                "aggregate_score": aggregate,
                "per_example": [
                    {"id": r.example_id, "score": r.score, "passed": r.passed}
                    for r in current_results
                ],
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval-in-CI runner")
    parser.add_argument("--gold-set", type=Path, required=True)
    parser.add_argument("--baseline-scores", type=Path, required=True)
    parser.add_argument("--regression-threshold", type=float, default=0.05)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    # In real CI, generate_fn would call the actual Claude client
    # For the demo, use a stub
    def demo_generate(prompt: str) -> str:
        return f"Demo response to: {prompt[:60]}"

    results = run_eval(args.gold_set, demo_generate)

    # Print summary
    passed = sum(1 for r in results if r.passed)
    print(f"Eval results: {passed}/{len(results)} passed")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.example_id} score={r.score:.2f} failures={r.failures}")

    ok, detail = compare_to_baseline(results, args.baseline_scores, args.regression_threshold)
    print(f"\nBaseline comparison: {detail['status']}")
    for k, v in detail.items():
        if k != "status":
            print(f"  {k}: {v}")

    if args.update_baseline:
        update_baseline(results, args.baseline_scores)
        print("Baseline updated.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
