.DEFAULT_GOAL := help
UV ?= uv
NPM ?= npm
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= $(PYTHON) -m ruff
MYPY ?= $(PYTHON) -m mypy
ALEMBIC ?= $(PYTHON) -m alembic
UV_CACHE_DIR ?= $(CURDIR)/.runtime/cache/uv
WORKSPACE_PYTHONPATH := $(CURDIR)/src
export PYTHONPATH := $(WORKSPACE_PYTHONPATH)$(if $(PYTHONPATH),:$(PYTHONPATH))

CONTRACT_BASE_REF ?= origin/main
BASE_REF ?=
WRITE_SCOPES ?=
TESTS ?=
PROMPT_FILE ?=
MODEL ?=
CODEX_BINARY ?= codex
TARGET_COMMIT ?=
SOURCE_BRANCH ?=
REVIEWED_COMMIT ?=
PRODUCT_SHA ?=
OLD_PRODUCT_SHA ?=
EXPECTED_AWS_ACCOUNT_ID ?=
PUBLIC_DEMO_CHANGE_SET_ARN ?=
PUBLIC_DEMO_PLAN_SHA256 ?=
PUBLIC_DEMO_CONFIRM ?=
PUBLIC_DEMO_EVIDENCE_ID ?=
DOT := .
PUBLIC_DEMO_REPORT_ROOT ?= $(CURDIR)/$(DOT)runtime/reports/public-demo
PUBLIC_DEMO_SNAPSHOT_DIR = $(PUBLIC_DEMO_REPORT_ROOT)/$(PRODUCT_SHA)
PUBLIC_DEMO_ARTIFACT_DIR ?= $(CURDIR)/apps/web/out
PUBLIC_DEMO_PLAN ?= $(PUBLIC_DEMO_REPORT_ROOT)/aws-plan-$(PRODUCT_SHA).json
PYTHON_PATHS := apps src tests ml scripts infra

# Public source acceptance is separate from the retained private-context integration gate.
.PHONY: public-check
public-check:
	test ! -e resources/instruments/ashare-catalog.dev.v1.json.gz
	test ! -e governance/delivery.json
	$(PYTHON) scripts/check_repository_policy.py
	$(PYTHON) scripts/check_data_rights.py
	$(PYTHON) scripts/check_architecture.py
	$(PYTHON) scripts/check_interface_registry.py
	$(PYTHON) scripts/check_metric_registry.py
	$(PYTHON) scripts/generate_contract_schemas.py --check
	$(PYTHON) scripts/generate_openapi.py --check-local-refs
	$(PYTHON) scripts/generate_openapi.py --check
	$(MAKE) lint-python typecheck
	$(PYTEST) tests/unit/test_demo_briefing.py tests/unit/test_project_tasks.py \
		tests/unit/test_event_contract.py tests/unit/test_event_comparison.py \
		tests/unit/test_boundary_contracts.py tests/unit/test_time.py \
		tests/unit/test_domain_contract_safety.py tests/unit/test_extended_contract_safety.py \
		tests/contract/test_source_rights.py tests/unit/dmi/test_source_rights_gate.py
	$(MAKE) demo

.PHONY: help doctor demo install install-ml format lint lint-python typecheck test test-unit \
	test-contract governance architecture-check contracts-generate contracts-check contract-compat \
	openapi-local-only openapi-generate openapi-schema-check openapi-compat api-types-check openapi-check \
	delivery-registry agent-run package-check agent-review integration-merge integration-check \
	interface-registry metric-registry repository-policy python-check \
	web-check compose-check verify-fast verify governance-local compose-up compose-down migrate \
	migration-test integration-test r1-database-test e2e-test parallel-isolation-test parallel-doctor workspace-up \
	workspace-status workspace-down workspace-destroy r1-functional-replay r1-free-live-smoke \
	public-demo-snapshot public-demo-build public-demo-test public-demo-infra-plan \
	public-demo-infra-deploy public-demo-upload public-demo-smoke \
	public-demo-upgrade-existing-shell

