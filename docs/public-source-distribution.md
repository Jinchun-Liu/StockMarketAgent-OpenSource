# Public source distribution

This publication starts a new Git history from the committed source snapshot dated
2026-11-02. It does not include the upstream private repository history or its 26
uncommitted product files. The upstream private repository and its history are retained.

## Excluded materials

- The restricted A-share directory `resources/instruments/ashare-catalog.dev.v1.json.gz`.
- The private `governance/delivery.json` authorization/deployment ledger.
- Private task, handoff, verification, operations and consolidated delivery records.
- Private host hook settings and the upstream context-bound CI workflow.
- Credentials, actual .env files, holdings, private reports, raw research data, model weights
  and build/dependency caches.

The generic application, adapters, schemas, migrations, tests and reusable infrastructure
templates are source code; publishing a template does not deploy it or grant account access.
Upstream private-context governance scripts remain unchanged as reference implementations.
They require their original context and are not the public distribution acceptance gate.
No active owner authorization record is supplied or inherited by a clone.

## Local data preparation

The excluded catalog was collected under private non-commercial FETCH/RETAIN permissions,
with `redistribution_allowed: false` in configs/sources.yaml. Public source licensing does
not alter these rights. Do not upload that file, including through Git history or CI artifacts.

The builder in `scripts/build_ashare_instrument_catalog.py` is retained. Review its help,
source terms, configured rights and your permitted local use before explicitly generating a
catalog. The builder's network/rights checks must not be bypassed. Ordinary tests and
onboarding must not fetch exchange data automatically. Keep a generated catalog untracked.

The exact full-catalog validation and data-dependent tests remain unchanged. They need a
lawful catalog with the expected source receipts; they are not replaced with fabricated
market data, weaker counts or a silent live-data fallback. The deterministic demo does not
need this catalog and labels its original synthetic evidence explicitly.

## What public CI proves

`make public-check` checks distribution exclusions, repository policy, registered source
rights, architecture, interface/metric registries, generated schemas, Python lint/types,
selected offline contract/evidence/rights regressions, and the documented demo.

It does not claim that the private source-context governance gate, the entire Python suite,
Web/Docker product E2E, live-source acquisition, external models or production deployment
has passed for this distribution. Those tests and commands are retained for owners who
provide their required legitimate data and environment. No thresholds or tests are removed.
