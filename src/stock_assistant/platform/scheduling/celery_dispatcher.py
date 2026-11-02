"""Celery implementation of the provider-neutral JobDispatcherPort."""

from __future__ import annotations

import asyncio

from celery import Celery

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.jobs import (
    JobAccepted,
    JobCancelRequest,
    JobCancelResult,
    JobFailure,
    JobRequest,
    JobResult,
    JobResultLookup,
    JobResultQuery,
    JobStatus,
)

_STATE_MAP = {
    "PENDING": JobStatus.QUEUED,
    "RECEIVED": JobStatus.QUEUED,
    "STARTED": JobStatus.RUNNING,
    "RETRY": JobStatus.RUNNING,
    "SUCCESS": JobStatus.SUCCEEDED,
    "FAILURE": JobStatus.FAILED,
    "REVOKED": JobStatus.CANCELLED,
}


class CeleryJobDispatcher[PayloadT: ContractModel, ResultT: ContractModel]:
    """Bound Celery adapter; vendor handles never cross the port boundary."""

    def __init__(
        self,
        app: Celery,
        *,
        result_model: type[ResultT],
        queue_name: str = "celery",
    ) -> None:
        self._app = app
        self._result_model = result_model
        self._queue_name = queue_name

    async def enqueue(self, request: JobRequest[PayloadT]) -> JobAccepted:
        await asyncio.to_thread(
            self._app.send_task,
            request.job_name,
            args=(request.payload.model_dump(mode="json"),),
            task_id=str(request.job_id),
            queue=self._queue_name,
        )
        return JobAccepted(
            schema_version="1.0",
            job_id=request.job_id,
            accepted_at=request.queued_at,
            queue_name=self._queue_name,
        )

    async def get_result(self, request: JobResultQuery) -> JobResultLookup[ResultT]:
        result = self._app.AsyncResult(str(request.job_id))
        state = _STATE_MAP.get(result.state, JobStatus.FAILED)
        completed_at = (
            request.context.requested_at
            if state
            in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }
            else None
        )
        payload = (
            self._result_model.model_validate(result.result)
            if state is JobStatus.SUCCEEDED
            else None
        )
        failure = (
            JobFailure(
                reason_code="JOB_EXECUTION_FAILED",
                safe_message="The background job failed.",
                retryable=False,
                failed_at=request.context.requested_at,
            )
            if state is JobStatus.FAILED
            else None
        )
        return JobResultLookup(
            schema_version="1.0",
            job_id=request.job_id,
            found=True,
            job=JobResult(
                schema_version="1.0",
                job_id=request.job_id,
                status=state,
                completed_at=completed_at,
                result=payload,
                failure=failure,
                attempt=1,
            ),
            as_of=request.context.as_of,
        )

    async def cancel(self, request: JobCancelRequest) -> JobCancelResult:
        await asyncio.to_thread(
            self._app.control.revoke,
            str(request.job_id),
            terminate=False,
        )
        return JobCancelResult(
            schema_version="1.0",
            job_id=request.job_id,
            cancelled=True,
            status=JobStatus.CANCELLED,
            processed_at=request.context.requested_at,
        )
