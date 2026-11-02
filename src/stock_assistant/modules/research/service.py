"""Use cases for creating and controlling independent research sessions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.instruments import InstrumentLookupRequest, ResolutionStatus
from stock_assistant.contracts.research import (
    ResearchCutoffMode,
    ResearchExecutionSourceMode,
    ResearchSessionAccepted,
    ResearchSessionCreateBody,
    ResearchSessionCreateRequest,
    ResearchSourceMode,
)
from stock_assistant.modules.research.skills import resolve_skill_selection
from stock_assistant.ports.instruments import InstrumentMasterDataPort
from stock_assistant.ports.research import ResearchRepositoryPort


class ResearchSessionService:
    """Resolve identity at the cutoff and create a session without touching watchlists."""

    def __init__(
        self,
        instruments: InstrumentMasterDataPort,
        repository: ResearchRepositoryPort,
        source_mode: ResearchExecutionSourceMode = ResearchSourceMode.SYNTHETIC_DEVELOPMENT_ONLY,
    ) -> None:
        self._instruments = instruments
        self._repository = repository
        if source_mode is ResearchSourceMode.PRODUCTION_AUTHORIZED:
            raise ValueError("production research is not enabled")
        self._source_mode = source_mode

    async def create(
        self,
        *,
        body: ResearchSessionCreateBody,
        idempotency_key: str,
        request_id: UUID,
        correlation_id: UUID,
        now: datetime,
        actor_id: str = "local-research-user",
        workspace_id: str = "on-demand-research",
    ) -> ResearchSessionAccepted:
        cutoff = body.as_of or now
        cutoff_mode = (
            ResearchCutoffMode.CURRENT_RUN
            if body.as_of is None
            else ResearchCutoffMode.HISTORICAL_REPLAY
        )
        if cutoff > now:
            raise ApplicationError(ErrorCode.VALIDATION_ERROR, "RESEARCH_AS_OF_IN_FUTURE")
        nested_selection = body.analysis_config.skill_selection
        if nested_selection is not None and nested_selection != body.skill_selection:
            raise ApplicationError(
                ErrorCode.VALIDATION_ERROR,
                "RESEARCH_SKILL_SELECTION_CONFLICT",
            )
        resolve_skill_selection(
            instrument_id=body.instrument_id,
            selection=body.skill_selection,
        )
        operation = OperationContext(
            schema_version="1.0",
            request_id=request_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            workspace_id=workspace_id,
            as_of=cutoff,
            requested_at=now,
        )
        resolution = await self._instruments.resolve(
            InstrumentLookupRequest(
                schema_version="1.0",
                context=operation,
                instrument_id=body.instrument_id,
            )
        )
        if resolution.status is not ResolutionStatus.RESOLVED:
            raise ApplicationError(ErrorCode.NOT_FOUND, "INSTRUMENT_NOT_FOUND_AT_CUTOFF")
        profile = resolution.matches[0]
        mutation = MutationContext.model_validate(
            {
                **operation.model_dump(mode="python"),
                "idempotency_key": idempotency_key,
            }
        )
        analysis_config = body.analysis_config.model_copy(
            update={
                "schema_version": "1.0",
                "skill_selection": body.skill_selection,
            }
        )
        canonical_body = body.model_copy(update={"analysis_config": analysis_config})
        request_sha256 = hashlib.sha256(
            json.dumps(
                canonical_body.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return await self._repository.create(
            ResearchSessionCreateRequest(
                schema_version="1.0",
                context=mutation,
                session_id=uuid4(),
                job_id=uuid4(),
                instrument_id=profile.instrument.instrument_id,
                instrument_name=profile.display_name,
                request_sha256=request_sha256,
                analysis_config=analysis_config,
                source_mode=self._source_mode,
                cutoff_mode=cutoff_mode,
            )
        )
