# ADR-0005: Reuse mature open-source capabilities before custom implementation

- Status: accepted
- Date: 2026-07-22

## Decision

Require an evidence-backed reuse decision before adding a capability, dependency, service, model or
non-trivial algorithm. Prefer commercially usable, actively maintained projects behind project-owned
ports. Build only the smallest domain-specific gap after credible candidates fail explicit license,
rights, security, architecture or quality gates.

## Rationale

The assistant combines common platform problems with uncommon A-share evidence and point-in-time
invariants. Reimplementing parsers, queues, vector search, model training and observability wastes
effort, while wholesale forks import unlicensed data assumptions, trading behavior and architecture
that conflict with this repository. A recorded decision keeps reuse deliberate and auditable.

## Consequences

New capabilities and dependencies require a record under `docs/research/reuse-decisions/` when defined
by the reuse-first policy. One default implementation is selected; unused parallel stacks are rejected.
Third-party types remain behind ports, versions and artifacts are pinned, and license/data/model rights
are reviewed separately. Custom code must identify the exact unmet gap and remains subject to the same
tests, security and exit requirements.
