# Third-party software, models and data

This is the repository's direct-use and evaluated-reference registry. `present` means the dependency or
development service exists in the current repository; `selected` means it is the one future integration target
but has not been introduced; `reference` means no runtime dependency or copied code; `rejected` means it must not
be introduced without a new reuse decision. Exact current Python/npm versions are resolved by `uv.lock` and
`apps/web/package-lock.json`; container entries record the exact tag and digest used by R0P. Future entries must be
pinned to a release/commit/revision and artifact hash before integration.

| Project | Status | Intended use | License initial check | Integration/rights gate | Code copied now |
|---|---|---|---|---|---|
| FastAPI | present | HTTP entrypoint | MIT | Project contracts remain the source of truth | No |
| Celery | present | bounded background tasks | BSD-3-Clause | Project idempotency/outbox; no business payload types from Celery | No |
| PostgreSQL | present | fact, audit and point-in-time system of record | PostgreSQL License | Authorized data and encrypted backup rules apply | No |
| pgvector/pgvector | present | episodic-memory candidate retrieval | PostgreSQL License | Similarity is not the final duplicate/fact decision | No |
| Redis server | removed from current runtime | historical R0 cache/broker baseline | version-dependent | Compatibility `REDIS_*` variable names do not authorize reintroducing the server | No |
| Valkey | present in R0P runtime; production adapter gated | cache, lock and Celery broker | BSD-3-Clause | `9.1.0-alpine@sha256:c9b77919daeba2c02ad954d0c844cc4e7142069d177b89c5fd771f405daf9e02`; recovery/failure contract still required | No |
| MinIO server | removed and rejected beyond historical R0 | historical S3 baseline | AGPL-3.0; upstream archived | Compatibility `MINIO_*` variable names and the Python client do not mean the server is deployed | No |
| SeaweedFS | present in R0P runtime; production adapter gated | S3-compatible evidence/artifact storage | Apache-2.0 | `4.40@sha256:52194fba4fecd0083c842158b3a902ba6e04a63619b2b0efcd08007bdb6a4602`; versioning, retention, checksum, backup/restore and failure spike remain | No |
| LangGraph | present | bounded chat/analysis state graphs | MIT | Fixed nodes/tools; no direct fact or notification writes | No |
| OpenAI Python SDK | present as exact runtime dependency | OpenAI-compatible DeepSeek HTTPS client | Apache-2.0 | `openai==2.46.0` from `uv.lock`; project Port, exact base/model allowlists, local schema validation and redacted logs remain authoritative | No |
| DeepSeek API | selected external service | R2 structured narrative, evidence support assessment and six-dimension rubric judgment; deterministic reader compilation | service terms; no model weights distributed | Worker-only Secrets Manager injection; public/redacted evidence only; identity and private data excluded; deterministic evidence, score and publication gates remain authoritative | No |
| xbtlin/ai-berkshire | vendored provenance reference at `fd83d06347c6e3ee50133cda6962f40e226b5252` | 20 adapted analysis methods plus one investment-memo reader layer | MIT | All 21 upstream skill texts and the memo text are hash-retained for provenance; `wechat-article` remains provenance-only. Runtime loads only project-owned typed profiles with action, allocation, future-price and authority-endorsement behavior removed | No |
| Tavily Search API | selected external service | URL discovery after an explicit built-in data gap | service terms; no SDK dependency | Advanced search, at most five results, no answer/images/raw content; discovery output is not evidence and every selected page passes separate rights, SSRF, robots, PIT and content gates | No |
| CNINFO public annual-report index | adapted in R2 private preview | code-bound initial identity/business document discovery | Public data terms separate; DEV_ONLY/UNVERIFIED; AKShare MIT API reference only | Existing registered CNINFO domains; governed HTTPS/robots/DNS, one page up to five; no login/signature/secret; separate original PDF and independent audit | No |
| Qwen/Qwen3.5-9B | selected | local base model | Apache-2.0 model card | Pin official revision/hash; weights stay outside Git | No weights in Git |
| Qwen Embedding/Reranker | selected | local retrieval and reranking | verify each model card | Pin exact card/revision/dimension; separate index version | No weights in Git |
| vllm-project/vllm | selected | local OpenAI-compatible serving | Apache-2.0 | GPU/SLO/structured-output/LoRA contract test | No |
| modelscope/ms-swift | selected | single LoRA/QLoRA training entrypoint | Apache-2.0 | Pin training image/commit; model and dataset rights separate | No |
| Hugging Face Transformers/PEFT/TRL | present as optional ML dependencies | ms-swift training foundation | Apache-2.0 | No custom trainer duplication; resolved versions recorded per run | No |
| microsoft/qlib | present as optional ML dependency | offline PIT quant research/backtest | MIT | No real execution; API runtime does not import it | No |
| LightGBM | present as optional ML dependency | price/text models and quantiles | MIT | Time split, OOF and calibration tests | No |
| scikit-learn | present as optional ML dependency | regularized fusion and calibration | BSD-3-Clause | Fixed version/seed; separate calibration window | No |
| Polars | present as optional data dependency | rolling, cross-sectional percentile and Parquet batch processing | MIT | Adapter/offline use only; project formula and PIT rules remain authoritative | No |
| TA-Lib Python | selected, not yet installed | standard technical primitives and golden-vector comparison | BSD-2-Clause | Pin release/wheel hash; adapter boundary; verify native artifact and fixed samples before use | No |
| statsmodels | reference, not installed | rolling regression and HAC/Newey–West method validation | BSD-3-Clause | Offline reference only; no user signal from p-values | No |
| stumpy-dev/STUMPY | reference, not installed | offline matrix-profile motif/discord research | BSD-3-Clause | PIT, performance and incremental-value gates before any adoption | No |
| deepcharles/ruptures | reference, not installed | offline change-point research | BSD-2-Clause | Prefix-only PIT input and revision semantics required before product use | No |
| blue-yonder/tsfresh | reference, not installed | offline candidate-feature discovery | MIT | Multiple-testing/redundancy gate; never auto-publish extracted features | No |
| MLflow | present as optional ML dependency | experiment/model/prompt metadata | Apache-2.0 | Use aliases/tags, not deprecated stages; no licensed raw data in metadata | No |
| DVC | selected | dataset/artifact pointers and reproducible pipelines | Apache-2.0 | Git stores pointers/manifests only; rights registry remains authoritative | No |
| Trafilatura | selected | authorized HTML main-content extraction | Apache-2.0 for selected release | Parser does not fetch; pin a verified permissive release | No |
| pypdf | adopted runtime dependency | bounded text-only annual report PDF extraction | BSD-3-Clause | `pypdf==6.18.0`, exact wheel/sdist hashes in `uv.lock`; no OCR, encrypted/scanned input fails closed; child process has no credentials and bounded resources | No |
| Docling | selected | PDF/Office/XBRL parsing | MIT | Individual OCR/VLM model license; local-only authorized documents | No |
| PaddleOCR | selected fallback | scanned Chinese document OCR | Apache-2.0 code | Model/data license and numeric OCR gold test | No |
| RapidFuzz | selected | bounded title/entity fuzzy matching | MIT | Candidate matching only; deterministic thresholds/version | No |
| datasketch | selected | MinHash/LSH near-duplicate candidate retrieval | MIT | Candidate retrieval only; serialized index/version controlled | No |
| Apache ECharts | selected | breadth, distribution and data-quality charts | Apache-2.0 | No K-line role; pin npm version; frontend never recomputes backend business rules | No |
| TradingView Lightweight Charts | selected | sole OHLCV, volume and intraday financial chart renderer | Apache-2.0 | Preserve required TradingView attribution and link; render backend-calculated annotations only | No |
| shadcn/ui | selected | maintainable local UI primitives | MIT | Imported component source becomes project-maintained; no business logic copy | No |
| Apprise | selected | optional outbound notification adapter | BSD-2-Clause | Transport allowlist, secret provider, minimal payload, idempotency | No |
| OpenTelemetry | selected | traces and metric context | Apache-2.0 | Canonical metric registry and sensitive-attribute filtering | No |
| Prometheus client | present | runtime metric export | Apache-2.0 | Metrics must be registered in `governance/metrics.json` | No |
| openapi-typescript | present as exact dev dependency | generate deterministic frontend types from the repository OpenAPI snapshot | MIT | `7.13.0`; npm integrity `sha512-EFP392gcqXS7ntPvbhBzbF8TyBA+baIYEm791Hy5YkjDYKTnk/Tn5OQeKm5BIZvJihpp8Zzr4hzx0Irde1LNGQ==`; local-reference/config scanner plus Node no-network preload required; `--check` prevents drift | No |
| Playwright | present as exact Web development dependency | local browser acceptance testing for the public static R1 demo | Apache-2.0 | `@playwright/test@1.62.1`; Node `>=20`; browser binaries remain local build artifacts and tests must not access real accounts or unauthorized sources | No |
| oidc-client-ts | present as exact Web runtime dependency | Cognito Authorization Code with PKCE and access-token lifecycle | Apache-2.0 | `3.5.0`; public client only, session storage, no client secret, exact runtime endpoints and callback allowlist | No |
| react-markdown | present as exact Web runtime dependency | render server-compiled reader Markdown | MIT | `10.1.0`; raw HTML is disabled, images and unsafe link protocols are rejected before rendering | No |
| remark-gfm | present as exact Web runtime dependency | tables and standard reader-document formatting | MIT | `4.0.1`; used only with the locked Markdown renderer and server-compiled content | No |
| PyJWT | present as exact Python runtime dependency | strict Cognito access-token and JWKS verification | MIT | `2.13.0`; RS256 only, exact issuer/client/token_use/time checks, bounded HTTPS JWKS and no algorithm fallback | No |
| IBM Plex Sans / IBM Plex Mono | present as self-hosted Fontsource packages | Latin UI and evidence metadata typography | OFL-1.1 fonts; Fontsource package tooling MIT | `5.3.0`; locked npm integrity, local font files and OFL text; no font CDN | No |
| Noto Sans SC | present as self-hosted Fontsource package | Simplified Chinese report typography | OFL-1.1 font; Fontsource package tooling MIT | `5.3.0`; locked npm integrity, local font files and OFL text; no font CDN | No |
| oasdiff | selected and digest-pinned for R0P CI integration | detect breaking changes between repository OpenAPI snapshots | Apache-2.0 | `tufin/oasdiff:v1.25.1@sha256:3a0556fff855d1e1c91ebeedf4800772090cbdb4f319e0b4ee3be549bd5b5072`; local snapshot inputs only, no remote `$ref` | No |
| Hey API `@hey-api/openapi-ts` | rejected current default | no parallel frontend SDK/type generator | MIT | `0.99.0` remains pre-1.0 and generates a broader client/plugin surface than the static-type requirement | No |
| OpenAPI Generator | rejected current default | no multi-language SDK or template generator | Apache-2.0 | Java/template toolchain is unnecessary for the single TypeScript type artifact | No |
| OpenAPITools/openapi-diff | rejected current default | no second OpenAPI compatibility checker | Apache-2.0 | Java/Maven runtime duplicates the selected digest-pinned oasdiff role | No |
| Anchore Syft | selected | release SBOM generation | Apache-2.0 | Pin binary/container digest and retain SPDX/CycloneDX output | No |
| Anchore Grype | selected | image/filesystem vulnerability scan | Apache-2.0 | Pin scanner and vulnerability DB provenance; exceptions expire | No |
| Google OSV-Scanner | selected | lockfile vulnerability cross-check | Apache-2.0 | Pin release/hash and deduplicate findings with Grype | No |
| lm-evaluation-harness | selected supplementary | generic local-model regression | MIT | Finance gold/grounding gates remain project-owned | No |
| AKShare | present as optional data dependency | explicit 沪深北当前及沪深退市证券目录快照构建 | MIT code; exchange website content terms separate | `1.18.70`; builder validates official non-commercial download/retention records before network; runtime remains offline and `DEV_ONLY/UNVERIFIED` | No |
| BaoStock | present as exact optional data dependency | development-only A-share market-data cross-check outside the instrument-directory builder | BSD-3-Clause | `0.9.3`; SDK license does not authorize upstream market-data retention, redistribution, training or production use; no login/network in ordinary tests | No |
| QuantData Tushare-compatible research service | selected R1–R6 external data service; no new SDK dependency | fixed `daily` market route for `603986.SH` | service terms and upstream data rights separate; not represented as official Tushare | Exact HTTPS origin, POST shape, target/field allowlist, owner secret injection, 30 requests/minute project cap, `DEV_ONLY/UNVERIFIED`; R7 formal review required | No |
| ProMax Tushare-compatible research service | selected R1–R6 external data service; no new SDK dependency | `anns_d`, `income`, `news` and `npr` for the four non-market R1 categories | service terms and upstream data rights separate; not represented as official Tushare | Exact HTTPS origin/path, GET/header authentication, endpoint/field allowlist, no redirects, owner secret injection, 30 requests/minute project cap, `DEV_ONLY/UNVERIFIED`; R7 formal review required | No |
| Qinghong finance flash API | evaluated R1–R6 metadata source; dormant | `cninfo-latest`, `cls-telegraph` and `jin10` discovery metadata for `603986` | owner standard-tier account; upstream content rights separate | No SDK; exact route/field filters and `DEV_ONLY/UNVERIFIED`; documented gateway is HTTP-only, so activation requires a fixed HTTPS origin | No |
| Existing StockMarketAgent platform stack | present and adapted | R2 FastAPI/Pydantic contracts, PostgreSQL/SQLAlchemy UoW/outbox and existing event/evidence revision tables, Celery/Valkey, SeaweedFS S3 Ports, deterministic one-hop event matcher and Next.js shell | Apache-2.0 project code; dependencies separately listed and locked | No parallel event/graph database, framework or new dependency; global refresh contains no stock identity; production source/model gates remain fail-closed | Project-owned code only |
| AlphaEdu | reference | task entry, multi-perspective research, Bull/Bear, unknowns, falsification and source drilldown patterns | proprietary product and data | No code, data, prompts, brand assets or free-text contract copied; no runtime integration | No |
| TIKR Research Hub | reference | search-company then load-complete-research workflow | proprietary product and data | Workflow reference only; no content, data or runtime dependency | No |
| FinChat | reference | company search and research navigation | proprietary product and data | Workflow reference only; no content, data or runtime dependency | No |
| Koyfin Equity Research | reference | company-centred research workspace | proprietary product and data | Workflow reference only; no content, data or runtime dependency | No |
| AlphaSense | reference | evidence/source drilldown interaction principle | proprietary product and data | Public product reference only; no interface, content, data, prompt or runtime dependency copied | No |
| Quartr Pro | reference | primary-source navigation and citation drilldown principle | proprietary product and data | Public product reference only; no interface, content, data or runtime dependency copied | No |
| Simply Wall St | reference | progressive disclosure principle | proprietary product and data | Public product reference only; no visual language, chart, content or runtime dependency copied | No |
| Apple Human Interface Guidelines | reference | short interruptible motion, restrained materials and reduced-motion behavior | Apple reference documentation | Principles only; no Apple assets, components, marks or interface copied | No |
| ZhuLinsen/daily_stock_analysis | reference at v3.27.0 / `b36c721` | primary application-flow reference | MIT | New fixed-commit file audit before any adaptation | No |
| ZhiweiChen-coder/OpenAshare | reference at v0.1.0 / `fed8a10` | local-first A-share workspace UX | MIT | Pre-release; no data, memory or domain code copied | No |
| HKUDS/Vibe-Trading | reference at v0.1.11 / `835dea7` | Agent, PIT and research-workflow patterns | MIT | Exclude broker, order, shell and unrestricted tool paths | No |
| ZhuLinsen/alphasift | reference; adapter spike candidate at `9f522747caaf` | sector/hotspot schema and fallback patterns | Apache-2.0 | Production data rights and strict PIT remain project-owned | No |
| TauricResearch/TradingAgents | reference at v0.3.1 / `01477f9` | agent roles/checkpoint/provider patterns | Apache-2.0 | No direct facts, data assumptions or trading behavior | No |
| frinkleko/FinReport | reference, archived demo | factor/report and risk-module research | MIT | Demo-only and archived; no production code or data copied | No |
| pipiku915/FinMem-LLM-StockTrading | reference | layered-memory research design | MIT | No trading execution or performance claims | No |
| AI4Finance-Foundation/FinRobot | reference | finance-task/report evaluation ideas | Apache-2.0 | No notebook/data copy without separate rights | No |
| AI4Finance-Foundation/FinGPT | reference | finance task/data research | MIT code; datasets/models separate | Per-dataset/model card audit required | No |
| hsliuping/TradingAgents-CN | rejected | no product-base use | mixed; proprietary app/frontend | Do not copy restricted directories | No |
| OpenBB | rejected runtime | provider-platform reference only | AGPL-3.0 | New legal/reuse decision required | No |
| Open WebUI | rejected runtime | no parallel chat shell | license includes additional branding conditions | Duplicates identity, plugin and data boundaries | No |
| Aqua Trivy | rejected current default | no scanner/action use pending trust re-review | Apache-2.0 code; 2026 ecosystem compromise | Require a new supply-chain decision before use | No |
| vectorbt | rejected | no backtest dependency | Apache-2.0 with Commons Clause | Source-available restriction and duplicate Qlib role | No |
| pandas-ta | rejected current default | no parallel technical-indicator dependency | license/release path requires renewed audit | Duplicates selected TA-Lib role; re-evaluate only with clear permissive release | No |
| bukosabino/ta | rejected current default | no parallel technical-indicator dependency | MIT | Duplicates TA-Lib and its fill-NaN workflow conflicts with project abstention semantics | No |
| KLineChart | rejected current default | no second financial chart/indicator engine | Apache-2.0 | Bundled frontend indicator calculations would create a second business-rule source | No |
| exchange_calendars | present as exact runtime dependency | XSHG session generation behind TradingCalendarPort | Apache-2.0 | `4.13.2`; wheel hashes are locked in `uv.lock`; official exchange calendars remain authoritative and adapter output remains point-in-time stamped | No |
| Boto3 / Botocore | present as exact runtime dependency | ECS task-role S3 snapshot publication and conditional pointer writes | Apache-2.0 | `boto3==1.43.51`; wheel hashes are locked in `uv.lock`; bounded SDK retries, request/response checksums, SSE-S3 and `IfMatch`/`IfNoneMatch` are mandatory; no static AWS keys | No |
| sdmx1 | reference/planning candidate, not installed | future SDMX protocol adapter for individually authorized providers | Apache-2.0 | Pin only after endpoint rights, payload contract and failure behavior are approved | No |
| GDELT | reference data source, disabled | offline geopolitical/event candidate discovery | data terms require per-use review | Never establishes a company fact or exposure; no production ingestion now | No |
| OpenSanctions | reference, disabled | sanctions data-model reference and candidate discovery | code/data rights differ; business data use needs separate license review | No aggregated production data without explicit permission | No |
| ACLED | rejected default | no production conflict-event dependency | proprietary/commercial data terms | New rights and product-fit decision required | No |
| yfinance | rejected production | no overseas-market fallback | Apache-2.0 code; upstream data rights separate/restricted | SDK license does not grant retention, derivation or display rights | No |
| FRED API | rejected default | no default macro-data adapter | official service terms | Current software/AI-development and caching restrictions require separate written permission | No |
| Neo4j/standalone graph database | rejected current | no one-hop event graph store | product/version dependent | PostgreSQL bitemporal relations satisfy current scope | No |
| hudson-and-thames/mlfinlab | rejected | no financial-ML formula dependency | all rights reserved | Public issue repository is not a permissively licensed production library | No |
| Backtrader | rejected | no backtest dependency | GPL-3.0+ | Strong-copyleft review and duplicate Qlib role | No |
| MinIO new deployment | rejected | no R1 production use | AGPL-3.0; archived | SeaweedFS is the selected migration target | No |

Data from CNINFO, exchanges, government websites, Choice, research providers, media and social platforms is not
covered by this repository's license or by any SDK license above. Each action is governed by the corresponding
contract/site terms and the rights snapshot recorded in `configs/sources.yaml` and the runtime rights registry.

The 2026-09-14 R2 repair uses documentation as design references only:
[LlamaIndex document identity and caching](https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/),
[Unstructured original-element provenance](https://docs.unstructured.io/open-source/core-functionality/chunking),
and [LangGraph checkpoint persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
No code, library, model weights, vector store or alternative executor is incorporated. Existing typed contracts,
PostgreSQL JSONB, source adapters and the worker retain ownership of execution and research boundaries.
