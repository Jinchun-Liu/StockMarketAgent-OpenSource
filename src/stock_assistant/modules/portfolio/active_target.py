"""R1 application policy for the single fixed active target."""

from datetime import datetime
from uuid import UUID

from stock_assistant.contracts.active_targets import (
    ActiveTargetMutationResult,
    ActiveTargetState,
    ActiveTargetView,
)
from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.persistence import (
    ActiveTargetLookupRequest,
    ActiveTargetLookupResult,
    ActiveTargetWriteRequest,
    ActiveTargetWriteResult,
)
from stock_assistant.contracts.portfolio import (
    R1_FIXED_TARGET_ASSET_CLASS,
    R1_FIXED_TARGET_CURRENCY,
    R1_FIXED_TARGET_EXCHANGE,
    R1_FIXED_TARGET_INSTRUMENT,
    R1_FIXED_TARGET_INSTRUMENT_ID,
    R1_FIXED_TARGET_MARKET,
    R1_FIXED_TARGET_SYMBOL,
    R1_FIXED_TARGET_WATCHLIST_ID,
    R1_FIXED_TARGET_WATCHLIST_REVISION,
    ActiveTargetRevisionRef,
    Watchlist,
    WatchlistEntry,
)
from stock_assistant.ports.persistence import PortfolioRepositoryPort

R1_TARGET_LOCKED = "R1_TARGET_LOCKED"
R1_TARGET_INSTRUMENT_ID = R1_FIXED_TARGET_INSTRUMENT_ID
R1_TARGET_SYMBOL = R1_FIXED_TARGET_SYMBOL
R1_TARGET_EXCHANGE = R1_FIXED_TARGET_EXCHANGE
R1_TARGET_MARKET = R1_FIXED_TARGET_MARKET
R1_TARGET_ASSET_CLASS = R1_FIXED_TARGET_ASSET_CLASS
R1_TARGET_CURRENCY = R1_FIXED_TARGET_CURRENCY
R1_TARGET_NAME = "兆易创新"
R1_WATCHLIST_ID = R1_FIXED_TARGET_WATCHLIST_ID


def _fixed_instrument() -> InstrumentRef:
    return R1_FIXED_TARGET_INSTRUMENT.model_copy(deep=True)


R1_INSTRUMENT = _fixed_instrument()


class ActiveTargetLockedError(ValueError):
    """Raised before repository or source access for any non-R1 instrument."""

    code = R1_TARGET_LOCKED

    def __init__(self, instrument_id: str) -> None:
        super().__init__(f"{R1_TARGET_LOCKED}: instrument {instrument_id!r} is not available in R1")


def _is_fixed_instrument(instrument: InstrumentRef) -> bool:
    return (
        instrument.instrument_id == R1_TARGET_INSTRUMENT_ID
        and instrument.symbol == R1_TARGET_SYMBOL
        and instrument.exchange == R1_TARGET_EXCHANGE
        and instrument.market == R1_TARGET_MARKET
        and instrument.asset_class == R1_TARGET_ASSET_CLASS
        and instrument.currency == R1_TARGET_CURRENCY
    )


def _require_requested_target(requested_instrument: InstrumentRef | None) -> None:
    if requested_instrument is not None and not _is_fixed_instrument(requested_instrument):
        raise ActiveTargetLockedError(requested_instrument.instrument_id)


def _require_stored_fixed_revision(
    *,
    watchlist_id: UUID,
    revision: int,
    instrument: InstrumentRef,
) -> None:
    if (
        watchlist_id != R1_WATCHLIST_ID
        or revision != R1_FIXED_TARGET_WATCHLIST_REVISION
        or not _is_fixed_instrument(instrument)
    ):
        raise ValueError("repository did not preserve fixed R1 target revision 1")


def _state_from_lookup(result: ActiveTargetLookupResult) -> ActiveTargetState:
    if not result.found:
        return ActiveTargetState(
            schema_version="1.0",
            found=False,
            as_of=result.as_of,
        )
    if result.watchlist is None or result.active_target is None:
        raise ValueError("found active-target lookup omitted its aggregate")
    _require_stored_fixed_revision(
        watchlist_id=result.watchlist.watchlist_id,
        revision=result.watchlist.revision,
        instrument=result.active_target.instrument,
    )
    return _state(
        active_target=result.active_target,
        as_of=result.as_of,
    )


