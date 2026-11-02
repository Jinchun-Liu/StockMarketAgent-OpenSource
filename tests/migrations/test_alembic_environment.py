"""Deterministic checks for the Alembic online connection paths."""

from __future__ import annotations

import runpy
from contextlib import nullcontext
from pathlib import Path
from types import CoroutineType

import pytest
from alembic import context
from alembic.config import Config
from sqlalchemy import make_url

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_ENV = REPOSITORY_ROOT / "infra" / "migrations" / "env.py"


def test_online_migrations_reuse_injected_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    config = Config()
    config.attributes["connection"] = connection
    database_url = (
        "postgresql+asyncpg://stock_admin:a%40b%3Ac%2Fd%3F%23%25@db.internal:5432/stock_assistant"
    )
    config.attributes["database_url"] = database_url
    configured_connections: list[object] = []
    migration_runs = 0

    def configure(*, connection: object, target_metadata: object) -> None:
        assert target_metadata is None
        configured_connections.append(connection)

    def run_migrations() -> None:
        nonlocal migration_runs
        migration_runs += 1

    def reject_asyncio_run(coroutine: object) -> None:
        if isinstance(coroutine, CoroutineType):
            coroutine.close()
        pytest.fail("an injected connection must not start a nested event loop")

    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", run_migrations)
    monkeypatch.setattr("asyncio.run", reject_asyncio_run)

    runpy.run_path(str(ALEMBIC_ENV))

    assert config.get_main_option("sqlalchemy.url") == database_url
    assert configured_connections == [connection]
    assert migration_runs == 1


def test_offline_migration_composes_split_ecs_database_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config()
    configured_urls: list[str] = []
    migration_runs = 0

    def configure(
        *,
        url: str,
        target_metadata: object,
        literal_binds: bool,
        dialect_opts: dict[str, str],
    ) -> None:
        assert target_metadata is None
        assert literal_binds is True
        assert dialect_opts == {"paramstyle": "named"}
        configured_urls.append(url)

    def run_migrations() -> None:
        nonlocal migration_runs
        migration_runs += 1

    monkeypatch.setenv("DB_HOST", "preview.cluster.internal")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "stock_assistant")
    monkeypatch.setenv("DB_USERNAME", "migration_user")
    monkeypatch.setenv("DB_PASSWORD", "synthetic migration pass")
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", run_migrations)

    runpy.run_path(str(ALEMBIC_ENV))

    assert len(configured_urls) == 1
    parsed = make_url(configured_urls[0])
    assert parsed.drivername == "postgresql+asyncpg"
    assert parsed.username == "migration_user"
    assert parsed.password == "synthetic migration pass"  # noqa: S105
    assert parsed.host == "preview.cluster.internal"
    assert parsed.port == 5432
    assert parsed.database == "stock_assistant"
    assert parsed.query == {"ssl": "require"}
    assert migration_runs == 1
