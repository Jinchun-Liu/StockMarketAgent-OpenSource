# End-to-end tests

The suite builds an isolated full stack, waits for migrations and target infrastructure, confirms the
PostgreSQL/API/Web/worker/scheduler processes, and runs a development-only R1 fixture product workflow:

```text
SMA_RUN_DOCKER_E2E=1 make e2e-test
```

`tests/e2e/test_runtime_smoke.py` explicitly invokes
`python -m apps.scheduler.main --fixture-once`, which dispatches five independent capability requests
through the Celery worker and persists source runs, mode/capability-local cursor and watermark state,
evidence and revision history, and the v1.1 briefing in PostgreSQL. The test then proves that the
default `application/json` response remains v1.0 while the explicit source-provenance media type
returns v1.1, before rendering the same briefing and evidence drill-down through Next.js Web.

The end-to-end path covers all five deterministic replay capabilities, correction/update/contradiction
revision types, provenance, idempotent replay, fixed-target Web rendering and non-clickable replay
URLs. It derives a historical evidence cutoff from persisted revision timestamps and proves that a
revision-1 cutoff cannot see revision 2. A second isolated stack first records a successful window,
then recreates the real Celery worker with one development-only SOURCE_FAILURE; the test proves the
failed capability leaves its cursor version, hash, watermarks and last-success value unchanged while
the other four capabilities advance independently and the briefing remains explicitly degraded
without falling back to another runtime mode.

This replay path uses synthetic development-only data and must be explicitly enabled. It performs no
real provider access and is not a production fallback. It does not mean authorized CNINFO/Choice
connectors or grants are ready, does not establish R1-COVERAGE or complete R1, and must not be described
as real market data or used for trading decisions.

The only live-development entry point is the opt-in one-shot scheduler command below. It must run in
an isolated development Compose workspace whose environment sets `R1_RUNTIME_MODE=dev_live`,
`ENABLE_R1_DEV_LIVE=true`, and `ENABLE_R1_FIXTURE=false`:

```text
python -m apps.scheduler.main --dev-live-once --at 2026-08-01T09:05:00+08:00
```

The command is never run by automated tests. A smoke succeeds only when at least one fixed
`603986.SH` capability records a real `dev_live` success, all five capabilities reach an auditable
terminal state, `macro.flashes` is explicitly `not_qualified`, no failed capability advances its
cursor, and no replay source appears in the resulting briefing.