def _state(
    *,
    active_target: ActiveTargetRevisionRef,
    as_of: datetime,
) -> ActiveTargetState:
    return ActiveTargetState(
        schema_version="1.0",
        found=True,
        active_target=ActiveTargetView(
            instrument=active_target.instrument,
            revision=active_target.watchlist_revision,
            activated_at=active_target.activated_at,
        ),
        as_of=as_of,
    )


def _state_from_write(
    result: ActiveTargetWriteResult,
    *,
    requested_as_of: datetime,
) -> ActiveTargetState:
    _require_stored_fixed_revision(
        watchlist_id=result.watchlist.watchlist_id,
        revision=result.watchlist.revision,
        instrument=result.active_target.instrument,
    )
    if result.created != result.changed:
        raise ValueError("fixed R1 target save must be either a creation or an unchanged no-op")
    return _state(
        active_target=result.active_target,
        as_of=max(requested_as_of, result.stored_at),
    )


class FixedActiveTargetService:
    """Initialize revision 1 once and expose only read/no-op behavior afterward."""

    def __init__(self, repository: PortfolioRepositoryPort) -> None:
        self._repository = repository

    async def load(
        self,
        context: OperationContext,
        *,
        requested_instrument: InstrumentRef | None = None,
    ) -> ActiveTargetState:
        """Load the fixed target without producing a write side effect."""

        _require_requested_target(requested_instrument)
        result = await self._repository.get_active_target(
            ActiveTargetLookupRequest(
                schema_version="1.0",
                context=context,
                watchlist_id=R1_WATCHLIST_ID,
            )
        )
        return _state_from_lookup(result)

    async def initialize(
        self,
        context: MutationContext,
        *,
        requested_instrument: InstrumentRef | None = None,
    ) -> ActiveTargetMutationResult:
        """Create fixed revision 1, or return the existing revision as a no-op."""

        _require_requested_target(requested_instrument)
        lookup = await self._repository.get_active_target(
            ActiveTargetLookupRequest(
                schema_version="1.0",
                context=context,
                watchlist_id=R1_WATCHLIST_ID,
            )
        )
        if lookup.found:
            state = _state_from_lookup(lookup)
            if state.active_target is None:
                raise ValueError("found active-target lookup omitted active_target")
            return ActiveTargetMutationResult(
                schema_version="1.0",
                state=state,
                previous_revision=state.active_target.revision,
                created=False,
                changed=False,
                stored_at=state.active_target.activated_at,
            )

        instrument = _fixed_instrument()
        watchlist = Watchlist(
            watchlist_id=R1_WATCHLIST_ID,
            name=f"R1 fixed target: {R1_TARGET_NAME}",
            entries=[
                WatchlistEntry(
                    instrument=instrument,
                    added_at=context.as_of,
                    alert_enabled=True,
                )
            ],
            revision=R1_FIXED_TARGET_WATCHLIST_REVISION,
            updated_at=context.as_of,
        )
        active_target = ActiveTargetRevisionRef(
            schema_version="1.0",
            watchlist_id=R1_WATCHLIST_ID,
            watchlist_revision=R1_FIXED_TARGET_WATCHLIST_REVISION,
            instrument=instrument,
            activated_at=context.as_of,
        )
        stored = await self._repository.save_active_target(
            ActiveTargetWriteRequest(
                schema_version="1.0",
                context=context,
                watchlist=watchlist,
                active_target=active_target,
            )
        )
        state = _state_from_write(stored, requested_as_of=context.as_of)
        replayed_creation = stored.idempotent_replay and stored.created
        return ActiveTargetMutationResult(
            schema_version="1.0",
            state=state,
            previous_revision=(1 if replayed_creation or not stored.created else None),
            created=False if stored.idempotent_replay else stored.created,
            changed=False if stored.idempotent_replay else stored.changed,
            idempotent_replay=stored.idempotent_replay,
            stored_at=stored.stored_at,
        )
