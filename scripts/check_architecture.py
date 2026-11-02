#!/usr/bin/env python3
"""Enforce Python dependency direction from governance/architecture.json."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Layer:
    """One source layer and the internal imports it may use."""

    name: str
    path_prefixes: tuple[str, ...]
    allowed_internal_imports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Violation:
    """One actionable dependency-direction failure."""

    file: str
    line: int
    layer: str
    imported: str


class ArchitectureConfigError(ValueError):
    """Raised when the machine-readable architecture is malformed."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ArchitectureConfigError(f"{label} must be a non-empty array of strings")
    return tuple(value)


def _load_config(path: Path) -> tuple[dict[str, object], tuple[Layer, ...]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchitectureConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ArchitectureConfigError("architecture config root must be an object")
    raw_layers = raw.get("layers")
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ArchitectureConfigError("architecture.layers must be a non-empty array")

    layers: list[Layer] = []
    names: set[str] = set()
    prefixes: set[str] = set()
    for index, value in enumerate(raw_layers):
        if not isinstance(value, dict):
            raise ArchitectureConfigError(f"architecture.layers[{index}] must be an object")
        name = value.get("name")
        if not isinstance(name, str) or not name:
            raise ArchitectureConfigError(f"architecture.layers[{index}].name must be a string")
        if name in names:
            raise ArchitectureConfigError(f"duplicate layer name: {name}")
        names.add(name)
        path_prefixes = _string_tuple(value.get("path_prefixes"), f"layer {name}.path_prefixes")
        for prefix in path_prefixes:
            if prefix in prefixes:
                raise ArchitectureConfigError(f"duplicate layer path prefix: {prefix}")
            prefixes.add(prefix)
        allowed = _string_tuple(
            value.get("allowed_internal_imports"),
            f"layer {name}.allowed_internal_imports",
        )
        layers.append(Layer(name, path_prefixes, allowed))
    return raw, tuple(layers)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _layer_for(relative: str, layers: tuple[Layer, ...]) -> Layer | None:
    matches = [
        (len(prefix), layer)
        for layer in layers
        for prefix in layer.path_prefixes
        if relative.startswith(prefix)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from_import(node: ast.ImportFrom, package: str) -> str | None:
    if node.level == 0:
        return node.module
    relative_name = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(relative_name, package)
    except (ImportError, ValueError):
        return None


def _import_targets(
    tree: ast.AST,
    *,
    package: str,
    internal_roots: tuple[str, ...],
) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _resolve_from_import(node, package)
        if not base:
            continue
        if base in internal_roots:
            targets.extend(
                (node.lineno, f"{base}.{alias.name}") for alias in node.names if alias.name != "*"
            )
        else:
            targets.append((node.lineno, base))
    return targets


def _iter_python_files(
    root: Path, scan_roots: tuple[str, ...], excluded: set[str]
) -> Iterator[Path]:
    for relative_root in scan_roots:
        search_root = root / relative_root
        if not search_root.exists():
            continue
        for path in sorted(search_root.rglob("*.py")):
            if any(part in excluded for part in path.relative_to(root).parts):
                continue
            yield path


def validate(root: Path) -> tuple[list[Violation], list[str]]:
    """Return dependency violations and configuration/source errors."""

    try:
        config, layers = _load_config(root / "governance/architecture.json")
        scan_roots = _string_tuple(config.get("scan_roots"), "architecture.scan_roots")
        internal_roots = _string_tuple(
            config.get("internal_import_prefixes"),
            "architecture.internal_import_prefixes",
        )
        excluded = set(
            _string_tuple(
                config.get("exclude_directory_names"),
                "architecture.exclude_directory_names",
            )
        )
        bounded = config.get("bounded_contexts")
        if not isinstance(bounded, dict):
            raise ArchitectureConfigError("architecture.bounded_contexts must be an object")
        context_path_prefix = bounded.get("path_prefix")
        context_import_prefix = bounded.get("import_prefix")
        if not isinstance(context_path_prefix, str) or not context_path_prefix:
            raise ArchitectureConfigError("bounded_contexts.path_prefix must be a string")
        if not isinstance(context_import_prefix, str) or not context_import_prefix:
            raise ArchitectureConfigError("bounded_contexts.import_prefix must be a string")
        forbid_cross_contexts = bounded.get("forbid_cross_context_imports") is True
    except ArchitectureConfigError as exc:
        return [], [str(exc)]

    fail_unclassified = config.get("fail_on_unclassified_python") is True
    violations: list[Violation] = []
    errors: list[str] = []
    for path in _iter_python_files(root, scan_roots, excluded):
        relative_path = path.relative_to(root)
        relative = relative_path.as_posix()
        layer = _layer_for(relative, layers)
        if layer is None:
            if fail_unclassified:
                errors.append(f"{relative}: Python file is not assigned to an architecture layer")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as exc:
            errors.append(f"{relative}: cannot parse Python source: {exc}")
            continue
        module = _module_name(relative_path)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for line, imported in _import_targets(
            tree,
            package=package,
            internal_roots=internal_roots,
        ):
            if not any(_matches_prefix(imported, prefix) for prefix in internal_roots):
                continue
            if forbid_cross_contexts and relative.startswith(context_path_prefix):
                source_tail = relative.removeprefix(context_path_prefix)
                source_context = source_tail.split("/", maxsplit=1)[0]
                target_prefix = f"{context_import_prefix}."
                if imported.startswith(target_prefix):
                    target_tail = imported.removeprefix(target_prefix)
                    target_context = target_tail.split(".", maxsplit=1)[0]
                    if target_context and target_context != source_context:
                        violations.append(Violation(relative, line, layer.name, imported))
                        continue
            if any(_matches_prefix(imported, prefix) for prefix in layer.allowed_internal_imports):
                continue
            violations.append(Violation(relative, line, layer.name, imported))
    return violations, errors


def main() -> int:
    root = _repo_root()
    violations, errors = validate(root)
    if errors or violations:
        print("Architecture check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        for violation in violations:
            print(
                f"- {violation.file}:{violation.line}: layer '{violation.layer}' "
                f"may not import '{violation.imported}'",
                file=sys.stderr,
            )
        return 1
    print("Architecture check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
