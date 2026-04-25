# Security Policy

## Supported Versions

Security fixes are applied to the default branch. Consumers should run the latest commit or latest tagged release when available.

## Reporting a Vulnerability

Do not open a public issue for suspected vulnerabilities. Use GitHub private vulnerability reporting if it is enabled for this repository, or contact the repository owner through their GitHub profile.

Please include:

- A clear description of the issue and affected pattern
- Reproduction steps or a minimal proof of concept
- Potential impact and any known mitigations
- Whether prompts, API keys, logs, or customer data may be exposed

## Security Expectations

- Never commit API keys, customer prompts, production logs, or evaluation datasets with private content.
- Treat observability examples as examples only; scrub sensitive payloads before sharing.
- Run local verification before merging:

```bash
python -m ruff check .
python -m compileall -q patterns
```
