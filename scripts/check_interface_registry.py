#!/usr/bin/env python3
"""Statically verify the published interface registry without importing the application."""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REGISTRY_ID = re.compile(
    r"^(http|task|event|schema|port):[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*:"
    r"v[1-9][0-9]*(?:\.[0-9]+)?$"
)
SEMVER = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
OWNER = re.compile(r"^[a-z][a-z0-9_-]*$")
TASK_NAME = re.compile(
    r"^stock_assistant\.(?P<owner>[a-z][a-z0-9_]*)\."
    r"(?P<operation>[a-z][a-z0-9_]*)\.v(?P<major>[1-9][0-9]*)$"
)
EVENT_NAME = re.compile(
    r"^com\.stockassistant\.(?P<owner>[a-z][a-z0-9_]*)\."
    r"(?P<event>[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)"
    r"\.v(?P<major>[1-9][0-9]*)$"
)
SCHEMA_FILE = re.compile(
    r"^(?P<name>[a-z][a-z0-9-]*)\.v(?P<version>[1-9][0-9]*\.[0-9]+)\.schema\.json$"
)
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}
PORT_DIRECTIONS = {"inbound_use_case", "outbound_dependency", "lifecycle_utility"}
PORT_VOID_METHOD_EXCEPTIONS = {
    "LifecyclePort.start",
    "LifecyclePort.close",
    "UnitOfWorkPort.__aexit__",
    "UnitOfWorkPort.commit",
    "UnitOfWorkPort.rollback",
}
PORT_MULTI_PARAMETER_EXCEPTIONS = {
    # Object content is a bounded streaming data plane beside its metadata contract.
    "ObjectStorePort.put",
    # Python's async context-manager protocol has a fixed exception triple.
    "UnitOfWorkPort.__aexit__",
}
REQUIRED_CHANGE_POLICY = {
    "search_existing_before_change",
    "reuse_semantic_equivalent",
    "duplicate_semantics_forbidden",
    "breaking_change_requires_new_major",
    "synchronize_all_affected_standards",
}


class RegistryError(ValueError):
    """Raised when a registry or statically inspected source is malformed."""


@dataclass(frozen=True, slots=True)
class HttpEndpoint:
    method: str
    path: str
    handler: str
    file: str
    response_contract: str


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    key: str
    name: str
    callable: str
    file: str
    is_stub: bool


@dataclass(frozen=True, slots=True)
class PublishedSchema:
    path: str
    version: str
    model: str


@dataclass(frozen=True, slots=True)
class DomainEvent:
    name: str
    files: frozenset[str]


@dataclass(frozen=True, slots=True)
class PublicPort:
    symbol: str
    file: str
    module: str
    class_name: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        raise RegistryError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"{path}: root must be an object")
    return value


def _validate_change_policy(registry: dict[str, object]) -> list[str]:
    policy = registry.get("change_policy")
    if not isinstance(policy, dict):
        return ["governance/interfaces.json.change_policy must be an object"]
    errors: list[str] = []
    for key in sorted(REQUIRED_CHANGE_POLICY):
        if policy.get(key) is not True:
            errors.append(f"governance/interfaces.json.change_policy.{key} must be true")
    return errors


def _validate_parallel_consumption_policy(registry: dict[str, object]) -> list[str]:
    policy = registry.get("parallel_consumption_policy")
    if not isinstance(policy, dict):
        return ["interfaces.parallel_consumption_policy must be an object"]
    errors: list[str] = []
    for key in (
        "registered_stubs_are_name_reservations_only",
        "declared_ports_do_not_imply_adapters",
    ):
        if policy.get(key) is not True:
            errors.append(f"interfaces.parallel_consumption_policy.{key} must be true")
    expected_paths = {
        "adapter_and_consumer_maturity_registry": "governance/delivery.json",
        "contract_freeze_protocol": "docs/engineering/parallel-development-protocol.md",
    }
    for key, expected in expected_paths.items():
        if policy.get(key) != expected:
            errors.append(f"interfaces.parallel_consumption_policy.{key} must be {expected}")
    return errors


