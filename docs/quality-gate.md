# Quality Notes - claude-production-patterns

Updated: 2026-05-30

These notes keep the archived repository reviewable without implying active production support.

## Profile

| Field | Value |
|---|---|
| Repository | `claude-production-patterns` |
| Status | Archived supporting proof |
| Primary stack | Python |
| Review expectation | Public review should not require customer data, production credentials, or vendor-owned assets. |

## Commands

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

## Boundaries

- Archived repositories should point to the current active successor when one exists.
- Demo, fixture, synthetic-data, and template modes must stay clearly labeled.
- Provider keys, tenant credentials, customer records, production logs, and confidential engagement material must never be committed.
- Production, compliance, ROI, accuracy, medical, financial, or safety claims require fresh validation before revival.

## Before Presenting

- README makes the archived/supporting status visible.
- Review guide, monetization playbook, revenue model, and enterprise readiness notes agree on scope.
- Local checks or CI workflows are visible enough for a reviewer to evaluate the surface.
- Any buyer conversation sells the reusable pattern, diagnostic, enablement material, or revival path rather than unsupported archived software.
