from asyncio import run
from datetime import UTC, datetime, timedelta

import pytest

from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.persistence import (
    ActiveTargetLookupRequest,
    ActiveTargetLookupResult,
    ActiveTargetWriteRequest,
    ActiveTargetWriteResult,
    PortfolioLookupRequest,
    PortfolioLookupResult,
    PortfolioSnapshotWriteRequest,
    PortfolioSnapshotWriteResult,
)
from stock_assistant.contracts.portfolio import ActiveTargetRevisionRef, Watchlist, WatchlistEntry
from stock_assistant.modules.portfolio.active_target import (
    R1_INSTRUMENT,
    R1_TARGET_LOCKED,
    R1_WATCHLIST_ID,
    ActiveTargetLockedError,
    FixedActiveTargetService,
)

NOW = datetime(2026, 7, 28, 1, 5, tzinfo=UTC)
LATER = NOW + timedelta(seconds=1)


def mutation_context() -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=NOW,
        requested_at=NOW,
        idempotency_key="r1-fixed-target-initialize",
    )


def fixed_aggregate(
    *,
    instrument: InstrumentRef = R1_INSTRUMENT,
    revision: int = 1,
    activated_at: datetime = NOW,
) -> tuple[Watchlist, ActiveTargetRevisionRef]:
    watchlist = Watchlist(
        watchlist_id=R1_WATCHLIST_ID,
        name="R1 fixed target: 兆易创新",
        entries=[
            WatchlistEntry(
                instrument=instrument,
                added_at=activated_at,
                alert_enabled=True,
            )
        ],
        revision=revision,
        updated_at=activated_at,
    )
    active_target = ActiveTargetRevisionRef(
        schema_version="1.0",
        watchlist_id=R1_WATCHLIST_ID,
        watchlist_revision=revision,
        instrument=instrument,
        activated_at=activated_at,
    )
    return watchlist, active_target


class FakePortfolioRepository:
    def __init__(
        self,
        *,
        lookup_result: ActiveTargetLookupResult | None = None,
        save_result: ActiveTargetWriteResult | None = None,
    ) -> None:
        self.lookup_requests: list[ActiveTargetLookupRequest] = []
        self.write_requests: list[ActiveTargetWriteRequest] = []
        self.lookup_result = lookup_result
        self.save_result = save_result

    async def get_latest(self, request: PortfolioLookupRequest) -> PortfolioLookupResult:
        raise AssertionError(f"unexpected portfolio lookup: {request!r}")

    async def save_snapshot(
        self, request: PortfolioSnapshotWriteRequest
    ) -> PortfolioSnapshotWriteResult:
        raise AssertionError(f"unexpected portfolio write: {request!r}")

    async def get_active_target(
        self, request: ActiveTargetLookupRequest
    ) -> ActiveTargetLookupResult:
        self.lookup_requests.append(request)
        if self.lookup_result is not None:
            return self.lookup_result
        return ActiveTargetLookupResult(
            schema_version="1.0",
            found=False,
            as_of=request.context.as_of,
        )

    async def save_active_target(
        self, request: ActiveTargetWriteRequest
    ) -> ActiveTargetWriteResult:
        self.write_requests.append(request)
        if self.save_result is not None:
            return self.save_result
        return ActiveTargetWriteResult(
            schema_version="1.0",
            watchlist=request.watchlist,
            active_target=request.active_target,
            created=True,
            changed=True,
            stored_at=request.context.as_of,
        )


def test_first_initialization_creates_fixed_target_revision_one() -> None:
    repository = FakePortfolioRepository()
    result = run(FixedActiveTargetService(repository).initialize(mutation_context()))

    assert result.created is True
    assert result.changed is True
    assert result.previous_revision is None
    assert result.state.active_target is not None
    assert result.state.active_target.instrument == R1_INSTRUMENT
    assert result.state.active_target.revision == 1
    assert len(repository.lookup_requests) == 1
    assert len(repository.write_requests) == 1


def test_concurrent_save_noop_maps_existing_revision_and_stored_time() -> None:
    watchlist, active_target = fixed_aggregate(activated_at=LATER)
    repository = FakePortfolioRepository(
        save_result=ActiveTargetWriteResult(
            schema_version="1.0",
            watchlist=watchlist,
            active_target=active_target,
            created=False,
            changed=False,
            idempotent_replay=True,
            stored_at=LATER,
        )
    )

    result = run(FixedActiveTargetService(repository).initialize(mutation_context()))

    assert result.previous_revision == 1
    assert result.created is False
    assert result.changed is False
    assert result.idempotent_replay is True
    assert result.stored_at == LATER
    assert result.state.as_of == LATER


