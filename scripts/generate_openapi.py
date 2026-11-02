#!/usr/bin/env python3
"""Generate the canonical FastAPI OpenAPI snapshot in an isolated process."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Final, cast

from pydantic import BaseModel

from stock_assistant.contracts.public_snapshots import (
    PublicEvidenceIndexV1,
    PublicRunStatusV1,
    PublicRunStatusV1_1,
    PublicSnapshotManifestV1_1,
    PublicSnapshotPointerV1,
)
from stock_assistant.contracts.security import RuntimeRealWebConfig, RuntimeWebConfig

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
OUTPUT_PATH: Final = PROJECT_ROOT / "schemas/http/openapi.v1.json"
OPENAPI_PATH: Final = OUTPUT_PATH
TYPE_GENERATION_CWD: Final = PROJECT_ROOT / "apps/web"
REDOCLY_FILENAMES: Final = (
    "redocly.yaml",
    "redocly.yml",
    "redocly.json",
    ".redocly.yaml",
    ".redocly.yml",
    ".redocly.json",
)

ISOLATED_ENV: Final[dict[str, str]] = {
    "APP_ENV": "test",
    "APP_NAME": "StockMarketAgent",
    "APP_VERSION": "0.1.0",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
    "PYTHONPATH": os.pathsep.join((str(PROJECT_ROOT), str(PROJECT_ROOT / "src"))),
    "TZ": "UTC",
    "WEB_ORIGIN": "http://127.0.0.1:3000",
}
RENDER_TIMEOUT_SECONDS: Final = 30
PUBLIC_STATIC_DOCUMENT_MODELS: Final[tuple[type[BaseModel], ...]] = (
    PublicSnapshotPointerV1,
    PublicSnapshotManifestV1_1,
    PublicEvidenceIndexV1,
    PublicRunStatusV1,
    PublicRunStatusV1_1,
    RuntimeWebConfig,
    RuntimeRealWebConfig,
)


class OpenApiGenerationError(RuntimeError):
    """Raised when the isolated application cannot render a valid document."""


def _walk(value: object, pointer: str = "") -> Iterator[tuple[str, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child_pointer = f"{pointer}/{escaped}"
            yield child_pointer, child
            yield from _walk(child, child_pointer)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_pointer = f"{pointer}/{index}"
            yield child_pointer, child
            yield from _walk(child, child_pointer)


def _redocly_candidates(type_generation_cwd: Path, project_root: Path) -> Iterator[Path]:
    current = type_generation_cwd.resolve()
    root = project_root.resolve()
    while True:
        for filename in REDOCLY_FILENAMES:
            yield current / filename
        if current == root:
            return
        if root not in current.parents:
            return
        current = current.parent


def validate_local_only(
    path: Path = OPENAPI_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
    type_generation_cwd: Path = TYPE_GENERATION_CWD,
) -> list[str]:
    """Return violations that could let type generation reach external resources."""

    errors: list[str] = []
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"OpenAPI snapshot is missing or invalid: {path}: {exc}"]

    for pointer, value in _walk(document):
        if pointer.endswith("/$ref") and (not isinstance(value, str) or not value.startswith("#")):
            try:
                display_path = path.relative_to(project_root)
            except ValueError:
                display_path = path
            errors.append(f"{display_path}{pointer} must be an internal '#...' reference")

    for candidate in _redocly_candidates(type_generation_cwd, project_root):
        if candidate.is_file():
            try:
                display_candidate = candidate.relative_to(project_root)
            except ValueError:
                display_candidate = candidate
            errors.append(
                "implicit Redocly configuration is forbidden for deterministic generation: "
                f"{display_candidate}"
            )
    return errors


def check_local_refs() -> int:
    """Validate the same local-only boundary before standalone type generation."""
    errors = validate_local_only()
    if errors:
        print("OpenAPI local-only validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("OpenAPI references are local-only and no implicit Redocly config exists")
    return 0


def _deny_network_audit(event: str, _arguments: tuple[object, ...]) -> None:
    """Fail closed if OpenAPI application import/rendering attempts network I/O."""

    if event.startswith("socket."):
        raise OpenApiGenerationError(
            f"network operation {event!r} is forbidden during OpenAPI generation"
        )


def _canonical_json(document: object) -> str:
    if not isinstance(document, dict):
        raise OpenApiGenerationError("FastAPI OpenAPI output must be a JSON object")
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _merge_public_static_document_schemas(document: dict[str, object]) -> None:
    """Add non-HTTP public document schemas from their canonical Pydantic models."""

    components = document.setdefault("components", {})
    if not isinstance(components, dict):
        raise OpenApiGenerationError("OpenAPI components must be an object")
    schemas = components.setdefault("schemas", {})
    if not isinstance(schemas, dict):
        raise OpenApiGenerationError("OpenAPI components.schemas must be an object")

    def merge(name: str, schema: object) -> None:
        current = schemas.get(name)
        if current is not None and current != schema:
            raise OpenApiGenerationError(f"OpenAPI schema component collision: {name}")
        schemas[name] = schema

    for model in PUBLIC_STATIC_DOCUMENT_MODELS:
        rendered = cast(
            dict[str, object],
            model.model_json_schema(
                mode="serialization",
                ref_template="#/components/schemas/{model}",
            ),
        )
        definitions = rendered.pop("$defs", {})
        if not isinstance(definitions, dict):
            raise OpenApiGenerationError(f"{model.__name__} emitted invalid $defs")
        for name, schema in definitions.items():
            merge(str(name), schema)
        merge(model.__name__, rendered)


def _render_in_child() -> int:
    sys.addaudithook(_deny_network_audit)
    from apps.api.main import app

    document = cast(dict[str, object], app.openapi())
    _merge_public_static_document_schemas(document)
    sys.stdout.write(_canonical_json(document))
    return 0


def render_openapi() -> str:
    """Render OpenAPI without inheriting the caller environment or repository cwd."""

    with tempfile.TemporaryDirectory(prefix="sma-openapi-render-") as isolated_cwd:
        try:
            result = subprocess.run(  # noqa: S603 - fixed interpreter and local script
                [sys.executable, str(Path(__file__).resolve()), "--isolated-child"],
                cwd=isolated_cwd,
                env=ISOLATED_ENV,
                check=False,
                capture_output=True,
                text=True,
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenApiGenerationError(
                f"isolated OpenAPI rendering exceeded {RENDER_TIMEOUT_SECONDS} seconds"
            ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "isolated renderer returned no diagnostic"
        raise OpenApiGenerationError(f"isolated OpenAPI rendering failed: {detail}")
    try:
        document: object = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OpenApiGenerationError(f"isolated renderer returned invalid JSON: {exc}") from exc
    return _canonical_json(document)


def check_snapshot(path: Path = OUTPUT_PATH) -> int:
    """Fail when the checked-in OpenAPI snapshot differs from FastAPI."""

    expected = render_openapi()
    try:
        actual = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"OpenAPI snapshot is missing or unreadable: {path}: {exc}", file=sys.stderr)
        return 1
    if actual == expected:
        print("OpenAPI snapshot is up to date")
        return 0
    print("OpenAPI snapshot drift detected", file=sys.stderr)
    print("run: python scripts/generate_openapi.py --write", file=sys.stderr)
    return 1


def write_snapshot(path: Path = OUTPUT_PATH) -> None:
    """Write the deterministic OpenAPI snapshot."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_openapi(), encoding="utf-8")
    print(f"generated {path.relative_to(PROJECT_ROOT)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the checked-in snapshot")
    mode.add_argument("--check", action="store_true", help="fail when the snapshot has drifted")
    mode.add_argument("--check-local-refs", action="store_true", help="validate local type inputs")
    mode.add_argument("--isolated-child", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check_local_refs:
        return check_local_refs()
    if args.isolated_child:
        return _render_in_child()
    if args.write:
        write_snapshot()
        return 0
    return check_snapshot()


if __name__ == "__main__":
    raise SystemExit(main())
