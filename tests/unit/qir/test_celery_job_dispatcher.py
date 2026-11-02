from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.jobs import (
    JobCancelRequest,
    JobRequest,
    JobResultQuery,
    JobStatus,
)
from stock_assistant.contracts.research import (
    ResearchGenerateRequest,
    ResearchGenerateResult,
    ResearchSessionStatus,
)
from stock_assistant.platform.scheduling.celery_dispatcher import CeleryJobDispatcher

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
JOB_ID = UUID("30000000-0000-4000-8000-000000000001")
SESSION_ID = UUID("30000000-0000-4000-8000-000000000002")


def _mutation() -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        as_of=NOW,
        requested_at=NOW,
        idempotency_key="research-dispatch-key-0001",
    )


def _operation() -> OperationContext:
    return OperationContext(schema_version="1.0", as_of=NOW, requested_at=NOW)


class _AsyncResult:
    def __init__(self, state: str, result: object = None) -> None:
        self.state = state
        self.result = result


class _Control:
    def __init__(self) -> None:
        self.revocations: list[tuple[str, bool]] = []

    def revoke(self, job_id: str, *, terminate: bool) -> None:
        self.revocations.append((job_id, terminate))


class _CeleryApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, tuple[object, ...], str, str]] = []
        self.results: dict[str, _AsyncResult] = {}
        self.control = _Control()

    def send_task(
        self,
        name: str,
        *,
        args: tuple[object, ...],
        task_id: str,
        queue: str,
    ) -> None:
        self.sent.append((name, args, task_id, queue))

    def AsyncResult(self, job_id: str) -> _AsyncResult:
        return self.results[job_id]


def _request() -> JobRequest[ResearchGenerateRequest]:
    return JobRequest[ResearchGenerateRequest](
        schema_version="1.0",
        job_id=JOB_ID,
        job_name="stock_assistant.research.generate_report.v1",
        context=_mutation(),
        payload=ResearchGenerateRequest(
            schema_version="1.0",
            context=_mutation(),
            session_id=SESSION_ID,
            job_id=JOB_ID,
        ),
        queued_at=NOW,
    )


@pytest.mark.asyncio
async def test_dispatcher_enqueues_and_cancels_without_leaking_celery_types() -> None:
    app = _CeleryApp()
    dispatcher = CeleryJobDispatcher[ResearchGenerateRequest, ResearchGenerateResult](
        app,
        result_model=ResearchGenerateResult,
        queue_name="research",
    )

    accepted = await dispatcher.enqueue(_request())
    cancelled = await dispatcher.cancel(
        JobCancelRequest(
            schema_version="1.0",
            context=_mutation(),
            job_id=JOB_ID,
        )
    )

    assert accepted.status is JobStatus.QUEUED
    assert app.sent[0][0] == "stock_assistant.research.generate_report.v1"
    assert app.sent[0][2:] == (str(JOB_ID), "research")
    assert cancelled.status is JobStatus.CANCELLED
    assert app.control.revocations == [(str(JOB_ID), False)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("celery_state", "expected_status"),
    [("STARTED", JobStatus.RUNNING), ("UNRECOGNIZED", JobStatus.FAILED)],
)
async def test_dispatcher_maps_running_and_failure_states(
    celery_state: str,
    expected_status: JobStatus,
) -> None:
    app = _CeleryApp()
    app.results[str(JOB_ID)] = _AsyncResult(celery_state)
    dispatcher = CeleryJobDispatcher[ResearchGenerateRequest, ResearchGenerateResult](
        app,
        result_model=ResearchGenerateResult,
    )

    lookup = await dispatcher.get_result(
        JobResultQuery(schema_version="1.0", context=_operation(), job_id=JOB_ID)
    )

    assert lookup.job is not None
    assert lookup.job.status is expected_status
    assert (lookup.job.failure is not None) is (expected_status is JobStatus.FAILED)


@pytest.mark.asyncio
async def test_dispatcher_validates_a_success_payload_into_the_result_contract() -> None:
    app = _CeleryApp()
    payload: dict[str, Any] = ResearchGenerateResult(
        schema_version="1.0",
        session_id=SESSION_ID,
        job_id=JOB_ID,
        status=ResearchSessionStatus.SUCCEEDED,
        report_id=UUID("30000000-0000-4000-8000-000000000003"),
        completed_at=NOW,
    ).model_dump(mode="json")
    app.results[str(JOB_ID)] = _AsyncResult("SUCCESS", payload)
    dispatcher = CeleryJobDispatcher[ResearchGenerateRequest, ResearchGenerateResult](
        app,
        result_model=ResearchGenerateResult,
    )

    lookup = await dispatcher.get_result(
        JobResultQuery(schema_version="1.0", context=_operation(), job_id=JOB_ID)
    )

    assert lookup.job is not None
    assert lookup.job.status is JobStatus.SUCCEEDED
    assert lookup.job.result is not None
    assert lookup.job.result.session_id == SESSION_ID
