"""Thin command-line composition root for one-shot continuous-preview tasks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Final, Protocol, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.runtime.behavior_semantics import (
    BEHAVIOR_SEMANTICS_HASH as _BEHAVIOR_SEMANTICS_HASH,
)
from apps.runtime.behavior_semantics import (
    BEHAVIOR_WINDOW_SEMANTICS as _BEHAVIOR_WINDOW_SEMANTICS,
)
from apps.runtime.coordinator import ContinuousPreviewCoordinator, RefreshResult
from apps.runtime.llm import LlmSmokeResult, run_llm_smoke
from apps.runtime.research import RuntimeResearchEnricher
from apps.runtime.source_smoke import run_source_smoke
from apps.worker.tasks import execute_behavior_compute_runtime
from stock_assistant.config import get_settings
from stock_assistant.contracts.behavior import BehaviorComputeRequest, PressureMode
from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.forecasting import MarketBar
from stock_assistant.contracts.market_data import (
    MarketBarRevision,
    MarketDataRevision,
    MarketDataRevisionAppendRequest,
)
from stock_assistant.contracts.sectors import ExchangeCalendarQuery, TradingDay, TradingDayStatus
from stock_assistant.platform.calendars import ExchangeCalendarsXshgAdapter
from stock_assistant.platform.db.behavior import PostgresMarketDataRepository
from stock_assistant.platform.publication.s3_snapshots import S3SnapshotPublisher
from stock_assistant.ports.persistence import BehaviorPressureRepositoryPort

_RUNTIME_ROLE: Final = "stock_preview"
_BEHAVIOR_ADAPTER_MODULE: Final = "stock_assistant.platform.db.behavior"
_SHANGHAI: Final = ZoneInfo("Asia/Shanghai")
_BEHAVIOR_CONTEXT_NAMESPACE: Final = UUID("133d0219-d3fa-505e-9a4d-171cd6b22bb8")
_DEV_RIGHTS_HASH: Final = hashlib.sha256(b"r1-dev-only-market-bars-v1").hexdigest()


class _BehaviorRepositoryFactory(Protocol):
    def __call__(self, engine: AsyncEngine) -> BehaviorPressureRepositoryPort: ...


def _behavior_repository(engine: AsyncEngine) -> BehaviorPressureRepositoryPort | None:
    """Load the DMI adapter after lane integration; absence remains an explicit abstention."""

    try:
        module = importlib.import_module(_BEHAVIOR_ADAPTER_MODULE)
    except ModuleNotFoundError as error:
        if error.name != _BEHAVIOR_ADAPTER_MODULE:
            raise
        return None
    factory = cast(_BehaviorRepositoryFactory, module.PostgresBehaviorPressureRepository)
    return factory(engine)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one bounded R1 preview operation")
    parser.add_argument(
        "operation",
        choices=(
            "migrate",
            "ingest",
            "publish",
            "catch-up",
            "seed",
            "seed-last-session",
            "llm-smoke",
            "source-smoke",
            "behavior-intraday",
            "behavior-post-close",
            "behavior-last-session",
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.operation == "migrate":
        master_url = _database_url(master=True)
        _run_migrations(master_url)
        asyncio.run(_provision_runtime_role(master_url))
        result: dict[str, object] = {"operation": "migrate", "status": "succeeded"}
    elif arguments.operation == "llm-smoke":
        result = {
            "operation": "llm-smoke",
            "status": "succeeded",
            "models": [asdict(item) for item in asyncio.run(_run_llm_smoke())],
        }
    elif arguments.operation == "source-smoke":
        result = asdict(asyncio.run(run_source_smoke(settings=get_settings())))
    elif arguments.operation == "behavior-last-session":
        result = asdict(asyncio.run(_run_behavior_last_session()))
    elif arguments.operation == "seed-last-session":
        result = asdict(asyncio.run(_run_seed_last_session()))
    elif arguments.operation in {"behavior-intraday", "behavior-post-close"}:
        result = asdict(asyncio.run(_run_behavior_refresh(arguments.operation)))
    else:
        result = asdict(asyncio.run(_run_refresh(arguments.operation)))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


async def _run_llm_smoke() -> tuple[LlmSmokeResult, ...]:
    now = datetime.now(UTC)
    return await run_llm_smoke(
        settings=get_settings(),
        context=OperationContext(
            schema_version="1.0",
            request_id=UUID("263436ca-47f9-5cba-b72f-f80b602a6854"),
            correlation_id=UUID("3ad8db36-9531-5866-8633-2c656e938b09"),
            actor_id="continuous-preview-operator",
            workspace_id="default",
            as_of=now,
            requested_at=now,
            locale="zh-CN",
            timezone="Asia/Shanghai",
        ),
    )


def _database_url(*, master: bool) -> str:
    """Use SQLAlchemy quoting so arbitrary generated credential characters remain safe."""

    prefix = "MASTER_DB_" if master else "DB_"
    values = {
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT"),
        "database": os.getenv("DB_NAME"),
        "username": os.getenv(f"{prefix}USERNAME"),
        "password": os.getenv(f"{prefix}PASSWORD"),
    }
    missing = sorted(key for key, value in values.items() if not value)
    if missing:
        raise RuntimeError(f"DATABASE_CONFIGURATION_INCOMPLETE:{','.join(missing)}")
    try:
        port = int(values["port"] or "")
    except ValueError as error:
        raise RuntimeError("DATABASE_PORT_INVALID") from error
    return URL.create(
        drivername="postgresql+asyncpg",
        username=values["username"],
        password=values["password"],
        host=values["host"],
        port=port,
        database=values["database"],
        query={"ssl": "require"},
    ).render_as_string(hide_password=False)


def _run_migrations(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


async def _provision_runtime_role(database_url: str) -> None:
    """Create/rotate the fixed app role and grant only runtime DML privileges."""

    username = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")
    database = os.getenv("DB_NAME")
    if username != _RUNTIME_ROLE or not password or not database:
        raise RuntimeError("RUNTIME_DATABASE_SECRET_INVALID")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            exists = await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                {"role": _RUNTIME_ROLE},
            )
            if exists is not True:
                await connection.execute(text(f"CREATE ROLE {_RUNTIME_ROLE} LOGIN"))
            password_literal = await connection.scalar(
                text("SELECT quote_literal(:password)"),
                {"password": password},
            )
            database_identifier = await connection.scalar(
                text("SELECT quote_ident(:database)"),
                {"database": database},
            )
            if not isinstance(password_literal, str) or not isinstance(database_identifier, str):
                raise RuntimeError("RUNTIME_DATABASE_QUOTING_FAILED")
            await connection.execute(
                text(f"ALTER ROLE {_RUNTIME_ROLE} PASSWORD {password_literal}")
            )
            await connection.execute(
                text(f"GRANT CONNECT ON DATABASE {database_identifier} TO {_RUNTIME_ROLE}")
            )
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {_RUNTIME_ROLE}"))
            await connection.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                    f"TO {_RUNTIME_ROLE}"
                )
            )
            await connection.execute(
                text(
                    "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public "
                    f"TO {_RUNTIME_ROLE}"
                )
            )
            await connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES "
                    f"TO {_RUNTIME_ROLE}"
                )
            )
            await connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT USAGE, SELECT, UPDATE ON SEQUENCES "
                    f"TO {_RUNTIME_ROLE}"
                )
            )
    finally:
        await engine.dispose()


def _behavior_window(
    run_at: datetime,
    *,
    mode: PressureMode,
) -> tuple[datetime, datetime] | None:
    local = run_at.astimezone(_SHANGHAI)
    if mode is PressureMode.POST_CLOSE:
        if local.timetz().replace(tzinfo=None) < time(15, 10):
            return None
        window_end = local.replace(hour=15, minute=0, second=0, microsecond=0)
        return window_end - timedelta(minutes=10), window_end
    rounded = local.replace(
        minute=(local.minute // 10) * 10,
        second=0,
        microsecond=0,
    )
    clock = rounded.timetz().replace(tzinfo=None)
    in_morning = time(9, 40) <= clock <= time(11, 30)
    in_afternoon = time(13, 10) <= clock <= time(15, 0)
    if not (in_morning or in_afternoon):
        return None
    return rounded - timedelta(minutes=10), rounded


async def _is_xshg_trading_day(run_at: datetime) -> bool:
    local_date = run_at.astimezone(_SHANGHAI).date()
    context = OperationContext(
        schema_version="1.0",
        request_id=uuid5(_BEHAVIOR_CONTEXT_NAMESPACE, f"calendar:{run_at.isoformat()}"),
        correlation_id=uuid5(
            _BEHAVIOR_CONTEXT_NAMESPACE,
            f"behavior:{local_date.isoformat()}",
        ),
        actor_id="r1-behavior-scheduler",
        workspace_id="default",
        as_of=run_at,
        requested_at=run_at,
        locale="zh-CN",
        timezone="Asia/Shanghai",
    )
    calendar = await ExchangeCalendarsXshgAdapter().get_calendar(
        ExchangeCalendarQuery(
            schema_version="1.0",
            context=context,
            exchanges=["XSHG"],
            date_from=local_date,
            date_to=local_date,
        )
    )
    current = next(
        (
            item
            for item in calendar.days
            if item.exchange == "XSHG" and item.trading_date == local_date
        ),
        None,
    )
    return current is not None and current.status.value in {"open", "partial"}


def _behavior_last_session_window(
    run_at: datetime,
    trading_days: tuple[TradingDay, ...],
) -> tuple[datetime, datetime]:
    """Return the last post-close window visible to the latest completed premarket session."""

    if run_at.tzinfo is None or run_at.utcoffset() is None:
        raise ValueError("run_at must be timezone-aware")
    local_date = run_at.astimezone(_SHANGHAI).date()
    open_dates = sorted(
        day.trading_date
        for day in trading_days
        if day.exchange == "XSHG"
        and day.status in {TradingDayStatus.OPEN, TradingDayStatus.PARTIAL}
        and day.trading_date <= local_date
    )
    if len(open_dates) < 2:
        raise RuntimeError("RECENT_XSHG_BEHAVIOR_WINDOW_UNAVAILABLE")
    window_end = datetime.combine(open_dates[-2], time(15, 0), _SHANGHAI)
    return window_end - timedelta(minutes=10), window_end


def _behavior_refresh_identity(
    *,
    operation: str,
    mode: PressureMode,
    window_end: datetime,
) -> str:
    """Keep PIT backfills disjoint from the scheduled window's idempotency receipt."""

    prefix = "backfill:" if operation == "behavior-last-session" else ""
    return f"{prefix}{mode.value}:{_BEHAVIOR_WINDOW_SEMANTICS}:{window_end.isoformat()}"


