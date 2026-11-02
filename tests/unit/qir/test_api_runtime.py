from __future__ import annotations

import pytest
from apps.api.runtime import database_url_from_environment


def test_api_database_url_uses_separately_injected_runtime_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "stock_assistant")
    monkeypatch.setenv("DB_USERNAME", "stock_preview")
    monkeypatch.setenv("DB_PASSWORD", "synthetic@value")

    rendered = database_url_from_environment("postgresql+asyncpg://unused")

    assert "stock_preview" in rendered
    assert "synthetic@value" not in rendered
    assert rendered.endswith("?ssl=require")


def test_api_database_url_fails_closed_on_partial_ecs_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_HOST", "db.internal")
    with pytest.raises(RuntimeError, match="API_DATABASE_CONFIGURATION_INCOMPLETE"):
        database_url_from_environment("postgresql+asyncpg://unused")
