# StockMarketAgent Public Source Agent Rules

<!-- governance-entry:v1 -->

## Distribution Context and Authorization

This repository is a public source distribution. It has no inherited owner authorization,
private deployment, private account, provider credential, or production release permission.
The upstream private delivery ledger and host hooks are intentionally not distributed.
Never recreate an active authorization record from an upstream reference or assume a public
fork has permission to operate its author's infrastructure.

Read README.md, docs/public-source-distribution.md, docs/engineering/coding-standards.md,
docs/architecture/interface-standards.md and relevant existing code before edits.
The historical private-workflow documentation and its context-bound scripts remain reference
material; make public-check is this distribution's executable source acceptance entrypoint.
If a checkout has an explicit governance/delivery.json, read its current_stage,
integration_candidate, private_preview and owner_authorization before acting; it still cannot
override the current user's instructions, platform approval, source rights or secret policy.

## Product-First Delivery Budget

1. Before changes, state User Capability, Runnable Path, Acceptance Evidence and Non-goals.
2. Preserve user changes. Work on one short codex/ branch with precise write scope.
3. Reuse existing contracts, Ports, metrics and application boundaries. No parallel executor.
4. Implement, run focused tests, obtain one independent read-only review, then integrate.
   Use one fix opportunity; do not create attempt/seal/control/handoff machinery.
5. Completion requires a real user path. Documents, hashes, receipts or passing static checks
   do not prove stubbed, SKIPPED or NOT_IMPLEMENTED product capabilities.
6. New safeguards must address a reproducible live uncertainty and change a subsequent action.
   Do not add decorative hashes or repeat unchanged full verification.
7. Retain rights fail-closed, evidence_id, PIT and bitemporal visibility, idempotency,
   stable Decimal money, timezone-aware UTC timestamps, identifiers and errors.
8. Run focused checks during development; integration/release alone runs complete applicable
   product checks. State unavailable data, environmental limitations and skipped checks.
9. Update state documents only when a user capability, authorization or release actually changes.
10. Simplification must reduce steps, state, wrappers or branch types and never weaken correctness.

## Architecture and Data

Dependency direction: contracts/common <- ports <- modules/application <- platform <- apps.
Pydantic is the public data structure source; generated schemas and frontend API types must
not be hand-edited. Search governance/interfaces.json and governance/metrics.json first.
Published migrations are append-only. Public APIs use versioned contracts and stable errors.
Python 3.12 uses Ruff and strict mypy; TypeScript remains strict. External I/O requires
timeouts, bounded retries, idempotency and explicit authorization.

No real trading, automatic order placement, return promises, holdings publication or private data.
R1–R6 research labels remain DEV_ONLY/UNVERIFIED; R7 formal source authorization fails closed.
Facts must resolve to evidence_id; historical evaluation obeys PIT. Never bypass robots,
login, CAPTCHA, rate limits or access controls. Synthetic data must stay visibly synthetic.
Stock forum content may support aggregated sentiment, not sole company-fact evidence.

## Secrets and Private Assets

Do not read credentials or .env files; .env.example is the only exception. Never print, log,
commit or recover secret values. Use a managed client only within explicit current authorization.
Do not commit raw or private data, licensed snapshots without redistribution rights, model
weights, checkpoints, personal reports or private deployment records. The excluded catalog
resources/instruments/ashare-catalog.dev.v1.json.gz must remain local and untracked.
Keep THIRD_PARTY.md and upstream licenses. Package/SDK licenses do not authorize source data.

## Git and Verification

Keep shared history intact: main advances with ff-only; recover with exact revert or forward
fix. main --ff-only does not imply permission to publish. No force-push or history rewrite.
Commit, push, merge, release, deploy or external writes require a current explicit task grant.
Do not disable checks, lower thresholds, add exclusions or delete tests to pass a gate.
No user data deletion or overwrite. On macOS, move deletions to Trash; if unavailable, stop
and ask. Do not directly edit Git administrative files.

An agent may stop when complete, blocked or awaiting an owner decision. Report the actual
result, validation and material limitations, without expanding into unrelated repairs.
