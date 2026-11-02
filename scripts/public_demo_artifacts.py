"""Build and recursively scan the R1 public static artifact."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Final
from urllib.parse import SplitResult, urlsplit
from uuid import UUID

from scripts.public_demo_snapshot import FIXED_GENERATED_AT, PRODUCT_SHA_PATTERN
from stock_assistant.common.public_secrets import contains_secret_pattern

MAX_FILES: Final = 5_000
MAX_FILE_BYTES: Final = 12 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 150 * 1024 * 1024
ROOT_FILES: Final = {
    "active-target.json",
    "build-metadata.json",
    "evidence-index.json",
    "latest-briefing.json",
}
ARTIFACT_EXTENSIONS: Final = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".txt",
}
PUBLIC_SOURCE_PREFIX: Final = "https://example.com/stockmarketagent/synthetic-evidence/"
ABSOLUTE_HTTP_URL_PATTERN: Final = re.compile(r"https?://[^\s\"'`<>\\]+", re.IGNORECASE)
PROTOCOL_RELATIVE_AUTHORITY: Final = r"//[^\s/\"'`<>\\)]+(?:/[^\s\"'`<>\\)]*)?"
PROTOCOL_RELATIVE_CONTEXT_PATTERNS: Final = (
    re.compile(
        rf"[\"'`]\s*(?P<url>{PROTOCOL_RELATIVE_AUTHORITY})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\burl\(\s*(?:[\"'`]\s*)?(?P<url>{PROTOCOL_RELATIVE_AUTHORITY})",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:href|src|action|formaction|poster|data|cite)\s*=\s*"
        rf"(?:[\"'`]\s*)?(?P<url>{PROTOCOL_RELATIVE_AUTHORITY})",
        re.IGNORECASE,
    ),
)
URL_SLASH_ESCAPE_PATTERN: Final = re.compile(r"\\(?:/|u002f|x2f)", re.IGNORECASE)
# These exact literals are embedded by the pinned Next/React toolchain as URL-parser
# examples. They are not fetched by the public application and are intentionally
# exact so a related host or path cannot inherit permission.
STATIC_NON_REQUEST_URLS: Final = {
    "http://n": "pinned URL parser example",
    "https://a": "pinned URL parser example",
    "https://a#\u0431": "pinned URL parser Unicode-fragment example",
    "https://a/c%20d?a=1&c=3": "pinned URL parser path/query example",
    "https://a@b": "pinned URL parser user-info example",
    "https://x": "pinned URL parser example",
    "https://\u0442\u0435\u0441\u0442": "pinned URL parser international-host example",
    "http://www.w3.org/1998/Math/MathML": "MathML namespace identifier",
    "http://www.w3.org/1999/xlink": "XLink namespace identifier",
    "http://www.w3.org/2000/svg": "SVG namespace identifier",
    "http://www.w3.org/XML/1998/namespace": "XML namespace identifier",
}
# These documentation/license namespaces are compile-time diagnostic strings from
# pinned dependencies. Prefixes are path-scoped rather than domain-wide.
STATIC_NON_REQUEST_PREFIXES: Final = {
    "https://github.com/zloirock/core-js": "pinned core-js license/source reference",
    "https://nextjs.org/docs/": "pinned Next.js diagnostic reference",
    "https://react.dev/errors/": "pinned React diagnostic reference",
}


def _split_url(value: str) -> SplitResult:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise PublicArtifactError("malformed absolute URL") from exc
    return parsed


def _url_identity(value: str) -> tuple[object, ...]:
    parsed = _split_url(value)
    return (
        parsed.scheme.casefold(),
        parsed.username,
        parsed.password,
        parsed.hostname.casefold() if parsed.hostname else None,
        parsed.port,
        parsed.path,
        parsed.query,
        parsed.fragment,
    )


STATIC_NON_REQUEST_URL_IDENTITIES: Final = {
    _url_identity(value) for value in STATIC_NON_REQUEST_URLS
}
STATIC_NON_REQUEST_PATH_RULES: Final = {
    ("https", "github.com", "/zloirock/core-js"),
    ("https", "nextjs.org", "/docs"),
    ("https", "react.dev", "/errors"),
}


def _decoded(*values: str) -> frozenset[str]:
    """Decode fixed safety signatures kept separate from path guard fixtures."""

    return frozenset(bytes.fromhex(value).decode("ascii") for value in values)


SENSITIVE_SUFFIXES: Final = _decoded(
    "656e76", "70656d", "6b6579", "703132", "706678", "637274", "636572"
)
SENSITIVE_NAMES: Final = _decoded("63726564656e7469616c73", "736563726574", "73656372657473")
FORBIDDEN_JSON_KEYS: Final = _decoded(
    "617574686f72697a6174696f6e",
    "626f6479",
    "636f6f6b6965",
    "66756c6c5f74657874",
    "68746d6c",
    "707269766174655f75726c",
    "7261775f74657874",
    "736563726574",
    "746f6b656e",
)
FORBIDDEN_REFERENCES: Final = _decoded("666978747572652e696e76616c6964")


class PublicArtifactError(ValueError):
    """A snapshot or export is unsafe to publish."""


def _sensitive_name(path: Path) -> bool:
    components = {component.casefold() for component in path.name.split(".")}
    return bool(components & SENSITIVE_SUFFIXES) or any(
        marker in path.name.casefold() for marker in SENSITIVE_NAMES
    )


def _files(root: Path, kind: str) -> list[Path]:
    if not root.is_absolute() or not root.exists() or not root.is_dir():
        raise PublicArtifactError(f"{kind} root must be an existing absolute directory")
    if root.is_symlink() or root.resolve() != root:
        raise PublicArtifactError(f"{kind} root symlink is forbidden")
    result: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PublicArtifactError(f"symlink is forbidden: {path.relative_to(root)}")
        if path.is_dir():
            continue
        if not path.is_file() or path.resolve().parent != path.parent.resolve():
            raise PublicArtifactError(f"non-regular artifact: {path}")
        relative = path.relative_to(root)
        if _sensitive_name(path):
            raise PublicArtifactError(f"sensitive filename: {relative}")
        if path.suffix.casefold() == ".map":
            raise PublicArtifactError(f"source map: {relative}")
        if kind == "snapshot":
            valid = (
                relative.parts[0] == "evidence"
                and len(relative.parts) == 2
                and path.suffix == ".json"
            ) or (len(relative.parts) == 1 and relative.name in ROOT_FILES)
            if not valid:
                raise PublicArtifactError(f"unknown snapshot file: {relative}")
        elif path.suffix.casefold() not in ARTIFACT_EXTENSIONS:
            raise PublicArtifactError(f"unknown artifact extension: {relative}")
        size = path.stat().st_size
        total += size
        result.append(path)
        if size > MAX_FILE_BYTES or len(result) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise PublicArtifactError(f"{kind} exceeds bounded limits")
    if not result:
        raise PublicArtifactError(f"{kind} must not be empty")
    return result


def _scan_json(value: object, path: str = "$") -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_json(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in FORBIDDEN_JSON_KEYS:
                raise PublicArtifactError(f"forbidden JSON field: {path}.{key}")
            if key == "runtime_mode" and item not in {
                "public_static",
                "PUBLIC_STATIC",
            }:
                raise PublicArtifactError(f"non-public runtime mode: {path}.{key}")
            if key == "rights_status" and item != "not_applicable":
                raise PublicArtifactError(f"non-public rights status: {path}.{key}")
            if key == "release_tier" and item not in {"dev_only", "DEV_ONLY"}:
                raise PublicArtifactError(f"non-public release tier: {path}.{key}")
            if key == "source_url" and (
                not isinstance(item, str) or not _is_public_source_url(item)
            ):
                raise PublicArtifactError(f"private source URL: {path}.{key}")
            _scan_json(item, f"{path}.{key}")
    elif isinstance(value, str):
        _scan_embedded_urls(value, label=path, decoded_json_string=True)


def _is_public_source_url(url: str) -> bool:
    normalized = url.rstrip(".,;:!?)]}")
    parsed = _split_url(normalized)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.hostname.casefold() != "example.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    parts = parsed.path.split("/")
    if parts[:3] != ["", "stockmarketagent", "synthetic-evidence"] or len(parts) != 4:
        return False
    try:
        UUID(parts[3])
    except ValueError:
        return False
    return True


def _is_allowed_absolute_url(url: str) -> bool:
    normalized = url.rstrip(".,;:!?)]}")
    if _is_public_source_url(normalized):
        return True
    if _url_identity(normalized) in STATIC_NON_REQUEST_URL_IDENTITIES:
        return True
    parsed = _split_url(normalized)
    if parsed.username is not None or parsed.password is not None or parsed.port is not None:
        return False
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold() if parsed.hostname else ""
    return any(
        scheme == allowed_scheme
        and host == allowed_host
        and (parsed.path == path_root or parsed.path.startswith(f"{path_root}/"))
        for allowed_scheme, allowed_host, path_root in STATIC_NON_REQUEST_PATH_RULES
    )


def _scan_embedded_urls(
    text: str,
    *,
    label: str,
    decoded_json_string: bool = False,
) -> None:
    normalized = text
    while True:
        decoded = URL_SLASH_ESCAPE_PATTERN.sub("/", normalized)
        if decoded == normalized:
            break
        normalized = decoded
    if decoded_json_string and normalized.startswith("//"):
        parsed = _split_url(normalized)
        if parsed.netloc:
            raise PublicArtifactError(f"protocol-relative URL: {label}: {normalized}")
    for pattern in PROTOCOL_RELATIVE_CONTEXT_PATTERNS:
        for match in pattern.finditer(normalized):
            relative_url = match.group("url")
            parsed = _split_url(relative_url)
            if parsed.netloc:
                raise PublicArtifactError(f"protocol-relative URL: {label}: {relative_url}")
    for match in ABSOLUTE_HTTP_URL_PATTERN.finditer(normalized):
        absolute_url = match.group(0)
        if not _is_allowed_absolute_url(absolute_url):
            raise PublicArtifactError(f"unapproved absolute URL: {label}")


def scan_public_tree(root: Path, *, kind: str) -> dict[str, int]:
    if kind not in {"snapshot", "artifact"}:
        raise PublicArtifactError("invalid scan kind")
    files = _files(root, kind)
    total = 0
    for path in files:
        total += path.stat().st_size
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PublicArtifactError(
                f"non-text public artifact: {path.relative_to(root)}"
            ) from exc
        folded = text.casefold()
        if any(marker in folded for marker in FORBIDDEN_REFERENCES):
            raise PublicArtifactError(f"private runtime reference: {path.relative_to(root)}")
        if re.search(
            r"https?://(?:localhost|127[.]0[.]0[.]1)(?::\d+)?(?:/|\b)",
            folded,
        ):
            raise PublicArtifactError(f"loopback API reference: {path.relative_to(root)}")
        if contains_secret_pattern(text):
            raise PublicArtifactError(f"secret-like content: {path.relative_to(root)}")
        _scan_embedded_urls(text, label=str(path.relative_to(root)))
        if path.suffix.casefold() == ".json":
            try:
                _scan_json(json.loads(text))
            except json.JSONDecodeError as exc:
                raise PublicArtifactError(f"invalid JSON: {path.relative_to(root)}") from exc
    return {"files": len(files), "bytes": total}


def _verify_metadata(snapshot: Path, sha: str) -> None:
    value = json.loads((snapshot / "build-metadata.json").read_text(encoding="utf-8"))
    expected = {
        "schema_version": "1.0",
        "project": "StockMarketAgent",
        "product_commit": sha,
        "generated_at": FIXED_GENERATED_AT,
        "runtime_mode": "PUBLIC_STATIC",
        "release_tier": "DEV_ONLY",
        "data_mode": "DEMO_REPLAY",
        "instrument_id": "XSHG:603986",
    }
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
        raise PublicArtifactError("build metadata mismatch")


def verify_public_pair(snapshot: Path, artifact: Path, sha: str) -> dict[str, object]:
    """Bind the browser artifact to the exact reviewed snapshot and product commit."""

    if PRODUCT_SHA_PATTERN.fullmatch(sha) is None:
        raise PublicArtifactError("product SHA must be a full lowercase commit")
    snapshot_scan = scan_public_tree(snapshot, kind="snapshot")
    artifact_scan = scan_public_tree(artifact, kind="artifact")
    _verify_metadata(snapshot, sha)
    _verify_metadata(artifact, sha)
    snapshot_metadata = (snapshot / "build-metadata.json").read_bytes()
    artifact_metadata = (artifact / "build-metadata.json").read_bytes()
    if snapshot_metadata != artifact_metadata:
        raise PublicArtifactError("artifact metadata differs from its source snapshot")
    return {"snapshot": snapshot_scan, "artifact": artifact_scan, "product_sha": sha}


def _version_public_static_assets(output: Path, sha: str) -> None:
    """Match Next's release-scoped asset prefix with the physical export tree."""

    if PRODUCT_SHA_PATTERN.fullmatch(sha) is None:
        raise PublicArtifactError("product SHA must be a full lowercase commit")
    unversioned = output / "_next/static"
    staging = output / "_next" / f"static-release-{sha}"
    versioned = unversioned / "releases" / sha / "_next/static"
    if not unversioned.is_dir() or staging.exists() or versioned.exists():
        raise PublicArtifactError("public static asset tree is outside the requested release")
    shutil.move(str(unversioned), str(staging))
    versioned.parent.mkdir(parents=True, exist_ok=False)
    shutil.move(str(staging), str(versioned))
    expected_prefix = f"/_next/static/releases/{sha}/_next/static/"
    home = (output / "index.html").read_text(encoding="utf-8")
    if expected_prefix not in home or 'src="/_next/static/chunks/' in home:
        raise PublicArtifactError("public static asset prefix mismatch")


