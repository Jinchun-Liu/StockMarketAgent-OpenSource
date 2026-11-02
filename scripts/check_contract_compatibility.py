#!/usr/bin/env python3
"""Reject cross-commit rewrites of published schemas, migrations and frozen Ports."""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath


class CompatibilityCheckError(ValueError):
    """Raised when the comparison surface cannot be read safely."""


GIT_BINARY = shutil.which("git")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    if GIT_BINARY is None:
        raise CompatibilityCheckError("git executable is required for compatibility checks")
    return subprocess.run(  # noqa: S603 - fixed Git executable with shell disabled
        [GIT_BINARY, *args],
        cwd=root,
        check=False,
        capture_output=True,
    )


def _git_bytes(root: Path, base_ref: str, path: str) -> bytes:
    result = _git(root, "show", f"{base_ref}:{path}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CompatibilityCheckError(f"cannot read {path} from {base_ref}: {detail}")
    return result.stdout


def _optional_git_bytes(root: Path, base_ref: str, path: str) -> bytes | None:
    result = _git(root, "ls-tree", "--name-only", base_ref, "--", path)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CompatibilityCheckError(f"cannot inspect {path} in {base_ref}: {detail}")
    listed = {item for item in result.stdout.decode("utf-8", errors="replace").splitlines() if item}
    if path not in listed:
        return None
    return _git_bytes(root, base_ref, path)


def _base_paths(root: Path, base_ref: str, prefix: str) -> tuple[str, ...]:
    result = _git(root, "ls-tree", "-r", "--name-only", base_ref, "--", prefix)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise CompatibilityCheckError(f"cannot list {prefix} from {base_ref}: {detail}")
    return tuple(
        path
        for path in result.stdout.decode("utf-8").splitlines()
        if path and _safe_relative_path(path)
    )


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _load_json_bytes(raw: bytes, label: str) -> dict[str, object]:
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CompatibilityCheckError(f"{label} contains invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CompatibilityCheckError(f"{label} must contain a JSON object")
    return value


def _load_current_json(root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompatibilityCheckError(f"cannot read current {relative}: {exc}") from exc
    return _load_json_bytes(raw, relative)


def _object_items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _string(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) and value else None


def _active_execution(registry: dict[str, object]) -> object:
    return registry.get("active_execution")


def _first_freeze_is_authorized(
    base_delivery: dict[str, object],
    current_delivery: dict[str, object],
) -> bool:
    base_execution = _active_execution(base_delivery)
    current_execution = _active_execution(current_delivery)
    if base_execution is None and current_execution is None:
        return True
    if not isinstance(current_execution, dict):
        return False
    if current_execution.get("status") not in {
        "proposed",
        "contract_frozen",
        "implementing",
        "ready_for_integration",
        "integrating",
        "verified",
    }:
        return False
    lanes = _object_items(current_execution.get("lanes"))
    for lane in lanes:
        zones = lane.get("owns_single_writer_zones")
        if (
            lane.get("role") == "contract_steward"
            and lane.get("status")
            in {"implementing", "contract_frozen", "ready_for_integration", "verified"}
            and isinstance(zones, list)
            and "contract_boundary" in zones
        ):
            return True
    return False


def _semver(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    parts = value.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _annotation(node: ast.expr | None) -> str:
    return "" if node is None else ast.unparse(node)


def _argument(
    argument: ast.arg,
    *,
    kind: str,
    has_default: bool,
    default: ast.expr | None = None,
) -> tuple[str, str, str, str]:
    default_value = ""
    if has_default:
        default_value = "..." if default is None else ast.unparse(default)
    return kind, argument.arg, _annotation(argument.annotation), default_value


def _method_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, str, tuple[tuple[str, str, str, str], ...], str]:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    padding = len(positional) - len(arguments.defaults)
    positional_defaults: list[ast.expr | None] = [None] * padding + list(arguments.defaults)
    parameters: list[tuple[str, str, str, str]] = []
    for index, argument in enumerate(arguments.posonlyargs):
        default = positional_defaults[index]
        parameters.append(
            _argument(
                argument,
                kind="positional_only",
                has_default=default is not None,
                default=default,
            )
        )
    offset = len(arguments.posonlyargs)
    for index, argument in enumerate(arguments.args):
        default = positional_defaults[offset + index]
        parameters.append(
            _argument(
                argument,
                kind="positional_or_keyword",
                has_default=default is not None,
                default=default,
            )
        )
    if arguments.vararg is not None:
        parameters.append(_argument(arguments.vararg, kind="var_positional", has_default=False))
    for argument, default in zip(
        arguments.kwonlyargs,
        arguments.kw_defaults,
        strict=True,
    ):
        parameters.append(
            _argument(
                argument,
                kind="keyword_only",
                has_default=default is not None,
                default=default,
            )
        )
    if arguments.kwarg is not None:
        parameters.append(_argument(arguments.kwarg, kind="var_keyword", has_default=False))
    function_kind = "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"
    return node.name, function_kind, tuple(parameters), _annotation(node.returns)


def _port_signature(source: bytes, symbol: str, label: str) -> tuple[object, ...]:
    try:
        tree = ast.parse(source.decode("utf-8"), filename=label)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise CompatibilityCheckError(f"cannot parse Port source {label}: {exc}") from exc
    class_name = symbol.rsplit(".", maxsplit=1)[-1]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            methods = [
                _method_signature(member)
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            return tuple(sorted(methods, key=lambda item: item[0]))
    raise CompatibilityCheckError(f"{label} does not define {symbol}")


def _validate_published_migrations(root: Path, base_ref: str) -> list[str]:
    errors: list[str] = []
    try:
        paths = _base_paths(root, base_ref, "infra/migrations/versions")
    except CompatibilityCheckError as exc:
        return [str(exc)]
    for relative in paths:
        current = root / relative
        if not current.is_file():
            errors.append(f"published migration removed: {relative}")
            continue
        try:
            base_bytes = _git_bytes(root, base_ref, relative)
            current_bytes = current.read_bytes()
        except (CompatibilityCheckError, OSError) as exc:
            errors.append(str(exc))
            continue
        if current_bytes != base_bytes:
            errors.append(f"published migration bytes changed: {relative}")
    return errors


def _validate_published_schemas(
    root: Path,
    base_ref: str,
    base_registry: dict[str, object],
    current_registry: dict[str, object],
) -> list[str]:
    errors: list[str] = []
    current_by_id = {
        interface_id: item
        for item in _object_items(current_registry.get("schemas"))
        if (interface_id := _string(item, "id")) is not None
    }
    for base_item in _object_items(base_registry.get("schemas")):
        if base_item.get("status") != "published":
            continue
        interface_id = _string(base_item, "id")
        relative = _string(base_item, "path")
        if interface_id is None or relative is None or not _safe_relative_path(relative):
            errors.append("base published schema entry is malformed")
            continue
        current_item = current_by_id.get(interface_id)
        if current_item is None:
            errors.append(f"published schema registration removed: {interface_id}")
            continue
        for key in ("version", "path", "model"):
            if current_item.get(key) != base_item.get(key):
                errors.append(f"published schema metadata changed: {interface_id}.{key}")
        current_path = root / relative
        if not current_path.is_file():
            errors.append(f"published schema removed: {relative}")
            continue
        try:
            base_bytes = _git_bytes(root, base_ref, relative)
            current_bytes = current_path.read_bytes()
        except (CompatibilityCheckError, OSError) as exc:
            errors.append(str(exc))
            continue
        if current_bytes != base_bytes:
            errors.append(f"published schema bytes changed: {relative}")
    return errors


def _is_protected_port(item: dict[str, object]) -> bool:
    return item.get("status") == "implemented" or item.get("signature_status") == "frozen"


def _validate_port_signatures(
    root: Path,
    base_ref: str,
    base_registry: dict[str, object],
    current_registry: dict[str, object],
    base_delivery: dict[str, object],
    current_delivery: dict[str, object],
) -> list[str]:
    errors: list[str] = []
    base_ports = _object_items(base_registry.get("ports"))
    current_ports = _object_items(current_registry.get("ports"))
    current_by_id = {
        interface_id: item
        for item in current_ports
        if (interface_id := _string(item, "id")) is not None
    }
    base_by_symbol = {
        symbol: item for item in base_ports if (symbol := _string(item, "symbol")) is not None
    }

    for base_item in base_ports:
        if not _is_protected_port(base_item):
            continue
        interface_id = _string(base_item, "id")
        symbol = _string(base_item, "symbol")
        relative = _string(base_item, "file")
        if interface_id is None or symbol is None or relative is None:
            errors.append("base protected Port entry is malformed")
            continue
        current_item = current_by_id.get(interface_id)
        if current_item is None:
            errors.append(f"frozen Port registration removed: {interface_id}")
            continue
        if current_item.get("symbol") != symbol or current_item.get("file") != relative:
            errors.append(f"frozen Port metadata changed: {interface_id}")
            continue
        try:
            base_signature = _port_signature(
                _git_bytes(root, base_ref, relative),
                symbol,
                f"{base_ref}:{relative}",
            )
            current_signature = _port_signature(
                (root / relative).read_bytes(),
                symbol,
                relative,
            )
        except (CompatibilityCheckError, OSError) as exc:
            errors.append(str(exc))
            continue
        if current_signature != base_signature:
            errors.append(f"frozen Port signature changed: {interface_id}")

    for current_item in current_ports:
        if current_item.get("signature_status") != "frozen":
            continue
        symbol = _string(current_item, "symbol")
        if symbol is None:
            errors.append("current frozen Port entry is missing symbol")
            continue
        prior_item = base_by_symbol.get(symbol)
        if prior_item is not None and _is_protected_port(prior_item):
            continue
        if not _first_freeze_is_authorized(base_delivery, current_delivery):
            errors.append(
                f"Port {symbol} first freeze requires active_execution to be null "
                "or an authorized proposed contract-steward lane"
            )
            continue
        if prior_item is None:
            continue
        base_version = _semver(_string(prior_item, "version"))
        current_version = _semver(_string(current_item, "version"))
        if base_version is None or current_version is None:
            errors.append(f"Port {symbol} first freeze requires valid MAJOR.MINOR versions")
            continue
        base_file = _string(prior_item, "file")
        current_file = _string(current_item, "file")
        if base_file is None or current_file is None:
            errors.append(f"Port {symbol} first freeze requires registered source files")
            continue
        try:
            base_signature = _port_signature(
                _git_bytes(root, base_ref, base_file),
                symbol,
                f"{base_ref}:{base_file}",
            )
            current_signature = _port_signature(
                (root / current_file).read_bytes(),
                symbol,
                current_file,
            )
        except (CompatibilityCheckError, OSError) as exc:
            errors.append(str(exc))
            continue
        if current_signature != base_signature and current_version <= base_version:
            errors.append(
                f"Port {symbol} first freeze changed its signature without a version increase"
            )
    return errors


def validate(root: Path, base_ref: str) -> list[str]:
    """Return compatibility violations between ``base_ref`` and the working tree."""

    resolved_root = root.resolve()
    verify = _git(resolved_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if verify.returncode != 0:
        detail = verify.stderr.decode("utf-8", errors="replace").strip()
        return [f"base ref is not a commit: {base_ref}: {detail}"]
    try:
        base_registry = _load_json_bytes(
            _git_bytes(resolved_root, base_ref, "governance/interfaces.json"),
            f"{base_ref}:governance/interfaces.json",
        )
        current_registry = _load_current_json(
            resolved_root,
            "governance/interfaces.json",
        )
        base_delivery_bytes = _optional_git_bytes(
            resolved_root,
            base_ref,
            "governance/delivery.json",
        )
        base_delivery: dict[str, object] = (
            {"active_execution": None}
            if base_delivery_bytes is None
            else _load_json_bytes(
                base_delivery_bytes,
                f"{base_ref}:governance/delivery.json",
            )
        )
        current_delivery = _load_current_json(
            resolved_root,
            "governance/delivery.json",
        )
    except CompatibilityCheckError as exc:
        return [str(exc)]

    return [
        *_validate_published_migrations(resolved_root, base_ref),
        *_validate_published_schemas(
            resolved_root,
            base_ref,
            base_registry,
            current_registry,
        ),
        *_validate_port_signatures(
            resolved_root,
            base_ref,
            base_registry,
            current_registry,
            base_delivery,
            current_delivery,
        ),
    ]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        required=True,
        help="commit/ref that owns the previously published compatibility surface",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    errors = validate(Path(__file__).resolve().parents[1], args.base_ref)
    if errors:
        print("Contract compatibility check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Contract compatibility check passed against {args.base_ref}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