def test_concurrent_replay_of_original_creation_is_reported_as_noop() -> None:
    watchlist, active_target = fixed_aggregate(activated_at=LATER)
    repository = FakePortfolioRepository(
        save_result=ActiveTargetWriteResult(
            schema_version="1.0",
            watchlist=watchlist,
            active_target=active_target,
            created=True,
            changed=True,
            idempotent_replay=True,
            stored_at=LATER,
        )
    )

    result = run(FixedActiveTargetService(repository).initialize(mutation_context()))

    assert result.previous_revision == 1
    assert result.created is False
    assert result.changed is False
    assert result.idempotent_replay is True


def test_repeated_initialization_is_noop_without_write() -> None:
    watchlist, active_target = fixed_aggregate()
    repository = FakePortfolioRepository(
        lookup_result=ActiveTargetLookupResult(
            schema_version="1.0",
            found=True,
            watchlist=watchlist,
            active_target=active_target,
            as_of=NOW,
        )
    )

    service = FixedActiveTargetService(repository)
    results = [run(service.initialize(mutation_context())) for _ in range(100)]

    assert all(result.created is False for result in results)
    assert all(result.changed is False for result in results)
    assert all(result.previous_revision == 1 for result in results)
    assert len(repository.lookup_requests) == 100
    assert repository.write_requests == []


def test_load_is_read_only() -> None:
    watchlist, active_target = fixed_aggregate()
    repository = FakePortfolioRepository(
        lookup_result=ActiveTargetLookupResult(
            schema_version="1.0",
            found=True,
            watchlist=watchlist,
            active_target=active_target,
            as_of=NOW,
        )
    )

    state = run(
        FixedActiveTargetService(repository).load(
            OperationContext(schema_version="1.0", as_of=NOW, requested_at=NOW)
        )
    )

    assert state.found is True
    assert state.active_target is not None
    assert state.active_target.instrument == R1_INSTRUMENT
    assert repository.write_requests == []


@pytest.mark.parametrize("operation", ["initialize", "load"])
def test_other_instrument_is_locked_before_repository_access(operation: str) -> None:
    repository = FakePortfolioRepository()
    service = FixedActiveTargetService(repository)
    other = InstrumentRef(
        instrument_id="XSHE:000001",
        symbol="000001.SZ",
        exchange="XSHE",
        market="CN",
    )

    with pytest.raises(ActiveTargetLockedError, match=R1_TARGET_LOCKED) as raised:
        if operation == "initialize":
            run(service.initialize(mutation_context(), requested_instrument=other))
        else:
            run(
                service.load(
                    OperationContext(
                        schema_version="1.0",
                        as_of=NOW,
                        requested_at=NOW,
                    ),
                    requested_instrument=other,
                )
            )

    assert raised.value.code == R1_TARGET_LOCKED
    assert repository.lookup_requests == []
    assert repository.write_requests == []


@pytest.mark.parametrize(
    ("source", "instrument", "revision"),
    [
        (
            "lookup",
            InstrumentRef(
                instrument_id="XSHG:603986",
                symbol="603986.XSHG",
                exchange="XSHG",
                market="CN",
            ),
            1,
        ),
        ("lookup", R1_INSTRUMENT, 2),
        (
            "save",
            InstrumentRef(
                instrument_id="XSHG:603986",
                symbol="603986.XSHG",
                exchange="XSHG",
                market="CN",
            ),
            1,
        ),
        ("save", R1_INSTRUMENT, 2),
    ],
)
def test_repository_result_must_preserve_fixed_identity_and_revision(
    source: str,
    instrument: InstrumentRef,
    revision: int,
) -> None:
    watchlist, active_target = fixed_aggregate(instrument=instrument, revision=revision)
    lookup_result = None
    save_result = None
    if source == "lookup":
        lookup_result = ActiveTargetLookupResult(
            schema_version="1.0",
            found=True,
            watchlist=watchlist,
            active_target=active_target,
            as_of=NOW,
        )
    else:
        save_result = ActiveTargetWriteResult(
            schema_version="1.0",
            watchlist=watchlist,
            active_target=active_target,
            created=False,
            changed=False,
            stored_at=NOW,
        )
    repository = FakePortfolioRepository(
        lookup_result=lookup_result,
        save_result=save_result,
    )

    with pytest.raises(ValueError, match="fixed R1 target revision 1"):
        run(FixedActiveTargetService(repository).initialize(mutation_context()))
