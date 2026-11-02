"""Persistence boundary for on-demand research sessions and reports."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from stock_assistant.contracts.research import (
    ResearchCheckpointLookupResult,
    ResearchCheckpointWriteRequest,
    ResearchReportLookupResult,
    ResearchReportWriteRequest,
    ResearchSessionAccepted,
    ResearchSessionCreateRequest,
    ResearchSessionListQuery,
    ResearchSessionLookupRequest,
    ResearchSessionLookupResult,
    ResearchSessionMutationRequest,
    ResearchSessionMutationResult,
    ResearchSessionPage,
    ResearchStageTransitionRequest,
)


@runtime_checkable
class ResearchRepositoryPort(Protocol):
    async def save_checkpoint(
        self, request: ResearchCheckpointWriteRequest
    ) -> ResearchCheckpointLookupResult: ...

    async def get_checkpoint(
        self, request: ResearchSessionLookupRequest
    ) -> ResearchCheckpointLookupResult: ...

    async def create(self, request: ResearchSessionCreateRequest) -> ResearchSessionAccepted: ...

    async def get(self, request: ResearchSessionLookupRequest) -> ResearchSessionLookupResult: ...

    async def list_page(self, query: ResearchSessionListQuery) -> ResearchSessionPage: ...

    async def transition(
        self, request: ResearchStageTransitionRequest
    ) -> ResearchSessionLookupResult: ...

    async def save_report(
        self, request: ResearchReportWriteRequest
    ) -> ResearchReportLookupResult: ...

    async def get_report(
        self, request: ResearchSessionLookupRequest
    ) -> ResearchReportLookupResult: ...

    async def cancel(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult: ...

    async def retry(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult: ...

    async def delete(
        self, request: ResearchSessionMutationRequest
    ) -> ResearchSessionMutationResult: ...

    async def mark_stalled(self, request: ResearchSessionMutationRequest) -> int: ...

    async def mark_stalled_due(self, *, now: datetime) -> int: ...

    async def purge_deleted(self, request: ResearchSessionMutationRequest) -> int: ...
