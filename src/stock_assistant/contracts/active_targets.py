"""Public active-target HTTP transfer contracts."""

from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import InstrumentRef


class ActiveTargetSelectionRequest(ContractModel):
    """Select one instrument by canonical id or by symbol and exchange."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "provide exactly one selector: instrument_id or symbol + exchange"
            ]
        }
    )

    schema_version: Literal["1.0"] = "1.0"
    instrument_id: str | None = Field(
        default=None,
        pattern=r"^[A-Z0-9_]+:[A-Za-z0-9._-]+$",
    )
    symbol: str | None = Field(default=None, min_length=1, max_length=64)
    exchange: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,32}$")
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def selector_must_be_unambiguous(self) -> "ActiveTargetSelectionRequest":
        canonical = self.instrument_id is not None
        market_pair = self.symbol is not None and self.exchange is not None
        partial_pair = (self.symbol is None) != (self.exchange is None)
        if partial_pair or canonical == market_pair:
            raise ValueError("provide exactly one selector: instrument_id or symbol with exchange")
        return self


class ActiveTargetView(ContractModel):
    """Resolved active target without internal watchlist or repository details."""

    instrument: InstrumentRef
    revision: int = Field(ge=1)
    activated_at: AwareDatetime


class ActiveTargetState(ContractModel):
    """Point-in-time state returned by GET active-target."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "found is true if and only if active_target is present",
                "active_target.activated_at is <= as_of when active_target is present",
            ]
        }
    )

    schema_version: Literal["1.0"] = "1.0"
    found: bool
    active_target: ActiveTargetView | None = None
    as_of: AwareDatetime

    @model_validator(mode="after")
    def found_must_match_target(self) -> "ActiveTargetState":
        if self.found != (self.active_target is not None):
            raise ValueError("found must match active_target presence")
        if self.active_target is not None and self.active_target.activated_at > self.as_of:
            raise ValueError("active target cannot be activated after as_of")
        return self


class ActiveTargetMutationResult(ContractModel):
    """Result of one idempotent active-target selection."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "state contains a found active target",
                "stored_at is <= state.as_of",
                (
                    "created=true requires previous_revision=null, target revision 1, "
                    "and changed=true"
                ),
                "created=false requires previous_revision",
                "changed=true increments previous_revision exactly once",
                "changed=false preserves previous_revision",
                "idempotent_replay=true requires created=false and changed=false",
            ]
        }
    )

    schema_version: Literal["1.0"] = "1.0"
    state: ActiveTargetState
    previous_revision: int | None = Field(default=None, ge=1)
    created: bool
    changed: bool
    idempotent_replay: bool = False
    stored_at: AwareDatetime

    @model_validator(mode="after")
    def mutation_revision_must_be_consistent(self) -> "ActiveTargetMutationResult":
        target = self.state.active_target
        if target is None:
            raise ValueError("active target mutation must return a found target")
        if self.stored_at > self.state.as_of:
            raise ValueError("stored_at cannot be later than state.as_of")
        if self.idempotent_replay and (self.created or self.changed):
            raise ValueError("idempotent replay cannot claim a created or changed mutation")
        if self.created:
            if self.previous_revision is not None or target.revision != 1 or not self.changed:
                raise ValueError(
                    "created target requires no previous revision, revision 1 and changed=true"
                )
        elif self.previous_revision is None:
            raise ValueError("existing target mutation requires previous_revision")
        elif self.changed and target.revision != self.previous_revision + 1:
            raise ValueError("changed target revision must increment exactly once")
        elif not self.changed and target.revision != self.previous_revision:
            raise ValueError("no-op target mutation must preserve revision")
        return self