async def _latest_premarket_behavior_window(run_at: datetime) -> tuple[datetime, datetime]:
    local_date = run_at.astimezone(_SHANGHAI).date()
    context = OperationContext(
        schema_version="1.0",
        request_id=uuid5(_BEHAVIOR_CONTEXT_NAMESPACE, f"backfill-calendar:{run_at.isoformat()}"),
        correlation_id=uuid5(
            _BEHAVIOR_CONTEXT_NAMESPACE,
            f"behavior-backfill:{local_date.isoformat()}",
        ),
        actor_id="r1-behavior-release-operator",
        workspace_id="default",
        as_of=run_at,
        requested_at=run_at,
        locale="zh-CN",
        timezone="Asia/Shanghai",
    )
    calendar = await ExchangeCalendarsXshgAdapter().get_calendar(
        ExchangeCalendarQuery(
            schema_version="1.0",
            context=context,
            exchanges=["XSHG"],
            date_from=local_date - timedelta(days=14),
            date_to=local_date,
        )
    )
    return _behavior_last_session_window(run_at, tuple(calendar.days))


def _dev_market_bars(
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[MarketBarRevision]:
    instrument = InstrumentRef(
        instrument_id="XSHG:603986",
        symbol="603986",
        exchange="XSHG",
        market="CN",
        asset_class="equity",
        currency="CNY",
    )
    revisions: list[MarketBarRevision] = []
    for index in range(10):
        opened_at = window_start + timedelta(minutes=index)
        closed_at = opened_at + timedelta(minutes=1)
        open_price = Decimal("80") + Decimal(index) / Decimal("10")
        close_price = open_price + Decimal(index % 3 - 1) / Decimal("10")
        identity = f"{window_end.isoformat()}:{index}"
        bar = MarketBar(
            instrument=instrument,
            interval="1min",
            opened_at=opened_at,
            closed_at=closed_at,
            received_at=closed_at,
            open_price=open_price,
            high_price=max(open_price, close_price) + Decimal("0.2"),
            low_price=min(open_price, close_price) - Decimal("0.2"),
            close_price=close_price,
            volume=Decimal(1000 + index * 100),
            adjusted=False,
            source_id="dev_only_fixture",
            data_version="r1-dev-only-v1",
            adjustment_version="none",
            rights_snapshot_hash=_DEV_RIGHTS_HASH,
        )
        revisions.append(
            MarketBarRevision(
                revision_id=uuid5(_BEHAVIOR_CONTEXT_NAMESPACE, identity),
                instrument=instrument,
                effective_at=closed_at,
                available_at=closed_at,
                first_seen_at=closed_at,
                revision_known_at=closed_at,
                source_id="dev_only_fixture",
                rights_snapshot_hash=_DEV_RIGHTS_HASH,
                content_hash=hashlib.sha256(bar.model_dump_json().encode("utf-8")).hexdigest(),
                bar=bar,
            )
        )
    return revisions


async def _run_behavior_refresh(operation: str) -> RefreshResult:
    run_at = datetime.now(UTC)
    mode = (
        PressureMode.INTRADAY_10M if operation == "behavior-intraday" else PressureMode.POST_CLOSE
    )
    window = _behavior_window(run_at, mode=mode)
    if window is None or not await _is_xshg_trading_day(run_at):
        return RefreshResult(operation=operation, status="NO_OP")
    return await _execute_behavior_refresh(
        operation=operation,
        run_at=run_at,
        mode=mode,
        window=window,
        context_as_of=run_at,
    )


async def _run_behavior_last_session() -> RefreshResult:
    run_at = datetime.now(UTC)
    window = await _latest_premarket_behavior_window(run_at)
    return await _execute_behavior_refresh(
        operation="behavior-last-session",
        run_at=run_at,
        mode=PressureMode.POST_CLOSE,
        window=window,
        context_as_of=window[1],
    )


async def _run_seed_last_session() -> RefreshResult:
    """Backfill the compatible behavior baseline before publishing a fresh environment."""

    await _run_behavior_last_session()
    return await _run_refresh("seed-last-session")


async def _execute_behavior_refresh(
    *,
    operation: str,
    run_at: datetime,
    mode: PressureMode,
    window: tuple[datetime, datetime],
    context_as_of: datetime,
) -> RefreshResult:
    engine = create_async_engine(
        _database_url(master=False),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )
    try:
        identity = _behavior_refresh_identity(
            operation=operation,
            mode=mode,
            window_end=window[1],
        )
        append_context = MutationContext(
            schema_version="1.0",
            request_id=uuid5(_BEHAVIOR_CONTEXT_NAMESPACE, f"append:{identity}"),
            correlation_id=uuid5(_BEHAVIOR_CONTEXT_NAMESPACE, identity),
            actor_id="r1-behavior-scheduler",
            workspace_id="default",
            as_of=context_as_of,
            requested_at=run_at,
            idempotency_key=f"r1-behavior-bars:{identity}",
            locale="zh-CN",
            timezone="Asia/Shanghai",
        )
        revisions: list[MarketDataRevision] = []
        revisions.extend(
            _dev_market_bars(
                window_start=window[0],
                window_end=window[1],
            )
        )
        appended = await PostgresMarketDataRepository(engine).append_revisions(
            MarketDataRevisionAppendRequest(
                schema_version="1.0",
                context=append_context,
                revisions=revisions,
            )
        )
        compute_context = append_context.model_copy(
            update={
                "request_id": uuid5(_BEHAVIOR_CONTEXT_NAMESPACE, f"compute:{identity}"),
                "idempotency_key": f"r1-behavior-compute:{identity}",
            }
        )
        await execute_behavior_compute_runtime(
            BehaviorComputeRequest(
                schema_version="1.0",
                context=compute_context,
                mode=mode,
                window_start=window[0],
                window_end=window[1],
                diagnostic_semantics_hash=_BEHAVIOR_SEMANTICS_HASH,
                data_manifest_hash=appended.manifest.content_hash,
                rights_snapshot_hash=_DEV_RIGHTS_HASH,
            ),
            engine=engine,
            maintain_transition_state=operation != "behavior-last-session",
        )
        return RefreshResult(operation=operation)
    finally:
        await engine.dispose()


async def _run_refresh(operation: str) -> RefreshResult:
    settings = get_settings()
    if settings.app_env != "preview":
        raise RuntimeError("CONTINUOUS_PREVIEW_REQUIRES_PREVIEW_ENV")
    settings.assert_safe_production()
    if (
        settings.public_snapshot_bucket is None
        or settings.public_base_url is None
        or settings.product_commit is None
    ):
        raise RuntimeError("CONTINUOUS_PREVIEW_PUBLICATION_CONFIG_MISSING")
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=5,
            read_timeout=15,
            retries={"mode": "standard", "max_attempts": 3},
            request_checksum_calculation="when_supported",
            response_checksum_validation="when_supported",
        ),
    )
    engine: AsyncEngine = create_async_engine(
        _database_url(master=False),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
    )
    try:
        coordinator = ContinuousPreviewCoordinator(
            engine=engine,
            calendar=ExchangeCalendarsXshgAdapter(),
            publisher=S3SnapshotPublisher(
                client=client,
                bucket=settings.public_snapshot_bucket,
            ),
            product_commit=settings.product_commit,
            advisory_lock_id=settings.snapshot_advisory_lock_id,
            freshness_grace_minutes=settings.snapshot_freshness_grace_minutes,
            public_base_url=settings.public_base_url,
            public_smoke_attempts=settings.public_smoke_attempts,
            research_enricher=RuntimeResearchEnricher(
                settings=settings,
                behavior_repository=_behavior_repository(engine),
                behavior_semantics_hash=_BEHAVIOR_SEMANTICS_HASH,
            ),
        )
        run_at = datetime.now(UTC)
        if operation == "ingest":
            return await coordinator.ingest(run_at=run_at)
        if operation == "publish":
            return await coordinator.publish(run_at=run_at)
        if operation == "catch-up":
            return await coordinator.catch_up(run_at=run_at)
        if operation == "seed":
            return await coordinator.seed(run_at=run_at)
        if operation == "seed-last-session":
            return await coordinator.seed_last_session(run_at=run_at)
        raise AssertionError("argument parser admitted an unsupported operation")
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover - use the container entrypoint
    raise SystemExit(main())
