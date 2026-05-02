# Contributing to claude-production-patterns

Thanks for helping improve this project. Keep changes small, documented, and easy to review.

## Development Workflow

1. Create a branch from `main`.
2. Make focused changes with tests or validation notes.
3. Run the checks described in the README, package scripts, or CI workflow.
4. Open a pull request with a clear summary and testing evidence.

## Commit Messages

Use short conventional commit messages so releases, audits, and changelogs stay readable:

- `feat: add user-facing capability`
- `fix: correct broken behavior`
- `docs: update usage or architecture notes`
- `test: add or adjust coverage`
- `refactor: simplify code without behavior change`
- `chore: update tooling or maintenance files`
- `deps: update dependencies`

Keep the subject line under 72 characters when practical. Use the body to explain why the change was made when the reason is not obvious from the diff.

## Quality Bar

Before requesting review:

- Run the fastest relevant local check first.
- Run the full test suite when touching shared logic or public behavior.
- Update documentation for workflow, API, configuration, or operational changes.
- Keep generated files, secrets, local caches, and editor metadata out of commits.

## Security

Do not include credentials, private keys, tokens, personal data, or production customer data in commits, issues, pull requests, screenshots, fixtures, or logs.
