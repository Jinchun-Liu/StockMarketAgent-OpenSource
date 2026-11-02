# R1 continuous-preview runtime reuse decision

## Decision

Adopt `exchange-calendars==4.13.2` behind the existing `TradingCalendarPort` and
`boto3==1.43.51` behind the snapshot publisher's narrow S3 client seam. The project retains
the business-specific XSHG window rule, evidence revision joins, public-data scanner,
last-known-good policy, catch-up classification and pointer-switch ordering.

This is a development-only preview. It does not make the government discovery sources
commercially authorized, does not authorize a production data claim and does not add any
trading capability.

## Calendar candidates

- [`exchange_calendars` 4.13.2](https://pypi.org/project/exchange-calendars/4.13.2/) is an
  Apache-2.0 library with an XSHG calendar and explicit sessions, opens, breaks and closes.
  It is selected because those primitives map directly to `TradingCalendarPort` without
  importing a web framework or provider SDK into the domain. The adapter emits every natural
  date, including closed dates, and stamps output with the query's point-in-time context.
- [`pandas_market_calendars`](https://pandas-market-calendars.readthedocs.io/) is credible and
  permissively licensed, but would duplicate the selected calendar role and offers no benefit
  for the bounded XSHG-only preview.
- Official exchange calendars remain the authoritative facts. A library session is an adapter
  output, not an independent market-status claim; discrepancies fail the refresh rather than
  being silently repaired.

The exact release and transitive artifacts are resolved in `uv.lock`; the 4.13.2 universal
wheel SHA-256 is `fc5a2ad0d61b5c3a6539a3061cd4cbb55c59f4a903455cec7926e4b798919996`.
No unpinned runtime calendar release is permitted.

## S3 client candidates

- [Boto3 S3](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)
  is selected. In ECS it refreshes task-role credentials through the container credential
  provider, supports bounded SDK retry configuration, request and response checksums, SSE-S3,
  and native `IfMatch` / `IfNoneMatch` conditional writes. This is the minimum primitive set
  for assets-first, manifest-second, full-readback, pointer-last publication.
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-welcome.html) remains an
  operator/deployment tool. It is not selected inside the coordinator because subprocess
  lifecycle, output parsing and error classification weaken the transactional publication
  boundary.
- The existing MinIO Python compatibility client is not selected. It does not express the
  chosen AWS-native credential-refresh, checksum and conditional S3 behavior as directly, and
  a new MinIO runtime is already rejected by repository policy.

`boto3` and `botocore` use the Apache-2.0 license. `boto3==1.43.51` is pinned; the PyPI wheel
SHA-256 is `a97057bb609fd38c80448db6a93db770786775dabd651401debf09816d4553d7`,
with the complete resolution retained in `uv.lock`.

## Supply-chain and runtime controls

- No static AWS access key is accepted; ECS task roles are the only runtime credential route.
- Network retries are standard-mode and bounded. Application-level publication never retries a
  pointer conflict as an overwrite.
- Immutable snapshot keys use `IfNoneMatch="*"`; a collision is accepted only when a full GET
  is byte-identical. `current.json` uses an ETag CAS and accepts a concurrent winner only when
  its exact bytes match.
- Every local document passes schema validation, cross-document validation and the public-data
  scanner before the first PUT. Every uploaded asset and manifest is fetched in full and
  revalidated before the pointer switch.
- Snapshot assets use one-year immutable caching; `current.json` uses `no-store` and zero cache
  lifetime. Daily refresh never runs whole-bucket sync, delete or whole-site invalidation.
- The runtime image remains non-root and read-only. Migration/bootstrap uses the RDS-managed
  master credential only in a separate task definition; scheduled refresh tasks receive only
  the generated least-privilege application credential.

## Residual limits

Calendar correctness still depends on the pinned library's embedded schedule and must be
compared with official XSHG announcements during operations. The temporary official-page
discovery sources remain `DEV_ONLY / UNVERIFIED / DISCOVERY`; source qualification and formal
provider authorization are deliberately deferred.
