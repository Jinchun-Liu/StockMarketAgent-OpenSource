# ADR-0006: Reuse and evolve existing interfaces and metrics before creating new ones

- Status: accepted
- Date: 2026-07-22

## Decision

Treat `governance/interfaces.json` and `governance/metrics.json` as the canonical inventories for public
interfaces and unified metrics. Every change must search these inventories and all consumers first. A
semantically equivalent definition must be reused; a new definition is allowed only after the unmet gap is
recorded. Existing definitions cannot be changed silently, and every affected standard, producer, consumer,
schema, dashboard, threshold, document and test changes atomically.

## Rationale

An expanding assistant can easily acquire duplicate ports, endpoints, task names, latency counters or model
quality formulas when different agents work from local context. Those duplicates create incompatible semantics
that naming conventions alone cannot repair. A canonical inventory, an explicit search step and machine checks
make extension deliberate while preserving historical comparability.

## Consequences

Interface and metric work has a small up-front inventory and impact-analysis cost. In return, adapters and
consumers share stable semantics, dashboards and ML reports remain comparable, and breaking changes receive a
new version instead of reinterpreting history. Reviewers must verify semantic equivalence manually; automated
checks enforce structure, exact duplicates, registration and synchronization markers but cannot prove two
different formulas mean the same thing.