help:
	@$(PYTHON) -m scripts.project_tasks help

doctor:
	@$(PYTHON) -m scripts.project_tasks doctor

demo:
	@$(PYTHON) -m scripts.project_tasks demo

install:
	UV_CACHE_DIR="$(UV_CACHE_DIR)" $(UV) sync --frozen --extra dev --extra data
	$(NPM) --prefix apps/web ci

install-ml:
	UV_CACHE_DIR="$(UV_CACHE_DIR)" $(UV) sync --frozen --extra ml

lint: lint-python

lint-python:
	$(RUFF) check $(PYTHON_PATHS)
	$(RUFF) format --check $(PYTHON_PATHS)

typecheck:
	$(MYPY) apps src/stock_assistant scripts

format:
	$(RUFF) format $(PYTHON_PATHS)
	$(RUFF) check --fix $(PYTHON_PATHS)

test:
	$(PYTEST) --cov=stock_assistant --cov-report=term-missing

test-unit:
	$(PYTEST) tests/unit

test-contract:
	$(PYTEST) tests/contract

repository-policy:
	$(PYTHON) scripts/check_data_rights.py
	$(PYTHON) scripts/check_governance.py
	$(PYTHON) scripts/check_repository_policy.py

architecture-check:
	$(PYTHON) scripts/check_architecture.py

delivery-registry:
	$(PYTHON) scripts/check_delivery_registry.py

agent-run:
	@test -n "$(PROMPT_FILE)" || (echo "PROMPT_FILE is required" >&2; exit 2)
	@test -n "$(MODEL)" || (echo "MODEL is required" >&2; exit 2)
	$(PYTHON) -m scripts.lightweight_agent run \
		--prompt-file "$(PROMPT_FILE)" \
		--model "$(MODEL)" \
		--codex-binary "$(CODEX_BINARY)"

package-check:
	@test -n "$(BASE_REF)" || (echo "BASE_REF is required" >&2; exit 2)
	@test -n "$(WRITE_SCOPES)" || (echo "WRITE_SCOPES is required" >&2; exit 2)
	@test -n "$(TESTS)" || (echo "TESTS is required" >&2; exit 2)
	$(PYTHON) scripts/package_check.py \
		--base-ref "$(BASE_REF)" \
		$(foreach scope,$(WRITE_SCOPES),--write-scope "$(scope)") \
		$(foreach test,$(TESTS),--test "$(test)")

agent-review:
	@test -n "$(BASE_REF)" || (echo "BASE_REF is required" >&2; exit 2)
	@test -n "$(TARGET_COMMIT)" || (echo "TARGET_COMMIT is required" >&2; exit 2)
	@test -n "$(PROMPT_FILE)" || (echo "PROMPT_FILE is required" >&2; exit 2)
	@test -n "$(MODEL)" || (echo "MODEL is required" >&2; exit 2)
	$(PYTHON) -m scripts.lightweight_agent review \
		--base-ref "$(BASE_REF)" \
		--target-commit "$(TARGET_COMMIT)" \
		--prompt-file "$(PROMPT_FILE)" \
		--model "$(MODEL)" \
		--codex-binary "$(CODEX_BINARY)"

integration-merge:
	@test -n "$(SOURCE_BRANCH)" || (echo "SOURCE_BRANCH is required" >&2; exit 2)
	@test -n "$(REVIEWED_COMMIT)" || (echo "REVIEWED_COMMIT is required" >&2; exit 2)
	$(PYTHON) scripts/integration_release.py merge \
		--source-branch "$(SOURCE_BRANCH)" \
		--reviewed-commit "$(REVIEWED_COMMIT)"

interface-registry:
	$(PYTHON) scripts/check_interface_registry.py

metric-registry:
	$(PYTHON) scripts/check_metric_registry.py

contracts-generate:
	$(PYTHON) scripts/generate_contract_schemas.py --write

