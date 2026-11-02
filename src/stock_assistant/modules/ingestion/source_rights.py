"""Fail-closed R1 capability rights checks for the two required sources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import (
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
)
from stock_assistant.contracts.portfolio import R1_FIXED_TARGET_INSTRUMENT_ID
from stock_assistant.ports.ingestion import RightsPolicyPort

CNINFO_DATA_SERVICE: Final = "cninfo_data_service"
CHOICE_QUANT_API: Final = "choice_quant_api"
R1_INSTRUMENT_ID: Final = R1_FIXED_TARGET_INSTRUMENT_ID

_R1_SYMBOL_ALIASES: Final = frozenset({R1_INSTRUMENT_ID, "603986.SH"})
_R1_CAPABILITIES: Final = {
    CNINFO_DATA_SERVICE: (
        "statutory_announcements",
        "financial_changes",
    ),
    CHOICE_QUANT_API: (
        "financial_changes",
        "market_snapshot",
        "company_industry_news",
        "macro_flashes",
    ),
}
_REQUIRED_OPERATIONS: Final = (
    RightsOperation.FETCH,
    RightsOperation.RETAIN,
    RightsOperation.DERIVE,
)
_REQUIRED_PERMITTED_USES: Final = (
    RightsPermittedUse.DISPLAY,
    RightsPermittedUse.SUMMARY,
    RightsPermittedUse.NON_DISPLAY,
    RightsPermittedUse.AI_USE,
)


class SourceRightsStatus(StrEnum):
    """Stable source status for downstream gap handling."""

    ELIGIBLE = "eligible"
    RIGHTS_BLOCKED = "rights_blocked"


@dataclass(frozen=True, slots=True)
class CapabilityRightsStatus:
    """Rights outcome for exactly one capability of one source."""

    capability: str
    status: SourceRightsStatus
    reason_code: str | None = None
    failing_operation: RightsOperation | None = None
    failing_permitted_use: RightsPermittedUse | None = None


@dataclass(frozen=True, slots=True)
class IndependentSourceRightsStatus:
    """A source-local status that cannot be satisfied by another source."""

    source_id: str
    status: SourceRightsStatus
    capabilities: tuple[CapabilityRightsStatus, ...]


@dataclass(frozen=True, slots=True)
class R1SourceRightsResult:
    """The fixed target and the two independently evaluated sources."""

    instrument_id: str
    sources: tuple[IndependentSourceRightsStatus, ...]


def normalize_r1_instrument_code(instrument_code: str) -> str:
    """Normalize the two permitted spellings and reject every other target."""

    if instrument_code.strip().upper() not in _R1_SYMBOL_ALIASES:
        raise ApplicationError(ErrorCode.PRECONDITION_FAILED, "R1_TARGET_LOCKED")
    return R1_INSTRUMENT_ID


class R1SourceRightsGate:
    """Evaluate every capability without connectors, fallback, or cursor state."""

    def __init__(self, rights_policy: RightsPolicyPort) -> None:
        self._rights_policy = rights_policy

    async def evaluate(
        self,
        *,
        context: OperationContext,
        instrument_code: str,
        rights_snapshot_hashes: Mapping[str, str],
    ) -> R1SourceRightsResult:
        """Return fail-closed status for CNINFO and Choice independently."""

        instrument_id = normalize_r1_instrument_code(instrument_code)
        sources: list[IndependentSourceRightsStatus] = []
        for source_id, capabilities in _R1_CAPABILITIES.items():
            snapshot_hash = rights_snapshot_hashes.get(source_id)
            if snapshot_hash is None:
                capability_statuses = tuple(
                    _blocked(capability, "RIGHTS_SNAPSHOT_MISSING") for capability in capabilities
                )
            else:
                evaluated_capabilities: list[CapabilityRightsStatus] = []
                for capability in capabilities:
                    evaluated_capabilities.append(
                        await self._evaluate_capability(
                            context=context,
                            source_id=source_id,
                            capability=capability,
                            rights_snapshot_hash=snapshot_hash,
                        )
                    )
                capability_statuses = tuple(evaluated_capabilities)
            source_status = (
                SourceRightsStatus.ELIGIBLE
                if all(
                    capability.status is SourceRightsStatus.ELIGIBLE
                    for capability in capability_statuses
                )
                else SourceRightsStatus.RIGHTS_BLOCKED
            )
            sources.append(
                IndependentSourceRightsStatus(
                    source_id=source_id,
                    status=source_status,
                    capabilities=capability_statuses,
                )
            )
        return R1SourceRightsResult(instrument_id=instrument_id, sources=tuple(sources))

    async def _evaluate_capability(
        self,
        *,
        context: OperationContext,
        source_id: str,
        capability: str,
        rights_snapshot_hash: str,
    ) -> CapabilityRightsStatus:
        for operation in _REQUIRED_OPERATIONS:
            for permitted_use in _REQUIRED_PERMITTED_USES:
                try:
                    check = RightsCheck(
                        context=context,
                        source_id=source_id,
                        operation=operation,
                        rights_snapshot_hash=rights_snapshot_hash,
                        content_category=capability,
                        permitted_use=permitted_use,
                    )
                except ValueError:
                    return _blocked(
                        capability,
                        "RIGHTS_SNAPSHOT_INVALID",
                        operation=operation,
                        permitted_use=permitted_use,
                    )
                try:
                    decision = await self._rights_policy.authorize(check)
                except TimeoutError:
                    return _blocked(
                        capability,
                        "RIGHTS_CHECK_TIMEOUT",
                        operation=operation,
                        permitted_use=permitted_use,
                    )
                except Exception:
                    return _blocked(
                        capability,
                        "RIGHTS_CHECK_UNAVAILABLE",
                        operation=operation,
                        permitted_use=permitted_use,
                    )
                invalid_reason = _invalid_decision_reason(
                    decision,
                    context=context,
                    expected_snapshot_hash=rights_snapshot_hash,
                )
                if invalid_reason is not None:
                    return _blocked(
                        capability,
                        invalid_reason,
                        operation=operation,
                        permitted_use=permitted_use,
                    )
                if not decision.allowed:
                    return _blocked(
                        capability,
                        decision.reason_code,
                        operation=operation,
                        permitted_use=permitted_use,
                    )

        return CapabilityRightsStatus(
            capability=capability,
            status=SourceRightsStatus.ELIGIBLE,
        )


def _invalid_decision_reason(
    decision: RightsDecision,
    *,
    context: OperationContext,
    expected_snapshot_hash: str,
) -> str | None:
    if decision.rights_snapshot_hash != expected_snapshot_hash:
        return "RIGHTS_SNAPSHOT_MISMATCH"
    if decision.decided_at > context.as_of:
        return "RIGHTS_DECISION_NOT_VISIBLE"
    if decision.retention_until is not None and decision.retention_until < context.as_of:
        return "RIGHTS_EXPIRED"
    return None


def _blocked(
    capability: str,
    reason_code: str,
    *,
    operation: RightsOperation | None = None,
    permitted_use: RightsPermittedUse | None = None,
) -> CapabilityRightsStatus:
    return CapabilityRightsStatus(
        capability=capability,
        status=SourceRightsStatus.RIGHTS_BLOCKED,
        reason_code=reason_code,
        failing_operation=operation,
        failing_permitted_use=permitted_use,
    )


__all__ = [
    "CHOICE_QUANT_API",
    "CNINFO_DATA_SERVICE",
    "CapabilityRightsStatus",
    "IndependentSourceRightsStatus",
    "R1SourceRightsGate",
    "R1SourceRightsResult",
    "SourceRightsStatus",
    "normalize_r1_instrument_code",
]
