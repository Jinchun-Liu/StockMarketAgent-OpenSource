from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_r1_functional_python_dependency_is_exact_and_locked() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_dependencies = project["project"]["optional-dependencies"]["data"]

    assert "baostock==0.9.3" in data_dependencies

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    assert packages["baostock"]["version"] == "0.9.3"

    project_lock = packages["stock-market-agent"]
    locked_data_dependencies = project_lock["optional-dependencies"]["data"]
    assert {"name": "baostock"} in locked_data_dependencies


def test_r1_functional_browser_dependency_is_exact_and_locked() -> None:
    package = json.loads((ROOT / "apps/web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "apps/web/package-lock.json").read_text(encoding="utf-8"))

    assert package["devDependencies"]["@playwright/test"] == "1.62.1"
    assert lock["packages"][""]["devDependencies"]["@playwright/test"] == "1.62.1"

    playwright = lock["packages"]["node_modules/@playwright/test"]
    assert playwright["version"] == "1.62.1"
    assert playwright["license"] == "Apache-2.0"


def test_r1_functional_dependencies_have_reuse_and_license_records() -> None:
    registry = json.loads((ROOT / "governance/reuse-decisions.json").read_text(encoding="utf-8"))
    decisions = {decision["id"]: decision for decision in registry["decisions"]}
    decision = decisions["r1-functional-dependencies-2026-08-02"]

    assert decision["status"] == "accepted"
    assert decision["final_decision"] == "adopt"
    assert decision["third_party_registry_terms"] == ["BaoStock", "Playwright"]

    record = ROOT / decision["record"]
    assert "<!-- reuse-decision-id:r1-functional-dependencies-2026-08-02 -->" in (
        record.read_text(encoding="utf-8")
    )

    third_party = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    assert "| BaoStock | present as exact optional data dependency" in third_party
    assert "| Playwright | present as exact Web development dependency" in third_party
