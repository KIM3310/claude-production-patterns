"""Pattern 07 — Prompt Caching with Claude.

When to use:
    - System prompt >2000 tokens
    - Same system prompt used across multiple requests
    - Cost reduction matters

Claude's prompt caching offers:
    - 90% discount on cache_read tokens vs regular input
    - ~5 minute cache TTL (ephemeral)
    - Up to 4 cache breakpoints per request

This pattern shows:
    1. How to mark content as cacheable
    2. How to measure actual savings
    3. When caching DOESN'T pay off
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from anthropic import Anthropic  # type: ignore


LARGE_SYSTEM_PROMPT = """\
You are a contract analysis assistant for a legal team.

Context about the team's work:
[... imagine 50KB of context here: the team's conventions, example prior
analyses, style guide, risk taxonomy, escalation rules, citation formats,
legal jurisdiction notes, and so on. Real production system prompts at
this scale save significant cost through caching.]
"""


@dataclass
class CachingMetrics:
    request_id: str
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    regular_input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


def call_with_caching(
    client: Anthropic,
    user_query: str,
    system_prompt: str = LARGE_SYSTEM_PROMPT,
    model: str = "claude-sonnet-4-20250514",
) -> CachingMetrics:
    """Call Claude with the system prompt marked as cacheable."""
    start = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_query}],
    )
    latency_ms = int((time.time() - start) * 1000)

    usage = response.usage
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    regular_input = usage.input_tokens - cache_write - cache_read
    output = usage.output_tokens

    # Cost (April 2026 pricing for Claude Sonnet 4)
    cost = (
        (regular_input / 1_000_000) * 3.00
        + (cache_write / 1_000_000) * 3.75
        + (cache_read / 1_000_000) * 0.30
        + (output / 1_000_000) * 15.00
    )

    return CachingMetrics(
        request_id=response.id,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
        regular_input_tokens=regular_input,
        output_tokens=output,
        cost_usd=cost,
        latency_ms=latency_ms,
    )


def call_without_caching(
    client: Anthropic,
    user_query: str,
    system_prompt: str = LARGE_SYSTEM_PROMPT,
    model: str = "claude-sonnet-4-20250514",
) -> CachingMetrics:
    """Same call WITHOUT cache_control marking."""
    start = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_query}],
    )
    latency_ms = int((time.time() - start) * 1000)

    usage = response.usage
    cost = (usage.input_tokens / 1_000_000) * 3.00 + (usage.output_tokens / 1_000_000) * 15.00

    return CachingMetrics(
        request_id=response.id,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        regular_input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=cost,
        latency_ms=latency_ms,
    )


def measure_caching_benefit(
    client: Anthropic,
    test_queries: list[str],
    system_prompt: str = LARGE_SYSTEM_PROMPT,
) -> dict:
    """Run N queries with and without caching; report savings."""
    cached_metrics = []
    uncached_metrics = []

    print("Running WITHOUT caching...")
    for q in test_queries:
        m = call_without_caching(client, q, system_prompt)
        uncached_metrics.append(m)
        print(f"  ${m.cost_usd:.6f} / {m.latency_ms}ms")

    print("\nRunning WITH caching...")
    for i, q in enumerate(test_queries):
        m = call_with_caching(client, q, system_prompt)
        cached_metrics.append(m)
        status = "WRITE" if m.cache_creation_input_tokens > 0 else "READ"
        print(f"  [{status}] ${m.cost_usd:.6f} / {m.latency_ms}ms")

    uncached_total = sum(m.cost_usd for m in uncached_metrics)
    cached_total = sum(m.cost_usd for m in cached_metrics)

    return {
        "queries": len(test_queries),
        "uncached_total_cost": uncached_total,
        "cached_total_cost": cached_total,
        "savings_absolute": uncached_total - cached_total,
        "savings_pct": (uncached_total - cached_total) / uncached_total * 100,
        "avg_uncached_latency_ms": sum(m.latency_ms for m in uncached_metrics) / len(uncached_metrics),
        "avg_cached_read_latency_ms": sum(
            m.latency_ms for m in cached_metrics[1:]
        ) / max(1, len(cached_metrics) - 1),
    }


def should_use_caching(
    system_prompt_tokens: int,
    requests_per_5min_window: int,
) -> tuple[bool, str]:
    """Heuristic: is caching worth it for this workload?

    Returns (yes/no, reason).
    """
    if system_prompt_tokens < 1024:
        return False, "System prompt too small; caching overhead exceeds benefit"

    if requests_per_5min_window < 2:
        return False, "Requests too infrequent; cache TTL will expire before reuse"

    if system_prompt_tokens >= 2048 and requests_per_5min_window >= 3:
        return True, "Large stable system prompt + frequent requests: caching strongly recommended"

    return True, "Marginally beneficial; measure actual savings before committing"


if __name__ == "__main__":
    import os
    import sys

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY to run this demo.")
        sys.exit(1)

    client = Anthropic()
    queries = [
        "Summarize this contract: [... clause 1 ...]",
        "Flag risks in this clause: [... clause 2 ...]",
        "Extract entities from: [... clause 3 ...]",
        "Compare to playbook: [... clause 4 ...]",
        "Final assessment: [... clause 5 ...]",
    ]

    results = measure_caching_benefit(client, queries)
    print("\n--- Results ---")
    for k, v in results.items():
        if isinstance(v, float):
            print(f"{k}: {v:.4f}")
        else:
            print(f"{k}: {v}")

    print("\n--- Heuristic ---")
    use, reason = should_use_caching(
        system_prompt_tokens=len(LARGE_SYSTEM_PROMPT) // 4,  # rough estimate
        requests_per_5min_window=5,
    )
    print(f"Use caching: {use}")
    print(f"Reason: {reason}")