contracts-check:
	$(PYTHON) scripts/generate_contract_schemas.py --check

contract-compat:
	$(PYTHON) scripts/check_contract_compatibility.py --base-ref "$(CONTRACT_BASE_REF)"

openapi-local-only:
	$(PYTHON) scripts/generate_openapi.py --check-local-refs

openapi-generate:
	$(PYTHON) scripts/generate_openapi.py --write
	$(PYTHON) scripts/generate_openapi.py --check-local-refs
	$(NPM) --prefix apps/web run api-types:generate

openapi-schema-check: openapi-local-only
	$(PYTHON) scripts/generate_openapi.py --check

openapi-compat:
	$(PYTHON) scripts/check_openapi_compatibility.py --base-ref "$(CONTRACT_BASE_REF)"

api-types-check: openapi-local-only
	$(NPM) --prefix apps/web run api-types:check

openapi-check: openapi-schema-check openapi-compat api-types-check

governance: repository-policy architecture-check delivery-registry interface-registry metric-registry contracts-check contract-compat openapi-schema-check openapi-compat

governance-local: repository-policy architecture-check delivery-registry interface-registry metric-registry contracts-check contract-compat openapi-schema-check

python-check: lint-python typecheck test

web-check: api-types-check
	$(NPM) --prefix apps/web run lint
	$(NPM) --prefix apps/web run typecheck
	node -e "require('node:fs').rmSync('apps/web/.next', { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })"
	$(NPM) --prefix apps/web run build
	$(NPM) --prefix apps/web run test:e2e:research

compose-check:
	COMPOSE_DISABLE_ENV_FILE=1 docker compose -f deploy/compose.yaml config --quiet
	COMPOSE_DISABLE_ENV_FILE=1 docker compose -f deploy/compose.yaml \
		-f deploy/compose.migration-test.yaml config --quiet

verify-fast: governance python-check

verify: verify-fast web-check compose-check migration-test integration-test r1-database-test e2e-test parallel-isolation-test

integration-check: verify

compose-up: workspace-up

compose-down: workspace-down

migrate:
	$(ALEMBIC) upgrade head

migration-test:
	$(PYTHON) scripts/workspace_runtime.py migration-test

integration-test:
	SMA_RUN_DOCKER_INTEGRATION=1 $(PYTEST) tests/integration/test_foundation_services.py
	$(PYTHON) scripts/workspace_runtime.py migration-test --suite recovery

r1-database-test:
	@set -eu; \
		project_name="$$($(PYTHON) -c 'from pathlib import Path; from scripts.workspace_runtime import migration_project_name; print(migration_project_name(Path.cwd()))')"; \
		compose() { COMPOSE_DISABLE_ENV_FILE=1 docker compose -p "$$project_name" -f deploy/compose.yaml -f deploy/compose.migration-test.yaml "$$@"; }; \
		if [ -n "$$(compose ps -aq)" ]; then \
			echo "R1 database test project already has resources; refusing reuse" >&2; \
			exit 1; \
		fi; \
		started=1; \
		cleanup() { if [ "$$started" -eq 1 ]; then compose down --volumes --remove-orphans; fi; }; \
		trap cleanup EXIT HUP INT TERM; \
		compose up --detach --wait --wait-timeout 180 postgres; \
		compose exec -T postgres psql -U stock_assistant -d postgres -v ON_ERROR_STOP=1 \
			-c "CREATE DATABASE stock_assistant_active_target_test"; \
		binding="$$(compose port postgres 5432)"; \
		case "$$binding" in 127.0.0.1:[0-9]*) ;; *) echo "R1 test PostgreSQL is not loopback-only: $$binding" >&2; exit 1 ;; esac; \
		port="$${binding##*:}"; \
			ACTIVE_TARGET_TEST_DATABASE_URL="postgresql+asyncpg://stock_assistant:synthetic-local-only@127.0.0.1:$${port}/stock_assistant_active_target_test" \
				$(PYTEST) tests/integration/test_active_target_repository.py \
					tests/integration/test_research_repository.py \
					tests/integration/test_market_event_repository.py; \
		R1_CURSOR_TEST_DATABASE_URL="postgresql+asyncpg://stock_assistant:synthetic-local-only@127.0.0.1:$${port}/stock_assistant_r0p_test" \
			$(PYTEST) tests/integration/test_r1_source_cursor_cas.py; \
		cleanup; \
		started=0; \
		trap - EXIT HUP INT TERM; \
		test -z "$$(compose ps -aq)"

