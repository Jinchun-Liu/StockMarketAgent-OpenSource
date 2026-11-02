# Deployment

`compose.yaml` is the R0P local and CI target stack. It runs PostgreSQL/pgvector, Valkey, SeaweedFS,
API, worker, scheduler and Web with pinned multi-architecture image digests. Redis and MinIO are no
longer runtime services. The existing `REDIS_*` and `MINIO_*` process variables are compatibility names
for the current application settings only; their endpoints resolve to Valkey and SeaweedFS.

Never invoke this file as an unnamed/default Compose project. Use the repository commands:

```text
make parallel-doctor
make workspace-up
make workspace-status
make workspace-down
make workspace-destroy
make migration-test
```

The wrapper derives `sma-<worktree-name>-<realpath-hash>` and always passes it with `docker compose -p`.
It disables implicit `.env` loading and records only non-sensitive runtime identity and dynamic
loopback URLs under `.runtime/workspaces/`.

- Web, API and the SeaweedFS master console receive dynamic `127.0.0.1` host ports.
- PostgreSQL, Valkey, the SeaweedFS S3/data plane, worker and scheduler have no host ports.
- all networks and named volumes are Compose-project scoped;
- `workspace-down` retains data, while `workspace-destroy` verifies the manifest, real path, project
  name and Docker labels before removing only that project's containers, network and named volumes.

`foundation` scope (used by integration tests) starts only PostgreSQL, Valkey and SeaweedFS. Full scope
also builds the application images. The local Qwen server remains external because no model or private
weight belongs in the repository.

Migration tests use the separate `compose.migration-test.yaml` override and a distinct
`sma-migration-<worktree-name>-<realpath-hash>` project. They accept an existing database only when
`R0P_TEST_DATABASE_URL` uses the async PostgreSQL driver, a local/CI-internal host and a database whose
name begins with `stock_assistant_r0p_test`; otherwise the wrapper creates a dynamic loopback test
database and destroys it in `finally`. `make migration-test` runs the upgrade suite; the recovery half
of `make integration-test` reuses the same safe harness with the allowlisted `recovery` suite.

The stack now includes a development-only R1 fixture vertical slice. With the fixture explicitly
enabled, the exercised path is:

```text
deploy/compose.yaml
→ scheduler: python -m apps.scheduler.main --fixture-once
→ Celery worker
→ PostgreSQL
→ API
→ Next.js Web
```

`SMA_RUN_DOCKER_E2E=1 make e2e-test` runs the product E2E in
`tests/e2e/test_runtime_smoke.py` for both synthetic fixture sources succeeding, CNINFO failing while
Choice succeeds, and Choice failing while CNINFO succeeds. Evidence and revision history,
source cursor/watermark state, the generated briefing, the latest-briefing API and the evidence API
are all exercised through PostgreSQL.

These CNINFO and Choice fixtures are synthetic and must be explicitly enabled; they do not access
either real provider and must never become a production fallback. The real CNINFO/Choice connectors
and grants are not implemented or ready. This vertical slice does not establish R1-COVERAGE, complete
R1, production market-data service, backup/restore qualification or suitability for trading
decisions.

## Current private R2 preview

As of 2026-09-09, the private runtime has real research enabled and synthetic fallback disabled. Deployment SHA, image digest, approved sources, exhausted once-only study cohorts and release blockers are recorded in the current task (upstream private record, excluded) and `governance/delivery.json`. Real research acceptance is not complete. Retained synthetic reports and offline fixtures keep their original identity.


`deploy/aws/r2-private-preview.yaml` defines the independent Tokyo R2 runtime. It deliberately contains no
R1 schedule and uses a two-phase rollout: create/update with `RuntimeDesiredCount=0`, run the migration task
through `scripts/r2_preview_aws.py migrate`, then plan activation with the exact migration receipt and
`RuntimeDesiredCount=1`. The runtime stack generates and retains a dedicated Secrets Manager origin-verification
value; `scripts/public_demo_aws.py` attaches the runtime ALB, its secret ARN, and exact Cognito domain to the
retained private-S3 CloudFront shell. The ALB returns 403 unless both the AWS-managed CloudFront prefix-list
boundary and the project-specific origin header match. `scripts/r2_preview_release.py` uploads immutable assets under the product
SHA, switches runtime config and HTML last, never invokes delete synchronization, and invalidates only known routes.
The worker keeps its root filesystem read-only and mounts a task-scoped ephemeral bind volume only at `/tmp`
for Celery prefork coordination; that scratch data is destroyed with the Fargate task.

The readable-research runtime keeps Cognito Authorization Code plus PKCE, while the owner-facing sign-in asks
only for username and password and the user pool has MFA disabled. DeepSeek and Tavily secret ARNs are mandatory
when an image is activated and are injected only into the worker execution context. API and scheduler tasks never
receive either secret. The worker has no direct Internet HTTPS rule: its only external route is an internal
CONNECT proxy that resolves and permits the exact DeepSeek, Tavily, approved official-source, and required AWS
service hostnames. Current real acquisition starts from actual gaps and separately audits fetched evidence; the legacy built-in-API-failure trigger is not its only discovery path. Tavily answer/raw-content features remain disabled. The API uses an explicit, validated proxy URL only for the fixed Cognito JWKS host; it has no
generic HTTPS egress or environment-proxy fallback. The scheduler has no proxy route and remains limited to
PostgreSQL, Valkey, DNS, private AWS endpoints and the S3 gateway endpoint.
