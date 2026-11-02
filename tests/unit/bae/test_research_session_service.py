from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.research import (
    ResearchAnalysisConfig,
    ResearchCutoffMode,
    ResearchSessionAccepted,
    ResearchSessionCreateBody,
    ResearchSessionCreateRequest,
    ResearchSessionStatus,
    ResearchSourceMode,
)
from stock_assistant.contracts.research_skills import (
    ResearchSkillId,
    ResearchSkillMode,
    ResearchSkillSelection,
)
from stock_assistant.modules.research.service import ResearchSessionService
from stock_assistant.platform.instruments.synthetic_master import (
    SyntheticAshareInstrumentMasterData,
)

NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
REQUEST_ID = UUID("20000000-0000-4000-8000-000000000001")
CORRELATION_ID = UUID("20000000-0000-4000-8000-000000000002")


class _CaptureRepository:
    def __init__(self) -> None:
        self.requests: list[ResearchSessionCreateRequest] = []

    async def create(self, request: ResearchSessionCreateRequest) -> ResearchSessionAccepted:
        self.requests.append(request)
        return ResearchSessionAccepted(
            schema_version="1.0",
            session_id=request.session_id,
            job_id=request.job_id,
            status=ResearchSessionStatus.QUEUED,
            accepted_at=request.context.requested_at,
        )


@pytest.mark.asyncio
async def test_create_resolves_identity_and_hashes_only_the_client_body() -> None:
    repository = _CaptureRepository()
    service = ResearchSessionService(SyntheticAshareInstrumentMasterData(), repository)  # type: ignore[arg-type]
    body = ResearchSessionCreateBody(schema_version="1.0", instrument_id="XSHG:603986")

    first = await service.create(
        body=body,
        idempotency_key="research-session-key-0001",
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        now=NOW,
    )
    second = await service.create(
        body=body,
        idempotency_key="research-session-key-0001",
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        now=NOW + timedelta(seconds=1),
    )

    assert first.status is ResearchSessionStatus.QUEUED
    assert second.status is ResearchSessionStatus.QUEUED
    assert repository.requests[0].request_sha256 == repository.requests[1].request_sha256
    assert repository.requests[0].context.as_of == NOW
    assert repository.requests[1].context.as_of == NOW + timedelta(seconds=1)
    assert repository.requests[0].cutoff_mode is ResearchCutoffMode.CURRENT_RUN
    assert repository.requests[1].cutoff_mode is ResearchCutoffMode.CURRENT_RUN
    assert repository.requests[0].instrument_name == "兆易创新"
    assert repository.requests[0].source_mode is ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY


@pytest.mark.asyncio
async def test_explicit_cutoff_is_persisted_as_historical_replay() -> None:
    repository = _CaptureRepository()
    service = ResearchSessionService(SyntheticAshareInstrumentMasterData(), repository)  # type: ignore[arg-type]
    cutoff = NOW - timedelta(seconds=1)

    await service.create(
        body=ResearchSessionCreateBody(
            schema_version="1.0",
            instrument_id="XSHG:603986",
            as_of=cutoff,
        ),
        idempotency_key="research-session-key-historical-0001",
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        now=NOW,
    )

    assert repository.requests[0].context.as_of == cutoff
    assert repository.requests[0].cutoff_mode is ResearchCutoffMode.HISTORICAL_REPLAY


@pytest.mark.asyncio
async def test_create_rejects_cutoff_before_the_directory_was_observed() -> None:
    repository = _CaptureRepository()
    service = ResearchSessionService(SyntheticAshareInstrumentMasterData(), repository)  # type: ignore[arg-type]
    cutoff = datetime(2025, 12, 31, 15, 0, tzinfo=UTC)

    with pytest.raises(ApplicationError, match="INSTRUMENT_NOT_FOUND_AT_CUTOFF"):
        await service.create(
            body=ResearchSessionCreateBody(
                schema_version="1.0",
                instrument_id="XSHG:603986",
                as_of=cutoff,
            ),
            idempotency_key="research-session-key-0002",
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            now=NOW,
        )

    assert repository.requests == []