def _parse_python(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise RegistryError(f"{path}: cannot parse Python source: {exc}") from exc


def _objects(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RegistryError(f"{label} must be an array of objects")
    return value


def _required_string(item: dict[str, object], key: str, label: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{label}.{key} must be a non-empty string")
    return value


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _join_path(*parts: str) -> str:
    components = [component for part in parts for component in part.split("/") if component]
    return "/" + "/".join(components)


def _import_map(tree: ast.Module) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local_name = alias.asname or alias.name
                imports[local_name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = alias.name
    return imports


def _resolve_type(node: ast.AST | None, imports: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolve_type(node.value, imports)
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Subscript):
        outer = _resolve_type(node.value, imports)
        inner = _resolve_type(node.slice, imports)
        return f"{outer}[{inner}]" if outer and inner else None
    if isinstance(node, ast.Tuple):
        values = [_resolve_type(element, imports) for element in node.elts]
        return ",".join(value for value in values if value) if all(values) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _resolve_type(node.left, imports)
        right = _resolve_type(node.right, imports)
        return f"{left}|{right}" if left and right else None
    return None


def _router_prefix(tree: ast.Module, router_name: str) -> str:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == router_name for target in targets):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        for keyword in value.keywords:
            if keyword.arg == "prefix":
                prefix = _literal_string(keyword.value)
                if prefix is not None:
                    return prefix
    return ""


def _discover_http(root: Path) -> tuple[dict[tuple[str, str], HttpEndpoint], list[str]]:
    errors: list[str] = []
    main_path = root / "apps/api/main.py"
    try:
        main_tree = _parse_python(main_path)
    except RegistryError as exc:
        return {}, [str(exc)]
    router_modules: dict[str, tuple[str, str]] = {}
    for node in main_tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            if alias.name == "router" or alias.name.endswith("_router"):
                router_modules[alias.asname or alias.name] = (node.module, alias.name)

    endpoints: dict[tuple[str, str], HttpEndpoint] = {}
    seen_mounts: set[tuple[str, str, str]] = set()
    for walk_node in ast.walk(main_tree):
        if not isinstance(walk_node, ast.Call) or not isinstance(walk_node.func, ast.Attribute):
            continue
        if walk_node.func.attr != "include_router" or not walk_node.args:
            continue
        router_arg = walk_node.args[0]
        if not isinstance(router_arg, ast.Name) or router_arg.id not in router_modules:
            errors.append(
                f"apps/api/main.py:{walk_node.lineno}: include_router must use a static import"
            )
            continue
        api_prefix = ""
        for keyword in walk_node.keywords:
            if keyword.arg == "prefix":
                api_prefix = _literal_string(keyword.value) or ""
        route_module, router_name = router_modules[router_arg.id]
        mount_key = (route_module, router_name, api_prefix)
        if mount_key in seen_mounts:
            continue
        seen_mounts.add(mount_key)
        route_path = root / f"{route_module.replace('.', '/')}.py"
        try:
            route_tree = _parse_python(route_path)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        route_prefix = _router_prefix(route_tree, router_name)
        imports = _import_map(route_tree)
        relative_file = route_path.relative_to(root).as_posix()
        for function in route_tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                method = decorator.func.attr.lower()
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(decorator.func.value, ast.Name):
                    continue
                if decorator.func.value.id != router_name:
                    continue
                leaf = _literal_string(decorator.args[0]) if decorator.args else None
                response_node: ast.AST | None = None
                for keyword in decorator.keywords:
                    if keyword.arg in {"path", "route"} and leaf is None:
                        leaf = _literal_string(keyword.value)
                    elif keyword.arg == "response_model":
                        response_node = keyword.value
                if leaf is None:
                    errors.append(f"{relative_file}:{function.lineno}: route path must be literal")
                    continue
                response_contract = _resolve_type(response_node, imports)
                if response_contract is None:
                    errors.append(
                        f"{relative_file}:{function.lineno}: route requires a static response_model"
                    )
                    continue
                full_path = _join_path(api_prefix, route_prefix, leaf)
                key = (method.upper(), full_path)
                if key in endpoints:
                    errors.append(f"duplicate static HTTP route: {key[0]} {key[1]}")
                    continue
                endpoints[key] = HttpEndpoint(
                    method=key[0],
                    path=key[1],
                    handler=f"{route_module}.{function.name}",
                    file=relative_file,
                    response_contract=response_contract,
                )
    return endpoints, errors


def _mapping_literal(node: ast.AST | None) -> ast.Dict | None:
    if isinstance(node, ast.Dict):
        return node
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Dict):
        return node.args[0]
    return None


def _discover_task_names(root: Path) -> tuple[dict[str, str], list[str]]:
    path = root / "apps/worker/task_names.py"
    try:
        tree = _parse_python(path)
    except RegistryError as exc:
        return {}, [str(exc)]
    mapping: ast.Dict | None = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "TASK_NAMES":
                mapping = _mapping_literal(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TASK_NAMES" for target in node.targets
        ):
            mapping = _mapping_literal(node.value)
    if mapping is None:
        return {}, ["apps/worker/task_names.py: TASK_NAMES must be a static mapping"]
    result: dict[str, str] = {}
    errors: list[str] = []
    for key_node, value_node in zip(mapping.keys, mapping.values, strict=True):
        key = _literal_string(key_node)
        value = _literal_string(value_node)
        if key is None or value is None:
            errors.append("apps/worker/task_names.py: TASK_NAMES keys and values must be strings")
            continue
        if key in result:
            errors.append(f"apps/worker/task_names.py: duplicate task key: {key}")
        result[key] = value
    return result, errors


def _task_key_from_decorator(decorator: ast.AST) -> str | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    if decorator.func.attr != "task":
        return None
    for keyword in decorator.keywords:
        if keyword.arg != "name" or not isinstance(keyword.value, ast.Subscript):
            continue
        subscript = keyword.value
        if not isinstance(subscript.value, ast.Name) or subscript.value.id != "TASK_NAMES":
            continue
        return _literal_string(subscript.slice)
    return None


def _discover_tasks(root: Path) -> tuple[dict[str, TaskDefinition], list[str]]:
    task_names, errors = _discover_task_names(root)
    path = root / "apps/worker/tasks.py"
    try:
        tree = _parse_python(path)
    except RegistryError as exc:
        return {}, [*errors, str(exc)]
    relative_file = path.relative_to(root).as_posix()
    definitions: dict[str, TaskDefinition] = {}
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        keys = [
            key
            for decorator in function.decorator_list
            if (key := _task_key_from_decorator(decorator)) is not None
        ]
        for key in keys:
            if key not in task_names:
                errors.append(f"{relative_file}:{function.lineno}: unknown TASK_NAMES key {key}")
                continue
            if key in definitions:
                errors.append(f"{relative_file}: duplicate task callable for key {key}")
                continue
            is_stub = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"_skipped", "_raise_disabled"}
                for node in ast.walk(function)
            )
            definitions[key] = TaskDefinition(
                key=key,
                name=task_names[key],
                callable=f"apps.worker.tasks.{function.name}",
                file=relative_file,
                is_stub=is_stub,
            )
    missing = sorted(set(task_names) - set(definitions))
    for key in missing:
        errors.append(f"apps/worker/task_names.py: registered task has no callable: {key}")
    return definitions, errors


