#!/usr/bin/env python3
"""Validate the small product-first governance surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_FILES = (
    "scripts/agent_executor.py",
    "scripts/check_lightweight_delivery.py",
    "scripts/feature_delivery.py",
    "scripts/generate_department_playbooks.py",
    "scripts/git_traceability.py",
    "scripts/handoff_validation.py",
    "scripts/render_work_package_prompt.py",
    "scripts/check_remote_traceability.py",
    "governance/agent-effective-capabilities.schema.json",
    "governance/agent-execution-receipt.schema.json",
    "governance/lightweight-delivery.schema.json",
    "governance/lightweight-review-result.schema.json",
    "governance/work-package-manifest.schema.json",
    "governance/work-package-manifest.v2.schema.json",
    "governance/work-package-seal.schema.json",
)
FORBIDDEN_ACTIVE_TERMS = (
    "agent-run-v2",
    "agent-review-v2",
    "agent-seal:",
    "agent-release:",
    "work-package-seal:",
    "feature-action:",
    "integration-action:",
    "work-package-check:",
    "lightweight-delivery-check:",
    "department-playbooks-check:",
    "remote-traceability:",
    "remote-governance:",
)
REQUIRED_AGENT_RULES = (
    "## Product-First Delivery Budget",
    "User Capability",
    "Runnable Path",
    "Acceptance Evidence",
    "Non-goals",
    "## Stale Workspace Refusal",
    "## Agent May Stop",
    "## Verification Tiers",
    "rights fail-closed",
    "`evidence_id`",
    "PIT",
    "幂等",
    "`main --ff-only`",
    "revert",
    "真实交易",
)
REQUIRED_CI_MARKERS = (
    "governance:",
    "python-quality:",
    "web-quality:",
    "migrations:",
    "integration:",
    "e2e:",
    "parallel-isolation:",
    "quality-gate:",
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        standards = _load(root / "governance/standards.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"cannot load governance/standards.json: {exc}"]

    for key in ("required_agent_documents", "required_governance_files"):
        values = standards.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            errors.append(f"governance/standards.json.{key} must be a string list")
            continue
        for relative in values:
            if not (root / relative).is_file():
                errors.append(f"required file is missing: {relative}")

    markers = standards.get("document_markers")
    if not isinstance(markers, dict):
        errors.append("governance/standards.json.document_markers must be an object")
    else:
        for relative, marker in markers.items():
            if not isinstance(relative, str) or not isinstance(marker, str):
                errors.append("document marker entries must be strings")
                continue
            path = root / relative
            if path.is_file() and marker not in path.read_text(encoding="utf-8"):
                errors.append(f"{relative} is missing marker {marker}")

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    for required in REQUIRED_AGENT_RULES:
        if required not in agents:
            errors.append(f"AGENTS.md must retain product-first safeguard: {required}")
    if len(agents.splitlines()) >= 200:
        errors.append("AGENTS.md must remain below 200 lines")
    if (root / "CLAUDE.md").read_text(encoding="utf-8").strip() != "@AGENTS.md":
        errors.append("CLAUDE.md must import AGENTS.md directly")

    makefile = (root / "Makefile").read_text(encoding="utf-8")
    for term in FORBIDDEN_ACTIVE_TERMS:
        if term in makefile:
            errors.append(f"Makefile still exposes legacy target: {term.rstrip(':')}")
    for target in ("agent-run:", "package-check:", "agent-review:", "integration-merge:"):
        if target not in makefile:
            errors.append(f"Makefile must retain minimal target: {target.rstrip(':')}")

    for relative in LEGACY_FILES:
        if (root / relative).exists():
            errors.append(f"legacy governance file remains active: {relative}")

    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for marker in REQUIRED_CI_MARKERS:
        if marker not in ci:
            errors.append(f"CI must retain hard gate: {marker.rstrip(':')}")
    if "refs/ci/verified" in ci or "contents: write" in ci:
        errors.append("CI must not publish a custom verification ref")

    for hook_file in (".codex/hooks.json", ".claude/settings.json"):
        try:
            hooks = _load(root / hook_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"cannot load {hook_file}: {exc}")
            continue
        if "scripts/agent_guard.py" not in json.dumps(hooks):
            errors.append(f"{hook_file} must invoke scripts/agent_guard.py")
        registered = hooks.get("hooks")
        if isinstance(registered, dict) and "Stop" in registered:
            errors.append(f"{hook_file} must not register a Stop hook")

    precommit = (root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    if "pass_filenames: false" in precommit or "repo: local" in precommit:
        errors.append("pre-commit must remain limited to changed-file hooks")

    safety = standards.get("safety_invariants")
    if not isinstance(safety, list) or len(safety) < 9:
        errors.append("governance/standards.json must retain the safety invariants")
    return errors


def main() -> int:
    try:
        errors = validate()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        print("Governance check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Governance check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
