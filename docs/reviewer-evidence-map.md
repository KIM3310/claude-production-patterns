# Review Guide - claude-production-patterns

Updated: 2026-05-30

This repository is archived as a supporting proof. Review it for the reusable pattern, domain evidence, and portfolio relationship; do not treat it as the current flagship unless it is explicitly revived.

## Summary

| Field | Notes |
|---|---|
| Repository | `claude-production-patterns` |
| Status | Archived supporting repository |
| Lane | Claude production-readiness operating kit |
| Primary reader | AI platform leaders, staff engineers, governance owners, and teams moving from prototype to production. |
| Why it exists | Production teams need cost controls, prompt versioning, eval gates, canary rollout, and audit trails before scale. |
| Stack | Python |

## Open First

1. Read the README archived-status note and relationship to active repositories.
2. Inspect `docs/monetization-playbook.md` for the buyer lane and offer ladder.
3. Use the commands below to confirm the proof surface still has a review path.
4. Check CI workflows before making quality claims.
5. Keep the archived status visible in any portfolio conversation.

## Checks

| Purpose | Command |
|---|---|
| Test suite | `make test` |

## CI

- .github/workflows/architecture-blueprint.yml
- .github/workflows/ci.yml
- .github/workflows/dependency-review.yml
- .github/workflows/repository-health.yml
- .github/workflows/repository-surface.yml
- .github/workflows/secret-scan.yml

## Evidence

- Patterns compile and test
- Each pattern has a crisp failure mode
- Architecture docs point to validation hooks

## Commercial Notes

| Possible offer | Working price assumption | Scope |
|---|---|---|
| Production-readiness audit | $8k-$25k | Score a Claude stack against cost, reliability, eval, and compliance controls. |
| Operating-pattern implementation | $35k-$120k | Install selected controls into a shared platform or product service. |
| Managed governance retainer | $8k-$30k/month | Maintain eval gates, dashboards, prompt registry, and release reviews. |

## Boundaries

- Avoid promising exact cost reduction without customer telemetry
- Treat compliance examples as implementation aids, not legal advice
- Keep vendor-specific claims neutral

## Useful Metrics

- Audit starts
- Controls implemented
- Eval pass rate
- Cost anomaly reduction