e2e-test:
	SMA_RUN_DOCKER_E2E=1 $(PYTEST) tests/e2e/test_runtime_smoke.py

r1-functional-replay:
	SMA_RUN_R1_FUNCTIONAL_REPLAY=1 $(PYTEST) \
		tests/e2e/test_runtime_smoke.py::test_five_session_replay_is_persisted_and_idempotent

r1-free-live-smoke:
	@test "$(ENABLE_DEV_LIVE)" = "1" || \
		(echo "ENABLE_DEV_LIVE=1 is required" >&2; exit 2)
	@test "$(TARGET_INSTRUMENT)" = "XSHG:603986" || \
		(echo "TARGET_INSTRUMENT=XSHG:603986 is required" >&2; exit 2)
	SMA_RUN_R1_FREE_LIVE_SMOKE=1 ENABLE_DEV_LIVE=1 TARGET_INSTRUMENT=XSHG:603986 \
		$(PYTEST) tests/e2e/test_runtime_smoke.py::test_explicit_free_live_smoke_has_no_replay_fallback

public-demo-snapshot:
	@test -n "$(PRODUCT_SHA)" || (echo "PRODUCT_SHA is required" >&2; exit 2)
	@test "$(PRODUCT_SHA)" = "$$(git rev-parse HEAD)" || \
		(echo "PRODUCT_SHA must equal the final HEAD" >&2; exit 2)
	SMA_RUN_R1_FUNCTIONAL_REPLAY=1 SMA_PUBLIC_DEMO_PRODUCT_SHA="$(PRODUCT_SHA)" \
		$(PYTEST) tests/e2e/test_runtime_smoke.py::test_five_session_replay_is_persisted_and_idempotent
	$(PYTHON) -m scripts.public_demo_artifacts scan --kind snapshot \
		--root "$(PUBLIC_DEMO_SNAPSHOT_DIR)"

public-demo-build:
	@test -n "$(PRODUCT_SHA)" || (echo "PRODUCT_SHA is required" >&2; exit 2)
	@test "$(PRODUCT_SHA)" = "$$(git rev-parse HEAD)" || \
		(echo "PRODUCT_SHA must equal the final HEAD" >&2; exit 2)
	$(PYTHON) -m scripts.public_demo_artifacts build \
		--repository-root "$(CURDIR)" \
		--snapshot-dir "$(PUBLIC_DEMO_SNAPSHOT_DIR)" \
		--product-sha "$(PRODUCT_SHA)"

public-demo-test:
	@test -n "$(PRODUCT_SHA)" || (echo "PRODUCT_SHA is required" >&2; exit 2)
	@test "$(PRODUCT_SHA)" = "$$(git rev-parse HEAD)" || \
		(echo "PRODUCT_SHA must equal the final HEAD" >&2; exit 2)
	$(PYTHON) -m scripts.public_demo_artifacts verify \
		--snapshot-dir "$(PUBLIC_DEMO_SNAPSHOT_DIR)" \
		--artifact-dir "$(PUBLIC_DEMO_ARTIFACT_DIR)" \
		--product-sha "$(PRODUCT_SHA)"
	cd apps/web && $(NPM) exec -- playwright test \
		--config=../../tests/e2e/qir/playwright.config.ts

