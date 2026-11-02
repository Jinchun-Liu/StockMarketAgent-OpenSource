from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import UUID

import pytest

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import (
    RightsCheck,
    RightsDecision,
    RightsOperation,
    RightsPermittedUse,
)
from stock_assistant.modules.ingestion.source_rights import (
    CHOICE_QUANT_API,
    CNINFO_DATA_SERVICE,
    R1SourceRightsGate,
    SourceRightsStatus,
)

NOW = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
CNINFO_HASH = "a" * 64
CHOICE_HASH = "b" * 64
REQUIRED_OPERATIONS = (
    RightsOperation.FETCH,
    RightsOperation.RETAIN,
    RightsOperation.DERIVE,
)
REQUIRED_USES = tuple(RightsPermittedUse)
CAPABILITY_COUNTS = {CNINFO_DATA_SERVICE: 2, CHOICE_QUANT_API: 4}
CAPABILITIES = {
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


class PolicyUnavailableError(RuntimeError):
    pass


class FakeRightsPolicy:
    def __init__(
        self,
        outcome: Callable[[RightsCheck], RightsDecision] | None = None,
    ) -> None:
        self.outcome = outcome or _allowed_decision
        self.checks: list[RightsCheck] = []

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        self.checks.append(check)
        return self.outcome(check)


def _context() -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        request_id=UUID(int=1),
        correlation_id=UUID(int=2),
        actor_id="synthetic-test",
        as_of=NOW,
        requested_at=NOW - timedelta(seconds=1),
    )


def _allowed_decision(check: RightsCheck) -> RightsDecision:
    return RightsDecision(
        allowed=True,
        reason_code="RIGHTS_ALLOWED",
        decided_at=NOW,
        rights_snapshot_hash=check.rights_snapshot_hash,
        retention_until=NOW + timedelta(days=1),
    )


def _evaluate(
    policy: FakeRightsPolicy,
    *,
    instrument_code: str = "XSHG:603986",
    hashes: dict[str, str] | None = None,
) -> object:
    return R1SourceRightsGate(policy).evaluate(
        context=_context(),
        instrument_code=instrument_code,
        rights_snapshot_hashes=hashes
        or {
            CNINFO_DATA_SERVICE: CNINFO_HASH,
            CHOICE_QUANT_API: CHOICE_HASH,
        },
    )


@pytest.mark.asyncio
async def test_all_required_combinations_make_both_sources_eligible() -> None:
    policy = FakeRightsPolicy()

    result = await _evaluate(policy)

    assert result.instrument_id == "XSHG:603986"
    assert tuple(source.source_id for source in result.sources) == (
        CNINFO_DATA_SERVICE,
        CHOICE_QUANT_API,
    )
    assert {source.status for source in result.sources} == {SourceRightsStatus.ELIGIBLE}
    assert all(
        capability.status is SourceRightsStatus.ELIGIBLE
        and capability.reason_code is None
        and capability.failing_operation is None
        and capability.failing_permitted_use is None
        for source in result.sources
        for capability in source.capabilities
    )
    for source_id, capabilities in CAPABILITIES.items():
        checks = [check for check in policy.checks if check.source_id == source_id]
        assert [
            (check.content_category, check.operation, check.permitted_use) for check in checks
        ] == list(product(capabilities, REQUIRED_OPERATIONS, REQUIRED_USES))


