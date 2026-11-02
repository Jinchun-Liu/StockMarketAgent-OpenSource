"""Ports for task dispatch and recurring schedules."""

from typing import Protocol, TypeVar, runtime_checkable

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import CursorPage
from stock_assistant.contracts.jobs import (
    JobAccepted,
    JobCancelRequest,
    JobCancelResult,
    JobRequest,
    JobResultLookup,
    JobResultQuery,
    ScheduleCreateRequest,
    ScheduleDeleteRequest,
    ScheduleDeleteResult,
    ScheduledJob,
    ScheduleLookup,
    ScheduleLookupResult,
    ScheduleQuery,
    ScheduleUpdateRequest,
)

JobPayloadT = TypeVar("JobPayloadT", bound=ContractModel)
JobResultT = TypeVar("JobResultT", bound=ContractModel)


@runtime_checkable
class JobDispatcherPort(Protocol[JobPayloadT, JobResultT]):
    async def enqueue(self, request: JobRequest[JobPayloadT]) -> JobAccepted: ...

    async def get_result(self, request: JobResultQuery) -> JobResultLookup[JobResultT]: ...

    async def cancel(self, request: JobCancelRequest) -> JobCancelResult: ...


@runtime_checkable
class SchedulerPort(Protocol[JobPayloadT]):
    async def create(
        self, request: ScheduleCreateRequest[JobPayloadT]
    ) -> ScheduledJob[JobPayloadT]: ...

    async def update(
        self, request: ScheduleUpdateRequest[JobPayloadT]
    ) -> ScheduledJob[JobPayloadT]: ...

    async def delete(self, request: ScheduleDeleteRequest) -> ScheduleDeleteResult: ...

    async def get(self, request: ScheduleLookup) -> ScheduleLookupResult[JobPayloadT]: ...

    async def list_page(self, request: ScheduleQuery) -> CursorPage[ScheduledJob[JobPayloadT]]: ...
