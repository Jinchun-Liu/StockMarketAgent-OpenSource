from pathlib import Path

import pytest
from scripts import package_check


def test_validate_scope_accepts_exact_files_and_directories() -> None:
    package_check.validate_scope(
        ("scripts/example.py", "tests/unit/test_example.py"),
        ("scripts/example.py", "tests/unit"),
    )


def test_validate_scope_rejects_changes_outside_authority() -> None:
    with pytest.raises(package_check.PackageCheckError, match=r"governance/delivery\.json"):
        package_check.validate_scope(
            ("scripts/example.py", "governance/delivery.json"),
            ("scripts",),
        )


@pytest.mark.parametrize("scope", ("", ".", "../scripts", "/absolute/scripts"))
def test_validate_scope_rejects_ambiguous_or_escaping_scope(scope: str) -> None:
    with pytest.raises(package_check.PackageCheckError, match="invalid write scope"):
        package_check.validate_scope(("scripts/example.py",), (scope,))


def test_focused_test_target_must_be_one_existing_pytest_target() -> None:
    target = (
        "tests/unit/test_package_check.py::test_validate_scope_accepts_exact_files_and_directories"
    )
    assert package_check._test_target(target) == target

    with pytest.raises(package_check.PackageCheckError, match="focused test"):
        package_check._test_target("src/stock_assistant/example.py")


@pytest.mark.parametrize(
    "target",
    (
        "/tests/unit/test_package_check.py",
        "../tests/unit/test_package_check.py",
        "tests/../src/stock_assistant/contracts/models.py",
        "tests/unit/../../pyproject.toml",
        r"tests\\unit\\test_package_check.py",
        "tests/unit",
    ),
)
def test_focused_test_target_rejects_absolute_escape_and_non_files(target: str) -> None:
    with pytest.raises(package_check.PackageCheckError):
        package_check._test_target(target)


def test_makefile_exposes_only_the_product_first_agent_targets() -> None:
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(encoding="utf-8")

    for target in ("agent-run:", "package-check:", "agent-review:", "integration-merge:"):
        assert makefile.count(target) == 1
    for retired in (
        "agent-run-v2:",
        "agent-review-v2:",
        "agent-seal:",
        "work-package-check:",
        "lightweight-delivery-check:",
    ):
        assert retired not in makefile
