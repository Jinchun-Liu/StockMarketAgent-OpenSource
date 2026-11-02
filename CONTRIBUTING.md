# Contributing

Read [AGENTS.md](AGENTS.md) and [the distribution notes](docs/public-source-distribution.md).

1. Define the user capability, runnable path, acceptance evidence and non-goals.
2. Use a short `codex/` branch and preserve unrelated changes.
3. Reuse contracts and Ports; keep business rules outside routes and frontend components.
4. Keep generated schemas/types synchronized and published migrations append-only.
5. Run `uv run make public-check` and focused tests for the change. Data-dependent and
   complete-runtime tests require their own lawful local inputs and infrastructure;
   report limitations without marking them passed.
6. Obtain one independent read-only review of the final committed change before integration.
7. Never contribute secrets, private logs, restricted data, model weights, holdings or reports.
8. Preserve evidence, PIT, idempotency, fail-closed source authorization and paper-only scope.

The public repository provides no inherited cloud account or owner grant. Changes to external
systems require their actual owner's explicit authorization. Do not copy the upstream private
delivery ledger into a contribution or use private workflow fixtures as access permission.
