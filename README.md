# claude-production-patterns

> Production patterns for operating Claude at scale. What teams do between "it works in dev" and "serving 1M requests/day reliably" — cost control, rate limiting, prompt versioning, eval-in-CI, canary rollout.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

---

## Why this exists

[claude-agent-cookbook](https://github.com/KIM3310/claude-agent-cookbook) covers **how to use Claude's API**. This repo covers **how to operate Claude in production**. Different concerns.

Production patterns this repo catalogs:

1. **Rate limiting & backoff**: exponential backoff, token-bucket per tenant, global circuit breaker.
2. **Cost control**: spend caps (session, daily, monthly), anomaly detection, per-tenant attribution.
3. **Prompt versioning**: Git-based prompt registry, version-pinning, A/B rollout.
4. **Eval-in-CI**: regression-gate for every prompt change.
5. **Canary rollout**: shadow eval, gradual traffic shift, automated rollback.
6. **Multi-provider fallback**: Claude primary, OpenAI fallback, graceful degradation.
7. **Prompt caching economics**: when caching pays, when it doesn't.
8. **Observability**: metrics, structured logs, traces for agent workflows.
9. **Error classification**: transient vs permanent, retry vs fail-fast.
10. **Compliance**: audit trail, PII handling, retention, data residency.

Each pattern is a concrete implementation you can drop into your codebase.

## The patterns

| # | Pattern | What it solves | Complexity |
|---|---------|----------------|-----------|
| 01 | [rate-limiting](patterns/01-rate-limiting/) | Stay within Anthropic rate limits; protect per-tenant | Low |
| 02 | [cost-control](patterns/02-cost-control/) | Prevent cost runaway; attribute spend to tenants | Medium |
| 03 | [prompt-versioning](patterns/03-prompt-versioning/) | Git-based prompt registry with versioning | Low |
| 04 | [eval-in-ci](patterns/04-eval-in-ci/) | Block regressions on every PR | Medium |
| 05 | [canary-rollout](patterns/05-canary-rollout/) | Gradual traffic shift with auto-rollback | High |
| 06 | [multi-provider-fallback](patterns/06-multi-provider-fallback/) | Claude down → OpenAI fallback | Medium |
| 07 | [prompt-caching](patterns/07-prompt-caching/) | 90% cost reduction on stable system prompts | Low |
| 08 | [observability](patterns/08-observability/) | OTel traces + Prometheus metrics + structured logs | Medium |
| 09 | [error-classification](patterns/09-error-classification/) | Retry-worthy vs fail-fast errors | Low |
| 10 | [compliance](patterns/10-compliance/) | Audit trail, PII, retention, residency | High |

## Quick Start

```bash
git clone https://github.com/KIM3310/claude-production-patterns.git
cd claude-production-patterns
make install
make pattern NAME=01-rate-limiting
```

## Who this is for

- Teams operating Claude at 10K+ requests/day.
- Teams taking an internal prototype to production.
- FDEs advising customers on production readiness.
- Platform teams building shared Claude infrastructure.

Not for:
- Teams still in ideation / prototyping.
- Teams using Claude only via Claude.ai (not API).

## Relationship to other repos

| Repo | Relationship |
|------|-------------|
| [claude-agent-cookbook](https://github.com/KIM3310/claude-agent-cookbook) | Sibling. Covers API usage patterns; this covers operational patterns. |
| [stage-pilot](https://github.com/KIM3310/stage-pilot) | Tool-calling reliability runtime used by production agents. |
| [agent-orchestration-benchmark](https://github.com/KIM3310/agent-orchestration-benchmark) | Benchmark suite with reliability metrics relevant to these patterns. |
| [fde-engagement-playbook](https://github.com/KIM3310/fde-engagement-playbook) | FDE process playbook; refs this repo for technical readiness. |

## License

MIT.

## Cloud + AI Architecture

This repository includes a neutral cloud and AI engineering blueprint that maps the current proof surface to runtime boundaries, data contracts, model-risk controls, deployment posture, and validation hooks.

- [Cloud + AI architecture blueprint](docs/cloud-ai-architecture.md)
- [Machine-readable architecture manifest](docs/architecture/blueprint.json)
- Validation command: `python3 scripts/validate_architecture_blueprint.py`
