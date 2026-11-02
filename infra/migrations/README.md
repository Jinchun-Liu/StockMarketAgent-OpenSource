# Database migrations

Alembic is the only supported way to change the PostgreSQL schema. A migration must be forward-compatible with the currently deployed API/worker, include indexes and constraints, document long-running operations, and be tested against a restored backup before production use.

## R0P storage train

The `20260723_0002` through `20260723_0006` revisions are one ordered
expand/backfill/validate/contract train:

1. **EXPAND** adds nullable event identity, Job/Message envelope fields, the
   append-only Job transition ledger, a technical consumer inbox, and repair/checkpoint
   tables. It does not create a user notification inbox.
2. **BACKFILL** derives only information already proved by legacy rows. The stable event
   ID comes from the first revision row in the existing cluster. Missing Job context or
   Outbox envelope identity is not guessed; such rows enter `migration_repair_queue`.
3. **VALIDATE** fails closed while a repair item or invalid PIT/uniqueness relation
   remains. Operators repair data from audit evidence, set `resolved_at` plus a
   non-empty `resolution_note`, and rerun the failed revision.
4. **CONTRACT** runs only after old writers are zero. It applies `NOT NULL`, logical
   uniqueness, and strict revision-chain enforcement without deleting history.
5. **KNOWLEDGE TIME FORWARD FIX** adds database-owned `recorded_at` to every immutable
   Job transition. PIT recovery filters both business `occurred_at` and database
   `recorded_at`, so a later-inserted backdated transition cannot leak into an earlier
   decision view. Existing rows receive one conservative migration cutoff via the
   non-volatile `CURRENT_TIMESTAMP` fast-default path; it is not a reconstruction of
   their original first-seen time. A historical query before that cutoff must abstain
   rather than infer an earlier knowledge time. New rows receive statement time from
   a database trigger and cannot override or later mutate it.

Revision `0006` is intentionally applied during R0P, before production Job writers
exist, so its ordinary PIT index build is bounded to an empty or synthetic-small
ledger. If an operator finds a materially populated ledger, the safe response is to
stop and introduce a new forward migration with a concurrent index plan; do not
rewrite this published revision after it lands.

The train is explicitly forward-only. Its `downgrade()` functions raise with the
forward-repair reason because deleting transition, deduplication, or repair evidence
would make recovery unauditable. A defect is corrected with a new add-only revision.

`message_inbox_receipts` is strictly a broker-consumer deduplication ledger. Future
user-facing inbox projections must use a separate table and migration.

## Parallel-delivery rules

- The migration graph is a single-writer boundary owned by the active Migration Steward.
- Every new revision starts from the current unique head. Published revisions are append-only and must
  never be edited, reordered, renamed or reused.
- Feature lanes submit their required schema delta to the steward; they do not create competing revisions
  in separate worktrees.
- If two accepted revisions still create multiple heads, the steward adds an explicit Alembic merge
  revision. Rewriting an existing `down_revision` is forbidden.
- Schema changes use expand/migrate/contract so the previous API and worker can coexist during rollout.
  Backfills must be bounded, retryable and independently observable.
- `scripts/check_delivery_registry.py` statically requires an acyclic graph with exactly one head.
  Before a stage is verified, integration must also exercise empty-database upgrade, previous-release
  upgrade and restore/forward-fix paths against PostgreSQL; the static check is not a database test.