def _discover_events(root: Path) -> tuple[dict[str, DomainEvent], list[str]]:
    """Discover literal production event names without importing producers."""

    occurrences: dict[str, set[str]] = {}
    errors: list[str] = []
    roots = (
        root / "apps",
        root / "src/stock_assistant/modules",
        root / "src/stock_assistant/platform/db",
    )
    for source_root in roots:
        for path in sorted(source_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                tree = _parse_python(path)
            except RegistryError as exc:
                errors.append(str(exc))
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if EVENT_NAME.fullmatch(node.value):
                    occurrences.setdefault(node.value, set()).add(relative)
    events = {
        name: DomainEvent(name=name, files=frozenset(files)) for name, files in occurrences.items()
    }
    return events, errors


def _discover_schemas(root: Path) -> tuple[dict[str, PublishedSchema], list[str]]:
    schemas: dict[str, PublishedSchema] = {}
    errors: list[str] = []
    for path in sorted((root / "schemas").rglob("*.schema.json")):
        relative = path.relative_to(root).as_posix()
        match = SCHEMA_FILE.fullmatch(path.name)
        if match is None:
            errors.append(f"{relative}: published schema filename must contain vMAJOR.MINOR")
            continue
        try:
            document = _load_json(path)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        version = document.get("x-schema-version")
        model = document.get("x-contract-model")
        schema_id = document.get("$id")
        if not isinstance(version, str) or version != match.group("version"):
            errors.append(f"{relative}: x-schema-version does not match filename")
            continue
        if not isinstance(model, str) or not model:
            errors.append(f"{relative}: missing x-contract-model")
            continue
        expected_suffix = relative.removeprefix("schemas/")
        if not isinstance(schema_id, str) or not schema_id.endswith(f"/{expected_suffix}"):
            errors.append(f"{relative}: $id must end with its immutable schema path")
            continue
        schemas[relative] = PublishedSchema(relative, version, model)
    return schemas, errors


def _protocol_base(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "Protocol"
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and (node.value.id == "Protocol")
    )


def _is_bare_or_nullable_result(node: ast.AST | None) -> bool:
    """Reject primitive/nullable business results that cannot evolve compatibly."""

    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return True
    if isinstance(node, ast.Name):
        return node.id in {"bool", "None"}
    if isinstance(node, ast.Attribute):
        return node.attr == "Optional"
    if isinstance(node, ast.Subscript):
        outer = node.value
        if isinstance(outer, ast.Name) and outer.id == "Optional":
            return True
        if isinstance(outer, ast.Attribute) and outer.attr == "Optional":
            return True
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_bare_or_nullable_result(node.left) or _is_bare_or_nullable_result(node.right)
    return False


def _validate_port_method_shape(
    relative: str,
    class_name: str,
    method: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    qualified = f"{class_name}.{method.name}"
    errors: list[str] = []
    if _is_bare_or_nullable_result(method.returns) and qualified not in PORT_VOID_METHOD_EXCEPTIONS:
        errors.append(
            f"{relative}:{method.lineno}: {qualified} must return an explicit result contract, "
            "not bool, None, or a nullable result"
        )
    positional = [*method.args.posonlyargs, *method.args.args]
    parameter_count = len(positional) - (1 if positional and positional[0].arg == "self" else 0)
    parameter_count += len(method.args.kwonlyargs)
    if parameter_count > 1 and qualified not in PORT_MULTI_PARAMETER_EXCEPTIONS:
        errors.append(f"{relative}:{method.lineno}: {qualified} must accept one request contract")
    return errors


def _exported_names(tree: ast.Module) -> set[str]:
    for node in tree.body:
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "__all__":
                value = node.value
        if isinstance(value, (ast.List, ast.Tuple)):
            names = [_literal_string(element) for element in value.elts]
            if all(name is not None for name in names):
                return {name for name in names if name is not None}
    raise RegistryError("src/stock_assistant/ports/__init__.py: __all__ must be a string list")


def _discover_ports(root: Path) -> tuple[dict[str, PublicPort], list[str]]:
    ports_root = root / "src/stock_assistant/ports"
    errors: list[str] = []
    try:
        exported = _exported_names(_parse_python(ports_root / "__init__.py"))
    except RegistryError as exc:
        return {}, [str(exc)]
    ports: dict[str, PublicPort] = {}
    for path in sorted(ports_root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = _parse_python(path)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        module = path.stem
        relative = path.relative_to(root).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Port"):
                continue
            if not any(_protocol_base(base) for base in node.bases):
                continue
            symbol = f"stock_assistant.ports.{module}.{node.name}"
            if symbol in ports:
                errors.append(f"duplicate Port Protocol: {symbol}")
                continue
            ports[symbol] = PublicPort(symbol, relative, module, node.name)
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    errors.extend(_validate_port_method_shape(relative, node.name, member))
            if node.name not in exported:
                errors.append(
                    f"{relative}:{node.lineno}: public Port must be exported in ports.__all__"
                )
    declared_names = {port.class_name for port in ports.values()}
    missing_symbols = sorted(
        name for name in exported if name.endswith("Port") and name not in declared_names
    )
    for name in missing_symbols:
        errors.append(
            f"src/stock_assistant/ports/__init__.py exports missing Port Protocol: {name}"
        )
    return ports, errors


def _kebab(name: str) -> str:
    normalized = name.replace("LoRA", "Lora")
    return re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
        "-",
        normalized,
    ).lower()


def _validate_common(
    registry: dict[str, object],
    status_definitions: dict[str, object],
) -> tuple[dict[str, list[dict[str, object]]], list[str]]:
    sections: dict[str, list[dict[str, object]]] = {}
    errors: list[str] = []
    seen_ids: set[str] = set()
    for section in ("http_endpoints", "tasks", "events", "schemas", "ports"):
        try:
            items = _objects(registry.get(section), f"interfaces.{section}")
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        sections[section] = items
        for index, item in enumerate(items):
            label = f"interfaces.{section}[{index}]"
            try:
                interface_id = _required_string(item, "id", label)
                version = _required_string(item, "version", label)
                status = _required_string(item, "status", label)
                owner = _required_string(item, "owner", label)
                _required_string(item, "compatibility", label)
            except RegistryError as exc:
                errors.append(str(exc))
                continue
            if not REGISTRY_ID.fullmatch(interface_id):
                errors.append(f"{label}.id has invalid format: {interface_id}")
            if not SEMVER.fullmatch(version):
                errors.append(f"{label}.version must be MAJOR.MINOR")
            if not OWNER.fullmatch(owner):
                errors.append(f"{label}.owner has invalid format")
            if interface_id in seen_ids:
                errors.append(f"duplicate interface id: {interface_id}")
            seen_ids.add(interface_id)
            if status not in status_definitions:
                errors.append(f"{label}.status is not defined: {status}")
    return sections, errors


def _validate_http(
    items: list[dict[str, object]],
    discovered: dict[tuple[str, str], HttpEndpoint],
) -> list[str]:
    errors: list[str] = []
    registry_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        label = f"interfaces.http_endpoints[{index}]"
        try:
            method = _required_string(item, "method", label).upper()
            path = _required_string(item, "path", label)
            handler = _required_string(item, "handler", label)
            file = _required_string(item, "file", label)
            response = _required_string(item, "response_contract", label)
            interface_id = _required_string(item, "id", label)
            version = _required_string(item, "version", label)
            owner = _required_string(item, "owner", label)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        key = (method, path)
        if key in registry_keys:
            errors.append(f"duplicate HTTP interface: {method} {path}")
            continue
        registry_keys.add(key)
        endpoint = discovered.get(key)
        if endpoint is None:
            errors.append(f"{label}: route does not exist in apps/api/main.py: {method} {path}")
            continue
        expected = {
            "handler": endpoint.handler,
            "file": endpoint.file,
            "response_contract": endpoint.response_contract,
        }
        actual = {"handler": handler, "file": file, "response_contract": response}
        if actual != expected:
            errors.append(f"{label}: handler/file/response contract does not match source")
        major_match = re.match(r"^/api/v(?P<major>[1-9][0-9]*)(?:/|$)", path)
        if major_match is None:
            errors.append(f"{label}.path must begin with /api/vMAJOR")
            continue
        major = major_match.group("major")
        route_suffix = endpoint.handler.removeprefix("apps.api.routes.")
        normalized_route_suffix = route_suffix.replace("_", "-")
        expected_id = f"http:{owner}.{normalized_route_suffix}:v{major}"
        if interface_id != expected_id:
            errors.append(f"{label}.id must be {expected_id}")
        if version.split(".", maxsplit=1)[0] != major:
            errors.append(f"{label}.version major must match API path")
        if item.get("status") != "implemented":
            errors.append(f"{label}.status must be implemented for a live route")
        if item.get("compatibility") != "major-versioned":
            errors.append(f"{label}.compatibility must be major-versioned")
    missing = sorted(set(discovered) - registry_keys)
    for method, path in missing:
        errors.append(f"unregistered HTTP interface: {method} {path}")
    return errors


def _validate_tasks(
    items: list[dict[str, object]],
    discovered: dict[str, TaskDefinition],
) -> list[str]:
    errors: list[str] = []
    registered_keys: set[str] = set()
    registered_names: set[str] = set()
    for index, item in enumerate(items):
        label = f"interfaces.tasks[{index}]"
        try:
            key = _required_string(item, "key", label)
            name = _required_string(item, "name", label)
            callable_name = _required_string(item, "callable", label)
            file = _required_string(item, "file", label)
            interface_id = _required_string(item, "id", label)
            version = _required_string(item, "version", label)
            owner = _required_string(item, "owner", label)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        if key in registered_keys:
            errors.append(f"duplicate task registry key: {key}")
            continue
        if name in registered_names:
            errors.append(f"duplicate task registry name: {name}")
        registered_keys.add(key)
        registered_names.add(name)
        definition = discovered.get(key)
        if definition is None:
            errors.append(f"{label}: task key does not exist in TASK_NAMES: {key}")
            continue
        if (name, callable_name, file) != (
            definition.name,
            definition.callable,
            definition.file,
        ):
            errors.append(f"{label}: task name/callable/file does not match static source")
        match = TASK_NAME.fullmatch(name)
        if match is None:
            errors.append(f"{label}.name is not a versioned task name")
            continue
        expected_id = (
            f"task:{match.group('owner')}."
            f"{match.group('operation').replace('_', '-')}:v{match.group('major')}"
        )
        if interface_id != expected_id:
            errors.append(f"{label}.id must be {expected_id}")
        if owner != match.group("owner"):
            errors.append(f"{label}.owner must match task namespace")
        if version.split(".", maxsplit=1)[0] != match.group("major"):
            errors.append(f"{label}.version major must match task name")
        expected_status = "registered_stub" if definition.is_stub else "implemented"
        if item.get("status") != expected_status:
            errors.append(f"{label}.status must be {expected_status}")
        request_schema = item.get("request_schema")
        result_schema = item.get("result_schema")
        if expected_status == "registered_stub":
            if (request_schema is None) != (result_schema is None):
                errors.append(
                    f"{label}: registered stub must publish both request/result schemas or neither"
                )
            expected_stub_behavior = (
                "typed_skipped"
                if request_schema is not None and result_schema is not None
                else "fail_closed_disabled"
            )
            if item.get("stub_behavior") != expected_stub_behavior:
                errors.append(f"{label}.stub_behavior must be {expected_stub_behavior}")
        elif "stub_behavior" in item:
            errors.append(f"{label}: implemented task must not declare stub_behavior")
        if item.get("compatibility") != "stable-name-v1":
            errors.append(f"{label}.compatibility must be stable-name-v1")
    for key in sorted(set(discovered) - registered_keys):
        errors.append(f"unregistered task interface: {key} = {discovered[key].name}")
    return errors


def _validate_events(
    items: list[dict[str, object]],
    discovered: dict[str, DomainEvent],
    schemas: list[dict[str, object]],
) -> list[str]:
    errors: list[str] = []
    registered_names: set[str] = set()
    schema_ids = {item.get("id") for item in schemas if isinstance(item.get("id"), str)}
    for index, item in enumerate(items):
        label = f"interfaces.events[{index}]"
        try:
            name = _required_string(item, "name", label)
            producer = _required_string(item, "producer", label)
            file = _required_string(item, "file", label)
            payload_schema = _required_string(item, "payload_schema", label)
            interface_id = _required_string(item, "id", label)
            version = _required_string(item, "version", label)
            owner = _required_string(item, "owner", label)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        if name in registered_names:
            errors.append(f"duplicate event registry name: {name}")
            continue
        registered_names.add(name)
        match = EVENT_NAME.fullmatch(name)
        if match is None:
            errors.append(f"{label}.name is not a versioned event name")
            continue
        event = discovered.get(name)
        if event is None:
            errors.append(f"{label}: event name does not exist as a production literal")
        elif file not in event.files:
            errors.append(f"{label}.file does not contain the registered event literal")
        expected_id = (
            f"event:{match.group('owner')}."
            f"{match.group('event').replace('_', '-')}:v{match.group('major')}"
        )
        if interface_id != expected_id:
            errors.append(f"{label}.id must be {expected_id}")
        if owner != match.group("owner"):
            errors.append(f"{label}.owner must match event namespace")
        if version.split(".", maxsplit=1)[0] != match.group("major"):
            errors.append(f"{label}.version major must match event name")
        if payload_schema not in schema_ids:
            errors.append(f"{label}.payload_schema must reference a registered schema id")
        if "." not in producer:
            errors.append(f"{label}.producer must be a fully qualified symbol")
        if item.get("status") != "implemented":
            errors.append(f"{label}.status must be implemented for a production event")
        if item.get("compatibility") != "stable-name-v1":
            errors.append(f"{label}.compatibility must be stable-name-v1")
    for name in sorted(set(discovered) - registered_names):
        errors.append(f"unregistered domain event interface: {name}")
    return errors


def _validate_schemas(
    items: list[dict[str, object]],
    discovered: dict[str, PublishedSchema],
) -> list[str]:
    errors: list[str] = []
    registered_paths: set[str] = set()
    for index, item in enumerate(items):
        label = f"interfaces.schemas[{index}]"
        try:
            path = _required_string(item, "path", label)
            model = _required_string(item, "model", label)
            version = _required_string(item, "version", label)
            interface_id = _required_string(item, "id", label)
            owner = _required_string(item, "owner", label)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        if path in registered_paths:
            errors.append(f"duplicate schema registry path: {path}")
            continue
        registered_paths.add(path)
        schema = discovered.get(path)
        if schema is None:
            errors.append(f"{label}: schema path does not exist: {path}")
            continue
        if (version, model) != (schema.version, schema.model):
            errors.append(f"{label}: schema version/model does not match generated file")
        relative = Path(path).relative_to("schemas")
        match = SCHEMA_FILE.fullmatch(relative.name)
        if match is None:
            errors.append(f"{label}.path has invalid versioned schema filename")
            continue
        namespace = ".".join((*relative.parent.parts, match.group("name")))
        expected_id = f"schema:{namespace}:v{version}"
        if interface_id != expected_id:
            errors.append(f"{label}.id must be {expected_id}")
        expected_owner = relative.parts[0]
        if owner != expected_owner:
            errors.append(f"{label}.owner must be {expected_owner}")
        if item.get("status") != "published":
            errors.append(f"{label}.status must be published")
        if item.get("compatibility") != "immutable-version":
            errors.append(f"{label}.compatibility must be immutable-version")
    for path in sorted(set(discovered) - registered_paths):
        errors.append(f"unregistered published schema: {path}")
    return errors


def _validate_ports(
    items: list[dict[str, object]],
    discovered: dict[str, PublicPort],
) -> list[str]:
    errors: list[str] = []
    registered_symbols: set[str] = set()
    for index, item in enumerate(items):
        label = f"interfaces.ports[{index}]"
        try:
            symbol = _required_string(item, "symbol", label)
            file = _required_string(item, "file", label)
            version = _required_string(item, "version", label)
            interface_id = _required_string(item, "id", label)
            owner = _required_string(item, "owner", label)
            direction = _required_string(item, "direction", label)
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        if symbol in registered_symbols:
            errors.append(f"duplicate Port registry symbol: {symbol}")
            continue
        registered_symbols.add(symbol)
        port = discovered.get(symbol)
        if port is None:
            errors.append(f"{label}: public Port Protocol does not exist: {symbol}")
            continue
        if file != port.file:
            errors.append(f"{label}.file must be {port.file}")
        slug = _kebab(port.class_name.removesuffix("Port"))
        expected_id = f"port:{port.module}.{slug}:v{version}"
        if interface_id != expected_id:
            errors.append(f"{label}.id must be {expected_id}")
        if owner != port.module:
            errors.append(f"{label}.owner must be {port.module}")
        if direction not in PORT_DIRECTIONS:
            errors.append(f"{label}.direction must be one of {sorted(PORT_DIRECTIONS)}")
        if item.get("status") != "declared":
            errors.append(f"{label}.status must be declared; a Protocol is not an implementation")
        if item.get("compatibility") != "source-compatible-minor":
            errors.append(f"{label}.compatibility must be source-compatible-minor")
    for symbol in sorted(set(discovered) - registered_symbols):
        errors.append(f"unregistered public Port Protocol: {symbol}")
    return errors


def validate(root: Path) -> list[str]:
    """Return all interface-registry violations."""

    try:
        registry = _load_json(root / "governance/interfaces.json")
    except RegistryError as exc:
        return [str(exc)]
    errors: list[str] = []
    if registry.get("schema_version") != "1.0":
        errors.append("governance/interfaces.json: schema_version must be 1.0")
    errors.extend(_validate_change_policy(registry))
    errors.extend(_validate_parallel_consumption_policy(registry))
    status_definitions = registry.get("status_definitions")
    if not isinstance(status_definitions, dict) or not status_definitions:
        return [*errors, "interfaces.status_definitions must be a non-empty object"]
    for status, description in status_definitions.items():
        if not isinstance(status, str) or not isinstance(description, str) or not description:
            errors.append("interfaces.status_definitions must map strings to descriptions")
    direction_definitions = registry.get("direction_definitions")
    if not isinstance(direction_definitions, dict):
        errors.append("interfaces.direction_definitions must be an object")
    elif set(direction_definitions) != PORT_DIRECTIONS or not all(
        isinstance(description, str) and description
        for description in direction_definitions.values()
    ):
        errors.append("interfaces.direction_definitions must describe every allowed direction")

    sections, common_errors = _validate_common(registry, status_definitions)
    errors.extend(common_errors)
    http, http_errors = _discover_http(root)
    tasks, task_errors = _discover_tasks(root)
    events, event_errors = _discover_events(root)
    schemas, schema_errors = _discover_schemas(root)
    ports, port_errors = _discover_ports(root)
    errors.extend(http_errors)
    errors.extend(task_errors)
    errors.extend(event_errors)
    errors.extend(schema_errors)
    errors.extend(port_errors)
    errors.extend(_validate_http(sections.get("http_endpoints", []), http))
    errors.extend(_validate_tasks(sections.get("tasks", []), tasks))
    errors.extend(
        _validate_events(
            sections.get("events", []),
            events,
            sections.get("schemas", []),
        )
    )
    errors.extend(_validate_schemas(sections.get("schemas", []), schemas))
    errors.extend(_validate_ports(sections.get("ports", []), ports))
    return errors


def main() -> int:
    errors = validate(_repo_root())
    if errors:
        print("Interface registry check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Interface registry check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