@pytest.mark.asyncio
async def test_denial_reports_exact_operation_and_use_and_stops_that_capability() -> None:
    def deny_one(check: RightsCheck) -> RightsDecision:
        decision = _allowed_decision(check)
        if (
            check.source_id == CHOICE_QUANT_API
            and check.content_category == "market_snapshot"
            and check.operation is RightsOperation.RETAIN
            and check.permitted_use is RightsPermittedUse.AI_USE
        ):
            return decision.model_copy(
                update={"allowed": False, "reason_code": "RIGHTS_SCOPE_BLOCKED"}
            )
        return decision

    policy = FakeRightsPolicy(deny_one)

    result = await _evaluate(policy)

    by_source = {source.source_id: source for source in result.sources}
    assert by_source[CNINFO_DATA_SERVICE].status is SourceRightsStatus.ELIGIBLE
    assert by_source[CHOICE_QUANT_API].status is SourceRightsStatus.RIGHTS_BLOCKED
    failure = next(
        capability
        for capability in by_source[CHOICE_QUANT_API].capabilities
        if capability.capability == "market_snapshot"
    )
    assert failure.reason_code == "RIGHTS_SCOPE_BLOCKED"
    assert failure.failing_operation is RightsOperation.RETAIN
    assert failure.failing_permitted_use is RightsPermittedUse.AI_USE
    denied_index = next(
        index
        for index, check in enumerate(policy.checks)
        if (
            check.source_id == CHOICE_QUANT_API
            and check.content_category == "market_snapshot"
            and check.operation is RightsOperation.RETAIN
            and check.permitted_use is RightsPermittedUse.AI_USE
        )
    )
    assert policy.checks[denied_index + 1].content_category == "company_industry_news"
    assert len(policy.checks) == sum(CAPABILITY_COUNTS.values()) * 12 - 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "reason_code"),
    (
        (TimeoutError(), "RIGHTS_CHECK_TIMEOUT"),
        (PolicyUnavailableError(), "RIGHTS_CHECK_UNAVAILABLE"),
    ),
)
async def test_policy_failures_remain_distinct_and_do_not_replace_other_source(
    exception: Exception,
    reason_code: str,
) -> None:
    def fail_cninfo(check: RightsCheck) -> RightsDecision:
        if check.source_id == CNINFO_DATA_SERVICE:
            raise exception
        return _allowed_decision(check)

    policy = FakeRightsPolicy(fail_cninfo)
    result = await _evaluate(policy)
    by_source = {source.source_id: source for source in result.sources}

    assert by_source[CNINFO_DATA_SERVICE].status is SourceRightsStatus.RIGHTS_BLOCKED
    assert {item.reason_code for item in by_source[CNINFO_DATA_SERVICE].capabilities} == {
        reason_code
    }
    assert all(
        item.failing_operation is RightsOperation.FETCH
        and item.failing_permitted_use is RightsPermittedUse.DISPLAY
        for item in by_source[CNINFO_DATA_SERVICE].capabilities
    )
    assert by_source[CHOICE_QUANT_API].status is SourceRightsStatus.ELIGIBLE
    assert len(policy.checks) == CAPABILITY_COUNTS[CNINFO_DATA_SERVICE] + (
        CAPABILITY_COUNTS[CHOICE_QUANT_API] * 12
    )


@pytest.mark.asyncio
async def test_missing_snapshot_fails_without_policy_access_or_failure_dimensions() -> None:
    policy = FakeRightsPolicy()

    result = await _evaluate(policy, hashes={CNINFO_DATA_SERVICE: CNINFO_HASH})
    choice = next(source for source in result.sources if source.source_id == CHOICE_QUANT_API)

    assert choice.status is SourceRightsStatus.RIGHTS_BLOCKED
    assert {item.reason_code for item in choice.capabilities} == {"RIGHTS_SNAPSHOT_MISSING"}
    assert all(
        item.failing_operation is None and item.failing_permitted_use is None
        for item in choice.capabilities
    )
    assert all(check.source_id == CNINFO_DATA_SERVICE for check in policy.checks)


@pytest.mark.asyncio
async def test_invalid_snapshot_is_distinct_and_does_not_block_other_source() -> None:
    policy = FakeRightsPolicy()

    result = await _evaluate(
        policy,
        hashes={CNINFO_DATA_SERVICE: "not-a-hash", CHOICE_QUANT_API: CHOICE_HASH},
    )
    by_source = {source.source_id: source for source in result.sources}

    assert {item.reason_code for item in by_source[CNINFO_DATA_SERVICE].capabilities} == {
        "RIGHTS_SNAPSHOT_INVALID"
    }
    assert all(
        item.failing_operation is RightsOperation.FETCH
        and item.failing_permitted_use is RightsPermittedUse.DISPLAY
        for item in by_source[CNINFO_DATA_SERVICE].capabilities
    )
    assert by_source[CHOICE_QUANT_API].status is SourceRightsStatus.ELIGIBLE
    assert all(check.source_id == CHOICE_QUANT_API for check in policy.checks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision_update", "reason_code"),
    (
        ({"rights_snapshot_hash": "c" * 64}, "RIGHTS_SNAPSHOT_MISMATCH"),
        ({"decided_at": NOW + timedelta(seconds=1)}, "RIGHTS_DECISION_NOT_VISIBLE"),
        ({"retention_until": NOW - timedelta(seconds=1)}, "RIGHTS_EXPIRED"),
    ),
)
async def test_invalid_policy_decisions_remain_precisely_distinct(
    decision_update: dict[str, object],
    reason_code: str,
) -> None:
    def invalid_decision(check: RightsCheck) -> RightsDecision:
        return _allowed_decision(check).model_copy(update=decision_update)

    policy = FakeRightsPolicy(invalid_decision)
    result = await _evaluate(policy)

    assert {item.reason_code for source in result.sources for item in source.capabilities} == {
        reason_code
    }
    assert all(
        item.failing_operation is RightsOperation.FETCH
        and item.failing_permitted_use is RightsPermittedUse.DISPLAY
        for source in result.sources
        for item in source.capabilities
    )
    assert len(policy.checks) == sum(CAPABILITY_COUNTS.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("instrument_code", ("XSHG:000001", "603986", ""))
async def test_non_target_fails_before_policy_access(instrument_code: str) -> None:
    policy = FakeRightsPolicy()

    with pytest.raises(ApplicationError, match="R1_TARGET_LOCKED"):
        await _evaluate(policy, instrument_code=instrument_code)

    assert policy.checks == []