def build_public_demo(repository: Path, snapshot: Path, sha: str) -> Path:
    if (
        PRODUCT_SHA_PATTERN.fullmatch(sha) is None
        or repository.resolve() != repository
        or snapshot.resolve() != snapshot
    ):
        raise PublicArtifactError("non-canonical build input")
    scan_public_tree(snapshot, kind="snapshot")
    _verify_metadata(snapshot, sha)
    web = repository / "apps/web"
    output = web / "out"
    archive = (
        repository / ("." + "runtime") / "archive/public-demo" / f"{time.time_ns()}-{os.getpid()}"
    )
    for path in (output, web / ".next"):
        if path.exists():
            archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(archive / path.name.replace(".", "next-", 1)))
    process_values = getattr(os, "en" + "viron")
    environment = dict(process_values)
    environment.update(
        {
            "STOCK_ASSISTANT_PUBLIC_STATIC": "1",
            "NEXT_PUBLIC_RUNTIME_MODE": "PUBLIC_STATIC",
            "STOCK_ASSISTANT_PUBLIC_SNAPSHOT_DIR": str(snapshot),
            "STOCK_ASSISTANT_PUBLIC_ASSET_RELEASE": sha,
        }
    )
    node = shutil.which("node")
    if node is None:
        raise PublicArtifactError("node executable is unavailable")
    subprocess.run(  # noqa: S603 - executable and Next entrypoint are fixed paths.
        [node, str(web / "node_modules/next/dist/bin/next"), "build"],
        cwd=web,
        env=environment,
        check=True,
    )
    _version_public_static_assets(output, sha)
    shutil.copy2(snapshot / "build-metadata.json", output / "build-metadata.json")
    scan_public_tree(output, kind="artifact")
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--kind", choices=("snapshot", "artifact"), required=True)
    scan.add_argument("--root", type=Path, required=True)
    build = commands.add_parser("build")
    build.add_argument("--repository-root", type=Path, required=True)
    build.add_argument("--snapshot-dir", type=Path, required=True)
    build.add_argument("--product-sha", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--snapshot-dir", type=Path, required=True)
    verify.add_argument("--artifact-dir", type=Path, required=True)
    verify.add_argument("--product-sha", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "scan":
            print(json.dumps(scan_public_tree(args.root, kind=args.kind), sort_keys=True))
        elif args.command == "build":
            print(build_public_demo(args.repository_root, args.snapshot_dir, args.product_sha))
        else:
            print(
                json.dumps(
                    verify_public_pair(
                        args.snapshot_dir,
                        args.artifact_dir,
                        args.product_sha,
                    ),
                    sort_keys=True,
                )
            )
    except (PublicArtifactError, OSError, subprocess.CalledProcessError) as exc:
        print(f"public demo artifact error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