@pytest.mark.asyncio
async def test_create_persists_manual_methods_and_rejects_boundary_blocked_methods() -> None:
    repository = _CaptureRepository()
    service = ResearchSessionService(SyntheticAshareInstrumentMasterData(), repository)  # type: ignore[arg-type]
    selection = ResearchSkillSelection(
        schema_version="1.0",
        mode=ResearchSkillMode.MANUAL,
        selected_skill_ids=[ResearchSkillId.INDUSTRY_RESEARCH],
    )

    await service.create(
        body=ResearchSessionCreateBody(
            schema_version="1.0",
            instrument_id="XSHG:603986",
            skill_selection=selection,
        ),
        idempotency_key="research-session-key-methods-0001",
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        now=NOW,
    )

    assert repository.requests[0].analysis_config.schema_version == "1.0"
    assert repository.requests[0].analysis_config.skill_selection == selection
    with pytest.raises(ApplicationError, match="RESEARCH_SKILL_NOT_AVAILABLE"):
        await service.create(
            body=ResearchSessionCreateBody(
                schema_version="1.0",
                instrument_id="XSHG:603986",
                skill_selection=ResearchSkillSelection(
                    schema_version="1.0",
                    mode=ResearchSkillMode.MANUAL,
                    selected_skill_ids=[ResearchSkillId.PORTFOLIO_REVIEW],
                ),
            ),
            idempotency_key="research-session-key-methods-0002",
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_create_rejects_conflicting_method_inputs_and_canonicalizes_equal_inputs() -> None:
    repository = _CaptureRepository()
    service = ResearchSessionService(SyntheticAshareInstrumentMasterData(), repository)  # type: ignore[arg-type]
    selection = ResearchSkillSelection(
        schema_version="1.0",
        mode=ResearchSkillMode.MANUAL,
        selected_skill_ids=[ResearchSkillId.INDUSTRY_RESEARCH],
    )
    top_level_only = ResearchSessionCreateBody(
        schema_version="1.0",
        instrument_id="XSHG:603986",
        skill_selection=selection,
    )
    duplicated_equal = ResearchSessionCreateBody(
        schema_version="1.0",
        instrument_id="XSHG:603986",
        analysis_config=ResearchAnalysisConfig(
            schema_version="1.0",
            skill_selection=selection,
        ),
        skill_selection=selection,
    )

    await service.create(
        body=top_level_only,
        idempotency_key="research-session-key-method-canonical-0001",
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        now=NOW,
    )
    await service.create(
        body=duplicated_equal,
        idempotency_key="research-session-key-method-canonical-0002",
        request_id=REQUEST_ID,
        correlation_id=CORRELATION_ID,
        now=NOW,
    )

    assert repository.requests[-2].request_sha256 == repository.requests[-1].request_sha256
    with pytest.raises(ApplicationError, match="RESEARCH_SKILL_SELECTION_CONFLICT"):
        await service.create(
            body=ResearchSessionCreateBody(
                schema_version="1.0",
                instrument_id="XSHG:603986",
                analysis_config=ResearchAnalysisConfig(
                    schema_version="1.0",
                    skill_selection=ResearchSkillSelection(schema_version="1.0"),
                ),
                skill_selection=selection,
            ),
            idempotency_key="research-session-key-method-conflict-0001",
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            now=NOW,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_code", "expected_message"),
    [
        (
            ResearchSessionCreateBody(
                schema_version="1.0",
                instrument_id="XSHG:603986",
                as_of=NOW + timedelta(seconds=1),
            ),
            ErrorCode.VALIDATION_ERROR,
            "RESEARCH_AS_OF_IN_FUTURE",
        ),
        (
            ResearchSessionCreateBody(
                schema_version="1.0",
                instrument_id="XSHG:999999",
            ),
            ErrorCode.NOT_FOUND,
            "INSTRUMENT_NOT_FOUND_AT_CUTOFF",
        ),
    ],
)
async def test_create_fails_closed_for_invalid_cutoff_or_unknown_instrument(
    body: ResearchSessionCreateBody,
    expected_code: ErrorCode,
    expected_message: str,
) -> None:
    repository = _CaptureRepository()
    service = ResearchSessionService(
        SyntheticAshareInstrumentMasterData(),
        repository,  # type: ignore[arg-type]
    )

    with pytest.raises(ApplicationError, match=expected_message) as raised:
        await service.create(
            body=body,
            idempotency_key="research-session-key-0003",
            request_id=REQUEST_ID,
            correlation_id=CORRELATION_ID,
            now=NOW,
        )

    assert raised.value.code is expected_code
    assert repository.requests == []