public-demo-infra-plan:
	@test -n "$(EXPECTED_AWS_ACCOUNT_ID)" || \
		(echo "EXPECTED_AWS_ACCOUNT_ID is required" >&2; exit 2)
	@test -n "$(PRODUCT_SHA)" || (echo "PRODUCT_SHA is required" >&2; exit 2)
	$(PYTHON) -m scripts.public_demo_aws --expected-account-id "$(EXPECTED_AWS_ACCOUNT_ID)" plan \
		--product-sha "$(PRODUCT_SHA)" \
		--template "$(CURDIR)/deploy/aws/public-demo.yaml" \
		--output "$(PUBLIC_DEMO_PLAN)"

public-demo-infra-deploy:
	@test -n "$(PUBLIC_DEMO_CHANGE_SET_ARN)" || \
		(echo "PUBLIC_DEMO_CHANGE_SET_ARN is required" >&2; exit 2)
	@test -n "$(PUBLIC_DEMO_PLAN_SHA256)" || \
		(echo "PUBLIC_DEMO_PLAN_SHA256 is required" >&2; exit 2)
	$(PYTHON) -m scripts.public_demo_aws --expected-account-id "$(EXPECTED_AWS_ACCOUNT_ID)" deploy \
		--plan "$(PUBLIC_DEMO_PLAN)" \
		--change-set-arn "$(PUBLIC_DEMO_CHANGE_SET_ARN)" \
		--plan-sha256 "$(PUBLIC_DEMO_PLAN_SHA256)" \
		--confirm "$(PUBLIC_DEMO_CONFIRM)" \
		--template "$(CURDIR)/deploy/aws/public-demo.yaml"

public-demo-upload:
	$(PYTHON) -m scripts.public_demo_artifacts scan --kind artifact \
		--root "$(PUBLIC_DEMO_ARTIFACT_DIR)"
	$(PYTHON) -m scripts.public_demo_aws --expected-account-id "$(EXPECTED_AWS_ACCOUNT_ID)" upload \
		--product-sha "$(PRODUCT_SHA)" \
		--artifact-dir "$(PUBLIC_DEMO_ARTIFACT_DIR)" \
		--confirm "$(PUBLIC_DEMO_CONFIRM)"

public-demo-upgrade-existing-shell:
	@test -n "$(OLD_PRODUCT_SHA)" || (echo "OLD_PRODUCT_SHA is required" >&2; exit 2)
	@test -n "$(PRODUCT_SHA)" || (echo "PRODUCT_SHA is required" >&2; exit 2)
	$(PYTHON) -m scripts.public_demo_aws --expected-account-id "$(EXPECTED_AWS_ACCOUNT_ID)" \
		upgrade-existing-shell \
		--old-product-sha "$(OLD_PRODUCT_SHA)" \
		--new-product-sha "$(PRODUCT_SHA)" \
		--repository-root "$(CURDIR)" \
		--snapshot-dir "$(PUBLIC_DEMO_SNAPSHOT_DIR)" \
		--confirm "$(PUBLIC_DEMO_CONFIRM)"

public-demo-smoke:
	@test -n "$(PUBLIC_DEMO_EVIDENCE_ID)" || \
		(echo "PUBLIC_DEMO_EVIDENCE_ID is required" >&2; exit 2)
	$(PYTHON) -m scripts.public_demo_aws --expected-account-id "$(EXPECTED_AWS_ACCOUNT_ID)" smoke \
		--product-sha "$(PRODUCT_SHA)" \
		--evidence-id "$(PUBLIC_DEMO_EVIDENCE_ID)"

parallel-isolation-test:
	SMA_RUN_DOCKER_PARALLEL_ISOLATION=1 $(PYTEST) \
		tests/integration/test_parallel_workspace_isolation.py

parallel-doctor:
	$(PYTHON) scripts/workspace_runtime.py doctor

workspace-up:
	$(PYTHON) scripts/workspace_runtime.py up

workspace-status:
	$(PYTHON) scripts/workspace_runtime.py status

workspace-down:
	$(PYTHON) scripts/workspace_runtime.py down

workspace-destroy:
	$(PYTHON) scripts/workspace_runtime.py destroy
