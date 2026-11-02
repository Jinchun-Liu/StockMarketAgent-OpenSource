# Security Policy

## Supported branch

Security fixes target `main` until the first stable release.

## Reporting

Do not open a public issue containing API keys, holdings, raw licensed data, model prompts, or exploit details. Report privately to the repository owner and include affected commit, reproduction steps, impact, and any safe mitigation.

## Non-negotiable controls

- Secrets never enter Git, logs, browser storage, prompts, or notification bodies.
- Holdings stay local unless `ALLOW_EXTERNAL_HOLDING_DATA=true` is explicitly enabled.
- Ingested HTML/PDF is untrusted input; instructions embedded in source text are ignored.
- Every generated factual claim must resolve to stored evidence.
- The application does not place live orders.
