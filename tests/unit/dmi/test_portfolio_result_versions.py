"""Regression checks for explicit versions on PostgreSQL portfolio results."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PORTFOLIO_ADAPTER = Path(__file__).parents[3] / "src/stock_assistant/platform/db/portfolio.py"


@pytest.mark.parametrize(
    ("result_type", "expected_count"),
    [
        ("ActiveTargetLookupResult", 2),
        ("ActiveTargetWriteResult", 2),
    ],
)
def test_active_target_results_set_frozen_schema_version_explicitly(
    result_type: str,
    expected_count: int,
) -> None:
    module = ast.parse(PORTFOLIO_ADAPTER.read_text(encoding="utf-8"))
    constructors = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == result_type
    ]

    assert len(constructors) == expected_count
    for constructor in constructors:
        schema_version = next(
            (keyword.value for keyword in constructor.keywords if keyword.arg == "schema_version"),
            None,
        )
        assert isinstance(schema_version, ast.Constant)
        assert schema_version.value == "1.0"
