"""Regression tests for reproducible, cache-friendly runtime image builds."""

from __future__ import annotations

from pathlib import Path

from tests.integration.runtime_support import create_full_workspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DOCKERFILE = REPOSITORY_ROOT / "deploy" / "docker" / "python.Dockerfile"
WEB_DOCKERFILE = REPOSITORY_ROOT / "deploy" / "docker" / "web.Dockerfile"


def test_python_dependencies_are_cached_before_project_sources() -> None:
    dockerfile = PYTHON_DOCKERFILE.read_text(encoding="utf-8")

    metadata_copy = dockerfile.index("COPY pyproject.toml uv.lock ./")
    dependency_sync = dockerfile.index(
        "uv sync --frozen --no-dev --extra data --no-install-project"
    )
    project_copy = dockerfile.index("COPY src ./src")
    project_sync = dockerfile.index("uv sync --frozen --no-dev --extra data --no-editable")

    assert metadata_copy < dependency_sync < project_copy < project_sync
    assert dockerfile.count("--mount=type=cache,target=/root/.cache/uv") == 2


def test_runtime_image_contains_one_shot_refresh_code_and_migrations() -> None:
    dockerfile = PYTHON_DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY apps ./apps" in dockerfile
    assert "COPY configs ./configs" in dockerfile
    assert "COPY resources ./resources" in dockerfile
    assert "COPY infra ./infra" in dockerfile
    assert "COPY alembic.ini ./" in dockerfile
    assert dockerfile.index("COPY apps ./apps") < dockerfile.index("USER app")


def test_full_runtime_workspace_contains_required_research_resources(tmp_path: Path) -> None:
    workspace = create_full_workspace(tmp_path / "runtime-resources")

    assert (workspace / "resources/research_methods/ai-berkshire/adapted/methods.v1.json").is_file()
    assert (workspace / "resources/research_sources/web-allowlist.v1.json").is_file()
    assert (workspace / "resources/instruments/ashare-catalog.dev.v1.json.gz").is_file()


def test_web_builder_copies_contract_inputs_before_build_without_shipping_them() -> None:
    dockerfile = WEB_DOCKERFILE.read_text(encoding="utf-8")
    builder_start = dockerfile.index(" AS builder")
    runner_start = dockerfile.index(" AS runner")
    builder = dockerfile[builder_start:runner_start]
    runner = dockerfile[runner_start:]

    web_copy = builder.index("COPY apps/web ./")
    schema_copy = builder.index("COPY schemas /schemas")
    network_guard_copy = builder.index(
        "COPY scripts/deny_node_network.cjs /scripts/deny_node_network.cjs"
    )
    build = builder.index("RUN npm run build")

    assert web_copy < schema_copy < network_guard_copy < build
    assert builder.count("COPY schemas /schemas") == 1
    assert builder.count("COPY scripts/deny_node_network.cjs /scripts/deny_node_network.cjs") == 1
    assert "/schemas" not in runner
    assert "deny_node_network.cjs" not in runner


def test_full_runtime_workspace_includes_only_required_root_script(
    tmp_path: Path,
) -> None:
    workspace = create_full_workspace(tmp_path / "runtime")
    scripts = workspace / "scripts"

    assert [path.name for path in scripts.iterdir()] == ["deny_node_network.cjs"]
    assert (scripts / "deny_node_network.cjs").read_bytes() == (
        REPOSITORY_ROOT / "scripts/deny_node_network.cjs"
    ).read_bytes()
